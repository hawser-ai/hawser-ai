"""Conformance scenarios (plan WP5.8)."""

from __future__ import annotations

import json
import traceback
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any, Protocol

import yaml

from hawser.config import HawserConfig, RecordMode
from hawser.models import (
    Event,
    EventType,
    PathInput,
    Policy,
    Revision,
    RunRequest,
    RunResult,
    RunStatus,
    RunSummary,
    Step,
)
from hawser.models.review import Review
from hawser.runtime.base import RunHandle

INSTRUCTION = "Extract the invoice."


class ReviewLike(Protocol):
    def get(self, run_id: str) -> Review | None: ...
    def open(self, run_id: str, *, assigned_to: str | None = None) -> Review: ...
    def edit(
        self,
        run_id: str,
        changes: dict[str, Any],
        *,
        reviewer: str = ...,
        comment: str | None = None,
        expected_version: int | None = None,
    ) -> Review: ...
    def approve(
        self,
        run_id: str,
        *,
        reviewer: str = ...,
        comment: str | None = None,
        expected_version: int | None = None,
    ) -> Revision: ...
    def current_result(self, run_id: str) -> Any: ...
    def revisions(self, run_id: str) -> list[Revision]: ...


class Target(Protocol):
    @property
    def reviews(self) -> ReviewLike: ...

    def submit(self, request: RunRequest) -> RunHandle: ...
    def get(self, run_id: str) -> RunResult | None: ...
    def wait(self, run_id: str, *, timeout: float | None = None) -> RunResult: ...
    def events(self, run_id: str, *, after_seq: int = 0) -> Iterator[Event]: ...
    def steps(self, run_id: str) -> list[Step]: ...
    def list(self, *, limit: int = 50) -> list[RunSummary]: ...
    def cancel(self, run_id: str) -> bool: ...


@dataclass
class ScenarioResult:
    name: str
    passed: bool
    detail: str = ""


# -- fixtures --------------------------------------------------------------------------------


def quickstart_dir() -> Path:
    """The bundled quickstart directory (invoice, schema, policy, cassette)."""
    return _quickstart()


def _quickstart() -> Path:
    base = resources.files("hawser._quickstart")
    with resources.as_file(base) as path:
        return Path(path)


def quickstart_cassette_dir() -> Path:
    return _quickstart() / "cassette"


def quickstart_request(
    *, policy: dict[str, Any] | None = None, idempotency_key: str | None = None
) -> RunRequest:
    qs = _quickstart()
    schema = json.loads((qs / "invoice.schema.json").read_text(encoding="utf-8"))
    base = yaml.safe_load((qs / "policy.yaml").read_text(encoding="utf-8")) or {}
    merged = _deep_merge(base, policy or {})
    return RunRequest(
        instruction=INSTRUCTION,
        inputs=[PathInput(path=str(qs / "invoice.pdf"), name="invoice.pdf")],
        output_schema=schema,
        policy=Policy.model_validate(merged),
        idempotency_key=idempotency_key,
    )


def _deep_merge(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    out = dict(a)
    for k, v in b.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)  # type: ignore[arg-type]
        else:
            out[k] = v
    return out


def local_target(home: Path) -> Target:
    """A ``LocalRuntime`` configured to replay the quickstart recording."""
    from hawser.runtime.local import LocalRuntime  # noqa: PLC0415 - avoid import cycle

    cfg = HawserConfig(
        home=home, record_mode=RecordMode.replay, cassette_dir=quickstart_cassette_dir()
    )
    return LocalRuntime(cfg)


# -- scenarios -------------------------------------------------------------------------------


def _run(target: Target, request: RunRequest, timeout: float = 120.0) -> RunResult:
    return target.submit(request).result(timeout=timeout)


def accepted_extraction(t: Target) -> None:
    result = _run(t, quickstart_request())
    assert result.status == RunStatus.accepted, result.error or result.status
    assert isinstance(result.result, dict) and result.result.get("invoice_number"), "no result"
    found = [p for p, m in result.field_meta.items() if m.status.value == "found"]
    anchored = [p for p in found if result.field_meta[p].evidence]
    assert found and len(anchored) / len(found) >= 0.9, "evidence coverage below 90%"
    assert result.usage.totals.model_calls >= 1
    assert result.revision_id is not None
    assert t.get(result.run_id) is not None


