from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from hawser.client import Client
from hawser.models import EventType, FieldStatus, Policy, RunStatus, StepKind, StepStatus, Usage
from hawser.models.common import JsonSchema
from hawser.runtime.local import LocalRuntime
from hawser.runtime.steps import StepExecutor, StepHandle
from hawser.testing import StaticAdapter, build_envelope
from hawser_synth import Invoice
from hawser_synth.invoice import GeneratedInvoice

RuntimeFactory = Callable[..., LocalRuntime]


def test_direct_extraction_accepted(
    runtime_factory: RuntimeFactory,
    invoice_pdf: Path,
    invoice: GeneratedInvoice,
    truth_envelope: Any,
) -> None:
    rt: LocalRuntime = runtime_factory(StaticAdapter(truth_envelope))
    client = Client(rt)
    result = client.run("Extract the invoice.", inputs=[invoice_pdf], output_schema=Invoice)

    assert result.status == RunStatus.accepted
    assert result.parse_as(Invoice) == invoice.truth
    assert result.validation.has_errors is False

    # evidence coverage: every found leaf with a printed value is anchored
    found = [p for p, m in result.field_meta.items() if m.status == FieldStatus.found]
    anchored = [p for p in found if result.field_meta[p].evidence]
    assert len(anchored) / len(found) >= 0.9
    ev = result.evidence_for("/total")
    assert len(ev) == 1
    assert ev[0].kind == "document_region"
    assert ev[0].page == 1
    assert ev[0].bbox is not None
    assert ev[0].text == invoice.source_texts["/total"]

    # usage reconciles with the single model call
    assert result.usage.totals.model_calls == 1
    assert result.usage.totals.cost_usd == pytest.approx(
        sum(c.cost_usd for c in result.usage.model_calls)
    )
    assert result.usage.totals.pages_processed == 1
    assert result.usage.models_used == ["anthropic/claude-opus-5-5"]
    assert result.usage.totals.wall_time_ms >= 0

    # artifacts: original, document index, step outputs
    kinds = {rt.artifacts.get(a.artifact_id).meta.kind.value for a in result.artifacts}
    assert {"original", "index"} <= kinds

    # events tell the story in order
    types = [e.type for e in rt.events(result.run_id)]
    assert types[0] == EventType.run_created
    assert EventType.asset_admitted in types
    assert EventType.context_selected in types
    assert EventType.model_called in types
    assert EventType.evidence_anchored in types
    assert EventType.validation_completed in types
    assert types[-1] == EventType.result_assembled
    seqs = [e.seq for e in rt.events(result.run_id)]
    assert seqs == list(range(1, len(seqs) + 1))

    # steps are recorded
    steps = {s.key: s for s in rt.meta.list_steps(result.run_id)}
    assert {"admit", "extract:model", "extract:anchor"} <= set(steps)
    assert all(s.status == StepStatus.completed for s in steps.values())
    assert steps["extract:model"].kind == StepKind.model
    assert steps["extract:model"].usage.totals.model_calls == 1

    # the run is listable and retrievable
    assert rt.get(result.run_id) is not None
    summary = rt.list()[0]
    assert summary.id == result.run_id
    assert summary.status == RunStatus.accepted


def test_unknown_required_routes_to_review(
    runtime_factory: RuntimeFactory,
    invoice_pdf: Path,
    invoice: GeneratedInvoice,
    invoice_schema: JsonSchema,
) -> None:
    env = build_envelope(
        invoice_schema,
        invoice.truth.model_dump(mode="json"),
        source_texts=invoice.source_texts,
        unknown={"/vendor_name", "/purchase_order_number"},
    )
    rt = runtime_factory(StaticAdapter(env))
    client = Client(rt)
    result = client.run("Extract the invoice.", inputs=[invoice_pdf], output_schema=Invoice)
    assert result.status == RunStatus.needs_review
    rules = {i.rule_id for i in result.validation.issues if i.severity == "error"}
    assert "core.unknown_required" in rules
    assert "core.schema" in rules
    fields = {i.field for i in result.validation.issues if i.rule_id == "core.unknown_required"}
    assert fields == {"/vendor_name"}  # purchase_order_number is nullable, so unknown is fine
    assert any(w.code == "repair_stopped" for w in result.warnings)  # static adapter: no progress
    assert result.field_meta["/vendor_name"].issues

    rejected = client.run(
        "Extract the invoice.",
        inputs=[invoice_pdf],
        output_schema=Invoice,
        policy=Policy(review_on_failure=False),
    )
    assert rejected.status == RunStatus.rejected


def test_evidence_consistency_warning(
    runtime_factory: RuntimeFactory,
    invoice_pdf: Path,
    invoice: GeneratedInvoice,
    invoice_schema: JsonSchema,
) -> None:
    truth = invoice.truth.model_dump(mode="json")
    truth["total"] = truth["total"] + 1000  # value disagrees with the quoted text
    env = build_envelope(invoice_schema, truth, source_texts=invoice.source_texts)
    result = Client(runtime_factory(StaticAdapter(env))).run(
        "x", inputs=[invoice_pdf], output_schema=Invoice
    )
    assert result.status == RunStatus.accepted  # warnings never block
    warn = [i for i in result.validation.issues if i.rule_id == "core.evidence_consistent"]
    assert [i.field for i in warn] == ["/total"]


def test_no_schema_answer(runtime_factory: RuntimeFactory, invoice_pdf: Path) -> None:
    rt = runtime_factory(StaticAdapter(text="The total is on page 1."))
    result = Client(rt).run("What is the total?", inputs=[invoice_pdf])
    assert result.status == RunStatus.accepted
    assert result.result == "The total is on page 1."
    assert result.field_meta == {}


