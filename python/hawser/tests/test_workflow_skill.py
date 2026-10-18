from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from hawser.client import Client
from hawser.config import HawserConfig, RecordMode
from hawser.gateway import Gateway, ModelRequest, load_registry
from hawser.models import EventType, FieldStatus, RunStatus
from hawser.runtime.local import LocalRuntime
from hawser.skills import SkillRegistry, load_bundle
from hawser.skills.lock import Lockfile
from hawser.testing import StaticAdapter, build_envelope
from hawser.tools.compute import align_line_items, check_arithmetic, reconcile
from hawser_synth.purchase_order import GeneratedPair, generate_pair

REPO = Path(__file__).resolve().parents[3]
SKILLS = REPO / "skills"


def pair_adapter(pair: GeneratedPair) -> StaticAdapter:
    """Answers invoice or PO extraction requests from the pair's ground truth."""

    def respond(req: ModelRequest) -> Any:
        assert req.output_schema is not None
        props = req.output_schema["properties"]
        if "po_number" in props:
            from hawser.structured import inline_refs, to_json_schema

            schema = inline_refs(to_json_schema(type(pair.purchase_order.truth)))
            return build_envelope(
                schema,
                pair.purchase_order.truth.model_dump(mode="json"),
                source_texts=pair.purchase_order.source_texts,
            )
        from hawser.structured import inline_refs, to_json_schema

        schema = inline_refs(to_json_schema(type(pair.invoice.truth)))
        return build_envelope(
            schema,
            pair.invoice.truth.model_dump(mode="json"),
            source_texts=pair.invoice.source_texts,
        )

    return StaticAdapter(output_fn=respond)


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    return tmp_path


def make_rt(tmp_path: Path, adapter: StaticAdapter) -> LocalRuntime:
    cfg = HawserConfig(home=tmp_path / "home", record_mode=RecordMode.live, skill_paths=[SKILLS])
    return LocalRuntime(cfg, gateway=Gateway(load_registry(), {"anthropic": adapter}))


def write_pair(tmp_path: Path, pair: GeneratedPair) -> list[Path]:
    inv = tmp_path / "invoice.pdf"
    po = tmp_path / "purchase_order.pdf"
    inv.write_bytes(pair.invoice.pdf)
    po.write_bytes(pair.purchase_order.pdf)
    return [inv, po]


def test_bundle_loads_and_locks() -> None:
    skill = load_bundle(SKILLS / "invoice-po-reconciliation")
    assert skill.name == "invoice-po-reconciliation"
    assert skill.run is not None
    assert "invoice_po.summary_consistent" in skill.validators
    assert set(skill.output_schema["properties"]) == {"invoice", "purchase_order", "reconciliation"}
    assert "$defs" not in skill.output_schema
    assert skill.prompt("extract_invoice")
    assert skill.bundle_hash.startswith("sha256:")
    reg = SkillRegistry([SKILLS])
    assert reg.resolve("invoice-po-reconciliation").version == "1.0.0"
    lock = Lockfile()
    d = lock.diff(skill)
    assert d["new"] and "document.extract" in d["tools_added"]
    lock.record(skill)
    assert lock.verify(skill) is None
    assert not lock.widens(skill)


def test_compute_tools_deterministic() -> None:
    pair = generate_pair(5, discrepancies=["qty_change", "extra_on_po"])
    inv = pair.invoice.truth.model_dump(mode="json")
    po = pair.purchase_order.truth.model_dump(mode="json")
    a = align_line_items(inv["line_items"], po["line_items"])
    assert len(a.matches) == len(inv["line_items"])  # every invoice item has a PO counterpart
    assert a.unmatched_right == [len(po["line_items"]) - 1]  # the extra PO item
    assert all(m.method == "sku" for m in a.matches)
    rec = reconcile(inv, po, a)
    assert rec.summary_status == "discrepancies"
    assert rec.discrepancy_count >= 2
    assert sum(1 for m in rec.matched_items if m.quantity_delta) == 1
    arith = check_arithmetic(
        inv["line_items"], subtotal=inv["subtotal"], tax=inv["tax"], total=inv["total"]
    )
    assert arith.line_total_mismatches == []
    assert arith.subtotal_ok and arith.total_ok


