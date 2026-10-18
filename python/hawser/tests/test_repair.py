from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from hawser.client import Client
from hawser.gateway import ModelRequest
from hawser.models import EventType, Policy, RunStatus
from hawser.models.common import JsonSchema
from hawser.models.policy import RepairPolicy
from hawser.runtime.local import LocalRuntime
from hawser.structured.subset import merge_values, subschema, top_level_only
from hawser.testing import StaticAdapter, build_envelope
from hawser_synth import Invoice
from hawser_synth.invoice import GeneratedInvoice

RuntimeFactory = Callable[..., LocalRuntime]


def test_subschema_and_merge(invoice_schema: JsonSchema, invoice: GeneratedInvoice) -> None:
    assert top_level_only(["/vendor_name", "/total"])
    assert not top_level_only(["/line_items/0/sku"])
    sub = subschema(invoice_schema, ["/vendor_name", "/total"])
    assert set(sub["properties"]) == {"vendor_name", "total"}
    assert sub["required"] == ["vendor_name", "total"]
    base = invoice.truth.model_dump(mode="json")
    patch = {"vendor_name": "Fixed Vendor", "total": 1.0, "currency": "ZZZ"}
    merged = merge_values(base, patch, ["/vendor_name", "/total"])
    assert merged["vendor_name"] == "Fixed Vendor"
    assert merged["total"] == 1.0
    assert merged["currency"] == base["currency"]  # untouched
    assert base["vendor_name"] != "Fixed Vendor"  # base not mutated


def test_ask_field_repair_fixes_unknown_required(
    runtime_factory: RuntimeFactory,
    invoice_pdf: Path,
    invoice: GeneratedInvoice,
    invoice_schema: JsonSchema,
) -> None:
    truth = invoice.truth.model_dump(mode="json")
    first = build_envelope(
        invoice_schema, truth, source_texts=invoice.source_texts, unknown={"/vendor_name"}
    )
    calls: list[ModelRequest] = []

    def respond(req: ModelRequest) -> Any:
        calls.append(req)
        if len(calls) == 1:
            return first
        # second call: sub-schema with only vendor_name; answer from the truth
        assert req.output_schema is not None
        assert set(req.output_schema["properties"]) == {"vendor_name"}
        assert any(
            "previous attempt" in p.text.lower()
            for m in req.messages
            for p in m.parts
            if p.kind == "text"
        )
        return build_envelope(
            {
                "type": "object",
                "properties": {"vendor_name": invoice_schema["properties"]["vendor_name"]},
            },
            {"vendor_name": truth["vendor_name"]},
            source_texts=invoice.source_texts,
        )

    rt = runtime_factory(StaticAdapter(output_fn=respond))
    result = Client(rt).run("Extract the invoice.", inputs=[invoice_pdf], output_schema=Invoice)
    assert result.status == RunStatus.accepted
    assert result.parse_as(Invoice) == invoice.truth
    assert len(calls) == 2
    assert result.usage.totals.model_calls == 2
    assert result.usage.totals.repair_attempts == 1
    assert result.field_meta["/vendor_name"].evidence  # repaired field is anchored
    types = [e.type for e in rt.events(result.run_id)]
    assert EventType.repair_started in types
    assert EventType.repair_no_progress not in types
    keys = {s.key for s in rt.meta.list_steps(result.run_id)}
    assert "extract:repair1:model" in keys


def test_repair_no_progress_stops_and_routes_to_review(
    runtime_factory: RuntimeFactory,
    invoice_pdf: Path,
    invoice: GeneratedInvoice,
    invoice_schema: JsonSchema,
) -> None:
    env = build_envelope(
        invoice_schema,
        invoice.truth.model_dump(mode="json"),
        source_texts=invoice.source_texts,
        unknown={"/vendor_name"},
    )
    adapter = StaticAdapter(env)  # always the same answer -> no progress
    rt = runtime_factory(adapter)
    result = Client(rt).run(
        "x",
        inputs=[invoice_pdf],
        output_schema=Invoice,
        policy=Policy(repair=RepairPolicy(attempts=3)),
    )
    assert result.status == RunStatus.needs_review
    assert len(adapter.calls) == 2  # one attempt, then no-progress stop
    types = [e.type for e in rt.events(result.run_id)]
    assert EventType.repair_no_progress in types
    assert any(w.code == "repair_stopped" and w.details["attempts"] == 1 for w in result.warnings)


def test_repair_disabled_by_policy(
    runtime_factory: RuntimeFactory,
    invoice_pdf: Path,
    invoice: GeneratedInvoice,
    invoice_schema: JsonSchema,
) -> None:
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
        policy=Policy(repair=RepairPolicy(attempts=0)),
    )
    assert result.status == RunStatus.needs_review
    assert len(adapter.calls) == 1