def test_scanned_pdf_sends_an_image(
    runtime_factory: RuntimeFactory,
    tmp_path: Path,
    scanned_invoice: GeneratedInvoice,
    invoice_schema: JsonSchema,
) -> None:
    p = tmp_path / "scan.pdf"
    p.write_bytes(scanned_invoice.pdf)
    env = build_envelope(
        invoice_schema,
        scanned_invoice.truth.model_dump(mode="json"),
        source_texts=scanned_invoice.source_texts,
    )
    adapter = StaticAdapter(env)
    rt = runtime_factory(adapter)
    result = Client(rt).run("Extract.", inputs=[p], output_schema=Invoice)
    # the model saw an image part
    parts = adapter.calls[0].messages[0].parts
    assert any(part.kind == "image" for part in parts)
    sel = next(e for e in rt.events(result.run_id) if e.type == EventType.context_selected)
    assert any(i["kind"] == "image" for i in sel.payload["included"])
    kinds = {rt.artifacts.get(a.artifact_id).meta.kind.value for a in result.artifacts}
    assert "rendered_page" in kinds
    assert result.status == RunStatus.accepted
    # Phase 7: the page without a text layer is OCR'd, so values are anchored to OCR boxes
    found = [m for m in result.field_meta.values() if m.status.value == "found"]
    anchored = [m for m in found if m.evidence]
    assert len(anchored) / len(found) >= 0.9
    ocr_calls = [
        e
        for e in rt.events(result.run_id)
        if e.type == EventType.tool_called and str(e.payload.get("tool", "")).startswith("ocr.")
    ]
    assert ocr_calls and ocr_calls[0].payload["page"] == 1
    assert "ocr" in kinds  # the OCR result is stored as an artifact


def test_admission_failures(runtime_factory: RuntimeFactory, tmp_path: Path) -> None:
    rt = runtime_factory(StaticAdapter({}))
    client = Client(rt)
    missing = client.run("x", inputs=[tmp_path / "nope.pdf"])
    assert missing.status == RunStatus.failed
    assert missing.error is not None and missing.error["code"] == "input_not_found"

    garbage = tmp_path / "blob.bin"
    garbage.write_bytes(b"\x00\x01\x02\xff" * 10)
    bad = client.run("x", inputs=[garbage])
    assert bad.status == RunStatus.failed
    assert bad.error is not None and bad.error["code"] == "unsupported_media_type"

    big = tmp_path / "big.txt"
    big.write_bytes(b"a" * 2048)
    small_rt = runtime_factory(StaticAdapter({}), max_bytes=1024)
    too_big = Client(small_rt).run("x", inputs=[big])
    assert too_big.status == RunStatus.failed
    assert too_big.error is not None and too_big.error["code"] == "input_too_large"

    types = [e.type for e in rt.events(missing.run_id)]
    assert EventType.step_failed in types
    assert types[-1] == EventType.result_assembled


def test_idempotency_key_returns_same_run(
    runtime_factory: RuntimeFactory, invoice_pdf: Path, truth_envelope: Any
) -> None:
    adapter = StaticAdapter(truth_envelope)
    client = Client(runtime_factory(adapter))
    a = client.run("x", inputs=[invoice_pdf], output_schema=Invoice, idempotency_key="k1")
    b = client.run("x", inputs=[invoice_pdf], output_schema=Invoice, idempotency_key="k1")
    assert a.run_id == b.run_id
    assert len(adapter.calls) == 1


def test_asset_dedup_across_runs(
    runtime_factory: RuntimeFactory, invoice_pdf: Path, truth_envelope: Any
) -> None:
    rt = runtime_factory(StaticAdapter(truth_envelope))
    client = Client(rt)
    a = client.run("x", inputs=[invoice_pdf], output_schema=Invoice)
    b = client.run("x", inputs=[invoice_pdf], output_schema=Invoice)
    admitted_b = next(e for e in rt.events(b.run_id) if e.type == EventType.asset_admitted)
    admitted_a = next(e for e in rt.events(a.run_id) if e.type == EventType.asset_admitted)
    assert admitted_b.payload["dedup"] is True
    assert admitted_b.payload["asset_id"] == admitted_a.payload["asset_id"]


def test_step_checkpoint_skips_completed_work(
    runtime_factory: RuntimeFactory, truth_envelope: Any
) -> None:
    rt: LocalRuntime = runtime_factory(StaticAdapter(truth_envelope))
    from hawser.clock import now
    from hawser.ids import new_id
    from hawser.models import RunRequest
    from hawser.runtime.events import EventEmitter
    from hawser.storage import RunRecord

    run = RunRecord(
        id=new_id("run"),
        tenant_id=rt.config.tenant_id,
        status=RunStatus.queued,
        request=RunRequest(instruction="x"),
        trace_id="t",
        created_at=now(),
        updated_at=now(),
    )
    rt.meta.put_run(run)
    emitter = EventEmitter(rt.meta, run_id=run.id, tenant_id=run.tenant_id)
    calls: list[int] = []

    from pydantic import BaseModel

    class Out(BaseModel):
        n: int

    def fn(h: StepHandle) -> Out:
        calls.append(1)
        return Out(n=42)

    usage = Usage()
    ex = StepExecutor(rt.meta, rt.artifacts, emitter, run_id=run.id, usage=usage)
    assert ex.run("s1", "step", StepKind.compute, fn, output_type=Out).n == 42
    ex2 = StepExecutor(rt.meta, rt.artifacts, emitter, run_id=run.id, usage=Usage())
    assert ex2.run("s1", "step", StepKind.compute, fn, output_type=Out).n == 42
    assert calls == [1]
    types = [e.type for e in rt.events(run.id)]
    assert types.count(EventType.step_started) == 1
    assert types.count(EventType.step_skipped) == 1
