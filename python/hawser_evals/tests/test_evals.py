from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from hawser.config import HawserConfig, RecordMode
from hawser.gateway import Gateway, ModelRequest, load_registry
from hawser.runtime.local import LocalRuntime
from hawser.structured import inline_refs, to_json_schema
from hawser.testing import StaticAdapter, build_envelope
from hawser_evals import check_thresholds, compare, load_dataset, run_eval, write_report
from hawser_evals.report import load_report, render_md
from hawser_evals.scoring import compare_documents
from hawser_synth.dataset import build
from hawser_synth.invoice import Invoice
from hawser_synth.purchase_order import PurchaseOrder, generate_pair

REPO = Path(__file__).resolve().parents[3]


class TruthAdapter(StaticAdapter):
    """Answers from the ground truth of whichever pair the request's page text belongs to."""

    def __init__(self, seeds: list[int], *, break_case: int | None = None) -> None:
        super().__init__(output_fn=self._respond)
        self.pairs = {s: generate_pair(s) for s in seeds}
        self.break_case = break_case

    def _respond(self, req: ModelRequest) -> Any:
        text = " ".join(p.text for m in req.messages for p in m.parts if p.kind == "text")
        assert req.output_schema is not None
        is_po = "po_number" in req.output_schema["properties"]
        for seed, pair in self.pairs.items():
            if (
                is_po
                and pair.purchase_order.truth.po_number in text
                and pair.purchase_order.truth.buyer_name in text
                and "Purchase Order" in text.replace("PURCHASE ORDER", "Purchase Order")
            ):
                schema = inline_refs(to_json_schema(PurchaseOrder))
                return build_envelope(
                    schema,
                    pair.purchase_order.truth.model_dump(mode="json"),
                    source_texts=pair.purchase_order.source_texts,
                )
            if not is_po and pair.invoice.truth.invoice_number in text:
                data = pair.invoice.truth.model_dump(mode="json")
                if seed == self.break_case:
                    data["total"] = data["total"] + 100  # planted regression
                schema = inline_refs(to_json_schema(Invoice))
                return build_envelope(schema, data, source_texts=pair.invoice.source_texts)
        raise AssertionError("request did not match any pair")


def _runtime(tmp_path: Path, adapter: StaticAdapter) -> LocalRuntime:
    cfg = HawserConfig(
        home=tmp_path / "home", record_mode=RecordMode.live, skill_paths=[REPO / "skills"]
    )
    return LocalRuntime(cfg, gateway=Gateway(load_registry(), {"anthropic": adapter}))


def test_compare_documents_alignment_and_outcomes() -> None:
    from hawser_evals.dataset import ScoringConfig

    cfg = ScoringConfig(list_keys={"/items": "sku"}, ignore=["/meta/*"], numeric_tolerance=0.01)
    expected = {
        "a": 1.0,
        "b": "Hello World",
        "c": None,
        "items": [{"sku": "X", "q": 1}, {"sku": "Y", "q": 2}],
        "meta": {"id": 1},
    }
    actual = {
        "a": 1.005,
        "b": "hello  world",
        "c": "oops",
        "items": [{"sku": "Y", "q": 2}, {"sku": "X", "q": 5}, {"sku": "Z", "q": 9}],
    }
    leaves = compare_documents(expected, actual, cfg)
    by = {leaf.pointer: leaf.outcome for leaf in leaves}
    assert by["/a"] == "correct"
    assert by["/b"] == "correct"
    assert by["/c"] == "hallucinated"
    assert by["/items/0/q"] == "mismatch"  # X aligned by sku, q differs
    assert by["/items/1/q"] == "correct"
    assert by["/items/2"] == "extra"
    assert "/meta/id" not in by


def test_eval_run_report_compare_gate(tmp_path: Path) -> None:
    ds_dir = build(tmp_path / "ds", n=4, seed=3000, scanned_fraction=0.0)
    ds = load_dataset(ds_dir)
    assert ds.skill == "invoice-po-reconciliation"
    seeds = [3000, 3001, 3002, 3003]

    good = run_eval(
        ds,
        _runtime(tmp_path / "a", TruthAdapter(seeds)),
        mode="workflow",
        model="anthropic/claude-opus-5-5",
    )
    assert good.metrics["cases"] == 4
    assert good.metrics["errors"] == 0
    assert good.metrics["field_accuracy"] == 1.0
    assert good.metrics["doc_pass_rate"] == 1.0
    assert good.metrics["status_accuracy"] == 1.0  # match -> accepted, discrepancy -> needs_review
    assert good.metrics["evidence_coverage"] > 0.9
    assert good.config.skill_version == "1.0.0"
    assert good.config.skill_hash and good.config.registry_version
    out = write_report(good, tmp_path / "report-a")
    assert (out / "report.json").exists()
    md = render_md(good)
    assert "Field accuracy | 100.0%" in md
    assert load_report(out).id == good.id

    bad = run_eval(
        ds,
        _runtime(tmp_path / "b", TruthAdapter(seeds, break_case=3001)),
        mode="workflow",
        model="anthropic/claude-opus-5-5",
    )
    assert bad.metrics["field_accuracy"] < 1.0
    assert any(c["field"] == "/invoice/total" and c["outcome"] == "mismatch" for c in bad.clusters)
    cmp = compare(good, bad)
    assert cmp["regressions"] == ["pair-03001"]
    assert cmp["metrics"]["field_accuracy"]["delta"] < 0

    assert (
        check_thresholds(good, {"field_accuracy": {"min": 0.95}, "review_rate": {"max": 1.0}}) == []
    )
    failures = check_thresholds(bad, {"field_accuracy": {"min": 0.999}, "nope": 1})
    assert len(failures) == 2

    budget = run_eval(
        ds,
        _runtime(tmp_path / "c", TruthAdapter(seeds)),
        mode="workflow",
        model="anthropic/claude-opus-5-5",
        budget_usd=0.0,
        limit=2,
    )
    # static adapter has zero cost, so the budget never trips; the limit does
    assert budget.metrics["cases"] == 2
    case = json.loads((ds_dir / "cases" / "pair-03000" / "expected.json").read_text())
    assert set(case) == {"invoice", "purchase_order", "reconciliation"}