def test_workflow_match_is_accepted(tmp_path: Path) -> None:
    pair = generate_pair(11, discrepancies=[])
    rt = make_rt(tmp_path, pair_adapter(pair))
    result = Client(rt).run(skill="invoice-po-reconciliation", inputs=write_pair(tmp_path, pair))
    assert result.status == RunStatus.accepted, [i.message for i in result.validation.issues]
    assert result.mode is not None and result.mode.value == "workflow"
    assert result.result is not None
    rec = result.result["reconciliation"]
    assert rec["summary_status"] == "match"
    assert rec["discrepancy_count"] == 0
    # evidence from both extractions is attached under prefixes
    assert result.field_meta["/invoice/total"].evidence
    assert result.field_meta["/purchase_order/po_number"].evidence
    assert result.evidence_for("/invoice/total")[0].kind == "document_region"
    # derived field carries calculation evidence linking its inputs
    delta = result.field_meta["/reconciliation/total_delta"]
    assert delta.status == FieldStatus.derived
    calc = result.evidence_for("/reconciliation/total_delta")[0]
    assert calc.kind == "calculation"
    assert len(calc.inputs) == 2
    assert result.usage.totals.model_calls == 2
    keys = {s.key for s in rt.meta.list_steps(result.run_id)}
    assert {
        "admit",
        "extract:invoice:model",
        "extract:purchase_order:model",
        "align",
        "arithmetic",
        "reconcile",
        "workflow",
    } <= keys
    plan = next(
        e
        for e in rt.events(result.run_id)
        if e.type == EventType.plan_created and "skill" in e.payload
    )
    assert plan.payload["mode"] == "workflow"
    assert set(plan.payload["inputs"]) == {"invoice", "purchase_order"}


def test_workflow_discrepancy_needs_review(tmp_path: Path) -> None:
    pair = generate_pair(12, discrepancies=["price_change", "missing_on_po"])
    rt = make_rt(tmp_path, pair_adapter(pair))
    result = Client(rt).run(skill="invoice-po-reconciliation", inputs=write_pair(tmp_path, pair))
    assert result.status == RunStatus.needs_review
    rules = {i.rule_id for i in result.validation.issues}
    assert "rules.expressions.no_discrepancies" in rules
    assert "invoice_po.unmatched_invoice_item" in rules  # workflow-flagged
    assert result.result is not None
    assert result.result["reconciliation"]["summary_status"] == "discrepancies"


def test_workflow_input_binding_by_name_and_position(tmp_path: Path) -> None:
    pair = generate_pair(13, discrepancies=[])
    a = tmp_path / "scan-001.pdf"
    b = tmp_path / "scan-002.pdf"
    a.write_bytes(pair.invoice.pdf)
    b.write_bytes(pair.purchase_order.pdf)
    rt = make_rt(tmp_path, pair_adapter(pair))
    result = Client(rt).run(skill="invoice-po-reconciliation", inputs=[a, b])  # positional
    assert result.status == RunStatus.accepted
    missing = Client(rt).run(skill="invoice-po-reconciliation", inputs=[a])
    assert missing.status == RunStatus.failed
    assert missing.error is not None and missing.error["code"] == "input_missing"


def test_tool_permission_enforced(tmp_path: Path) -> None:
    pair = generate_pair(14, discrepancies=[])
    rt = make_rt(tmp_path, pair_adapter(pair))
    result = Client(rt).run(
        skill="invoice-po-reconciliation", inputs=write_pair(tmp_path, pair), tools=["compute.*"]
    )
    assert result.status == RunStatus.failed
    assert result.error is not None and result.error["code"] == "tool_not_allowed"
