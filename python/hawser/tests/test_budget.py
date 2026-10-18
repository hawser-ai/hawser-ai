from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from hawser.client import Client
from hawser.errors import UsageMismatch
from hawser.gateway import Message, ModelRequest, TextPart, load_registry
from hawser.models import EventType, ModelCallUsage, Policy, RunStatus, Usage
from hawser.models.policy import Limits
from hawser.runtime.budget import estimate_call, reconcile
from hawser.runtime.local import LocalRuntime
from hawser.testing import StaticAdapter
from hawser_synth import Invoice

RuntimeFactory = Callable[..., LocalRuntime]


class PricedAdapter(StaticAdapter):
    """Reports real-looking token counts so registry pricing produces non-zero cost."""

    def complete(self, spec: Any, request: ModelRequest) -> Any:
        resp = super().complete(spec, request)
        resp.usage.input_tokens = 50_000
        resp.usage.output_tokens = 5_000
        return resp


def test_estimate_scales_with_input() -> None:
    reg = load_registry()
    spec = reg.get("anthropic/claude-opus-5-5")
    small = estimate_call(
        ModelRequest(model=spec.id, messages=[Message(role="user", parts=[TextPart(text="hi")])]),
        spec,
    )
    big = estimate_call(
        ModelRequest(
            model=spec.id, messages=[Message(role="user", parts=[TextPart(text="x" * 40_000)])]
        ),
        spec,
    )
    assert big.input_tokens > small.input_tokens
    assert big.cost_usd > small.cost_usd > 0


def test_cost_cap_blocks_the_call(
    runtime_factory: RuntimeFactory, invoice_pdf: Path, truth_envelope: Any
) -> None:
    adapter = StaticAdapter(truth_envelope)
    rt = runtime_factory(adapter)
    result = Client(rt).run(
        "x",
        inputs=[invoice_pdf],
        output_schema=Invoice,
        policy=Policy(limits=Limits(max_cost_usd=0.000001)),
    )
    assert (
        result.status == RunStatus.needs_review
    )  # review_on_failure routes budget stops to review
    assert result.error is not None and result.error["code"] == "max_cost_usd"
    assert adapter.calls == []  # never called the model
    rejected = Client(rt).run(
        "x",
        inputs=[invoice_pdf],
        output_schema=Invoice,
        policy=Policy(limits=Limits(max_cost_usd=0.000001), review_on_failure=False),
    )
    assert rejected.status == RunStatus.failed


def test_model_call_cap_counts_repairs(
    runtime_factory: RuntimeFactory, invoice_pdf: Path, invoice: Any, invoice_schema: Any
) -> None:
    from hawser.testing import build_envelope

    env = build_envelope(
        invoice_schema,
        invoice.truth.model_dump(mode="json"),
        source_texts=invoice.source_texts,
        unknown={"/vendor_name"},
    )
    adapter = StaticAdapter(env)
    result = Client(runtime_factory(adapter)).run(
        "x",
        inputs=[invoice_pdf],
        output_schema=Invoice,
        policy=Policy(limits=Limits(max_model_calls=1)),
    )
    # first call allowed; the repair attempt would be call 2 -> budget stops it, run goes to review
    assert len(adapter.calls) == 1
    assert result.status == RunStatus.needs_review
    assert result.error is not None and result.error["code"] == "max_model_calls"


def test_usage_reconciles_and_cost_is_priced(
    runtime_factory: RuntimeFactory, invoice_pdf: Path, truth_envelope: Any
) -> None:
    rt = runtime_factory(PricedAdapter(truth_envelope))
    result = Client(rt).run("x", inputs=[invoice_pdf], output_schema=Invoice)
    assert result.status == RunStatus.accepted
    expected = (50_000 * 4.0 + 5_000 * 20.0) / 1e6
    assert result.usage.totals.cost_usd == pytest.approx(expected)
    steps = rt.meta.list_steps(result.run_id)
    assert sum(c.cost_usd for s in steps for c in s.usage.model_calls) == pytest.approx(expected)
    reconcile(result.usage, steps)  # no exception
    events = [e for e in rt.events(result.run_id) if e.type == EventType.model_called]
    assert events[0].payload["cost_usd"] == pytest.approx(expected)


def test_reconcile_detects_mismatch() -> None:
    usage = Usage(
        model_calls=[
            ModelCallUsage(
                provider="p", model="m", input_tokens=1, output_tokens=1, cost_usd=0.5, latency_ms=1
            )
        ]
    )
    with pytest.raises(UsageMismatch):
        reconcile(usage, [])


def test_page_limit_from_policy(
    runtime_factory: RuntimeFactory, invoice_pdf: Path, truth_envelope: Any
) -> None:
    result = Client(runtime_factory(StaticAdapter(truth_envelope))).run(
        "x",
        inputs=[invoice_pdf],
        output_schema=Invoice,
        policy=Policy(limits=Limits(max_pages=0), review_on_failure=False),
    )
    assert result.status == RunStatus.failed
    assert result.error is not None and result.error["code"] == "max_pages"