def idempotent_resubmit(t: Target) -> None:
    key = f"conf-{uuid.uuid4().hex[:12]}"
    first = _run(t, quickstart_request(idempotency_key=key))
    second = t.submit(quickstart_request(idempotency_key=key))
    assert second.run_id == first.run_id, "idempotency key did not return the original run"
    assert second.result().status == first.status


def budget_exceeded_routes_to_review(t: Target) -> None:
    result = _run(t, quickstart_request(policy={"limits": {"max_cost_usd": 0.000001}}))
    assert result.status == RunStatus.needs_review, result.status
    assert result.error is not None, "no error recorded"
    assert result.error.get("type") == "BudgetExceeded", result.error
    assert result.usage.totals.model_calls == 0, "a model was called despite the budget"


def review_flow(t: Target) -> None:
    result = _run(t, quickstart_request(policy={"limits": {"max_cost_usd": 0.000001}}))
    assert result.status == RunStatus.needs_review
    review = t.reviews.open(result.run_id, assigned_to="conformance")
    assert review.version == 1
    edited = t.reviews.edit(
        result.run_id,
        {"/invoice_number": "INV-REVIEWED"},
        reviewer="conformance",
        expected_version=1,
    )
    assert edited.version == 2
    assert t.reviews.current_result(result.run_id)["invoice_number"] == "INV-REVIEWED"
    try:
        t.reviews.edit(result.run_id, {"/total": 1}, expected_version=1)
    except Exception as exc:
        assert "review_conflict" in str(getattr(exc, "code", "")) or "conflict" in str(exc)
    else:
        raise AssertionError("stale version was accepted")
    revision = t.reviews.approve(result.run_id, reviewer="conformance", expected_version=2)
    assert revision.kind == "reviewed"
    view = t.get(result.run_id)
    assert view is not None and view.status == RunStatus.accepted
    assert view.revision_id == revision.id
    assert view.result is not None
    assert view.result["invoice_number"] == "INV-REVIEWED"
    kinds = [r.kind for r in t.reviews.revisions(result.run_id)]
    assert kinds == ["machine", "reviewed"], kinds


def events_are_ordered(t: Target) -> None:
    result = _run(t, quickstart_request())
    events = list(t.events(result.run_id))
    seqs = [e.seq for e in events]
    assert seqs == list(range(1, len(seqs) + 1)), "event seq must be contiguous from 1"
    assert events[0].type == EventType.run_created
    assert events[-1].type == EventType.result_assembled
    tail = list(t.events(result.run_id, after_seq=len(seqs) - 2))
    assert [e.seq for e in tail] == seqs[-2:]


def steps_are_checkpointed(t: Target) -> None:
    result = _run(t, quickstart_request())
    keys = {s.key for s in t.steps(result.run_id)}
    assert "admit" in keys and any(k.startswith("extract") for k in keys), keys
    assert all(s.status.value == "completed" for s in t.steps(result.run_id))


def listing_includes_run(t: Target) -> None:
    result = _run(t, quickstart_request())
    ids = [s.id for s in t.list(limit=200)]
    assert result.run_id in ids
    summary = next(s for s in t.list(limit=200) if s.id == result.run_id)
    assert summary.status == result.status


def cancel_finished_is_noop(t: Target) -> None:
    result = _run(t, quickstart_request())
    assert t.cancel(result.run_id) is False
    view = t.get(result.run_id)
    assert view is not None and view.status == result.status


SCENARIOS: dict[str, Callable[[Target], None]] = {
    "accepted_extraction": accepted_extraction,
    "idempotent_resubmit": idempotent_resubmit,
    "budget_exceeded_routes_to_review": budget_exceeded_routes_to_review,
    "review_flow": review_flow,
    "events_are_ordered": events_are_ordered,
    "steps_are_checkpointed": steps_are_checkpointed,
    "listing_includes_run": listing_includes_run,
    "cancel_finished_is_noop": cancel_finished_is_noop,
}


def run_all(target: Target, names: list[str] | None = None) -> list[ScenarioResult]:
    out: list[ScenarioResult] = []
    for name, fn in SCENARIOS.items():
        if names and name not in names:
            continue
        try:
            fn(target)
        except Exception as exc:
            out.append(
                ScenarioResult(
                    name, False, f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=4)}"
                )
            )
        else:
            out.append(ScenarioResult(name, True))
    return out


__all__ = [
    "SCENARIOS",
    "ScenarioResult",
    "Target",
    "local_target",
    "quickstart_cassette_dir",
    "quickstart_dir",
    "quickstart_request",
    "run_all",
]
