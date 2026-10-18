from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from hawser.client import Client
from hawser.config import HawserConfig, RecordMode
from hawser.gateway import Gateway, ModelRequest, load_registry
from hawser.models import EventType, FieldStatus, Policy, RunStatus
from hawser.models.policy import Limits, PlanningPolicy
from hawser.plan import Plan, validate_plan
from hawser.plan.bindings import parse_binding, resolve
from hawser.runtime.local import LocalRuntime
from hawser.structured import inline_refs, to_json_schema
from hawser.testing import StaticAdapter, build_envelope
from hawser.tools import registry
from hawser_synth import Invoice
from hawser_synth.invoice import GeneratedInvoice
from hawser_synth.purchase_order import PurchaseOrder, generate_pair

REPO = Path(__file__).resolve().parents[3]
RuntimeFactory = Callable[..., LocalRuntime]
INVOICE_SCHEMA = inline_refs(to_json_schema(Invoice))


def plan_json(
    nodes: list[dict[str, Any]],
    *,
    goal: str = "Extract the invoice.",
    inputs: list[str] | None = None,
) -> dict[str, Any]:
    return {"goal": goal, "inputs": inputs or ["invoice"], "notes": "", "nodes": nodes}


EXTRACT_PLAN = plan_json(
    [
        {
            "id": "extract",
            "kind": "call_model",
            "instruction": "Extract the invoice.",
            "output_schema": INVOICE_SCHEMA,
            "context": {"inputs": ["invoice"], "pages": "all"},
        },
        {
            "id": "sum_ok",
            "kind": "compute",
            "expression": (
                "approx(sum(node.extract.line_items[:].line_total), node.extract.subtotal, 0.05)"
            ),
        },
        {
            "id": "gate",
            "kind": "branch",
            "condition": "node.sum_ok",
            "then": ["done"],
            "else": ["ask"],
        },
        {"id": "ask", "kind": "review", "reason": "line items do not add up"},
        {
            "id": "done",
            "kind": "exit",
            "status": "success",
            "result": {"invoice": "$node.extract", "sum_ok": "$node.sum_ok"},
        },
    ]
)


class AgentAdapter(StaticAdapter):
    """Answers planner calls with a canned plan and extraction calls from the ground truth."""

    def __init__(self, plans: list[Any], invoice: GeneratedInvoice, *, pair: Any = None) -> None:
        super().__init__(output_fn=self._respond)
        self.plans = list(plans)
        self.plan_calls = 0
        self.invoice = invoice
        self.pair = pair

    def _respond(self, req: ModelRequest) -> Any:
        if req.output_schema is None and "planner" in (req.system or "").lower():
            self.plan_calls += 1
            plan = self.plans.pop(0) if len(self.plans) > 1 else self.plans[0]
            self.text = json.dumps(plan) if not isinstance(plan, str) else plan
            return None
        self.text = None
        assert req.output_schema is not None
        props = req.output_schema["properties"]
        if "po_number" in props and self.pair is not None:
            return build_envelope(
                inline_refs(to_json_schema(PurchaseOrder)),
                self.pair.purchase_order.truth.model_dump(mode="json"),
                source_texts=self.pair.purchase_order.source_texts,
            )
        schema = inline_refs(to_json_schema(Invoice))
        inv = self.pair.invoice if self.pair is not None else self.invoice
        return build_envelope(
            schema, inv.truth.model_dump(mode="json"), source_texts=inv.source_texts
        )


def _first_text(req: ModelRequest) -> str:
    part = req.messages[0].parts[0]
    assert part.kind == "text"
    return part.text


def _rt(tmp_path: Path, adapter: StaticAdapter) -> LocalRuntime:
    cfg = HawserConfig(
        home=tmp_path / "home", record_mode=RecordMode.live, skill_paths=[REPO / "skills"]
    )
    return LocalRuntime(cfg, gateway=Gateway(load_registry(), {"anthropic": adapter}))


def test_bindings() -> None:
    assert parse_binding("$node.extract.items[0].sku") == ("node", "extract", ["items", 0, "sku"])
    assert parse_binding("$input.invoice") == ("input", "invoice", [])
    assert parse_binding("plain") is None
    out = resolve(
        {"a": "$node.x.v[1]", "b": ["$input.doc", 3]},
        inputs={"doc": "asset_1"},
        outputs={"x": {"v": [10, 20]}},
    )
    assert out == {"a": 20, "b": ["asset_1", 3]}


def test_validate_plan_rules() -> None:
    policy = Policy(limits=Limits(max_loop_iterations=3, max_parallel=2, max_plan_steps=10))
    ok = Plan.model_validate(EXTRACT_PLAN)
    assert validate_plan(ok, registry=registry, policy=policy, inputs=["invoice"]) == []

    def problems(nodes: list[dict[str, Any]], **kw: Any) -> set[str]:
        plan = Plan.model_validate(plan_json(nodes, **kw))
        return {
            p.code
            for p in validate_plan(
                plan,
                registry=registry,
                policy=policy,
                inputs=["invoice"],
                allowed_tools=[["compute.*", "http.post"]],
            )
        }

    base = EXTRACT_PLAN["nodes"]
    assert "exit" in problems([n for n in base if n["id"] != "done"])
    assert "unknown_node" in problems(
        [*base[:-1], {"id": "done", "kind": "exit", "status": "success", "result": "$node.nope"}]
    )
    assert "unknown_input" in problems(
        [{**base[0], "context": {"inputs": ["invoice"]}, "input": {"x": "$input.po"}}, *base[1:]]
    )
    assert "unknown_tool" in problems(
        [{"id": "t", "kind": "call_tool", "tool": "nope.tool"}, *base]
    )
    assert "tool_not_allowed" in problems(
        [
            {"id": "t", "kind": "call_tool", "tool": "search.text_index", "input": {"query": "x"}},
            *base,
        ]
    )
    assert "side_effect_unreviewed" in problems(
        [
            {
                "id": "t",
                "kind": "call_tool",
                "tool": "http.post",
                "input": {"url": "https://x", "json": {}},
            },
            *base,
        ]
    )
    assert "side_effect_unreviewed" not in problems(
        [
            {"id": "r", "kind": "review", "reason": "posting"},
            {
                "id": "t",
                "kind": "call_tool",
                "tool": "http.post",
                "input": {"url": "https://x", "json": {}},
            },
            *base,
        ]
    )
    assert "loop_bound" in problems(
        [{"id": "l", "kind": "loop", "body": ["extract"], "max_iterations": 9}, *base]
    )
    assert "parallel_limit" in problems(
        [{"id": "p", "kind": "parallel", "branches": [["extract"], ["sum_ok"], ["gate"]]}, *base]
    )
    assert "expression_invalid" in problems(
        [{**base[1], "expression": "__import__('os')"}, *[n for n in base if n["id"] != "sum_ok"]]
    )
    assert "duplicate_ids" in problems([base[0], base[0], *base[1:]])
    with pytest.raises(ValueError, match="needs 'tool'"):
        Plan.model_validate(plan_json([{"id": "t", "kind": "call_tool"}]))


def test_agent_run_with_canned_plan(
    tmp_path: Path, invoice: GeneratedInvoice, invoice_pdf: Path
) -> None:
    adapter = AgentAdapter([EXTRACT_PLAN], invoice)
    rt = _rt(tmp_path, adapter)
    result = Client(rt).run("Extract the invoice.", inputs=[invoice_pdf], mode="agent")
    assert result.status == RunStatus.accepted, result.error
    assert result.mode is not None and result.mode.value == "agent"
    assert result.result is not None
    assert result.result["sum_ok"] is True
    assert result.result is not None
    assert Invoice.model_validate(result.result["invoice"]) == invoice.truth
    # evidence travels with the binding into the final result
    assert result.field_meta["/invoice/total"].evidence
    assert getattr(result.evidence_for("/invoice/total")[0], "page", None) == 1
    assert result.plan_id is not None
    assert adapter.plan_calls == 1
    assert result.usage.totals.model_calls == 2  # plan + extract
    types = [e.type for e in rt.events(result.run_id)]
    plan_ev = next(
        e
        for e in rt.events(result.run_id)
        if e.type == EventType.plan_created and "plan_id" in e.payload
    )
    assert [n["id"] for n in plan_ev.payload["nodes"]] == [
        "extract",
        "sum_ok",
        "gate",
        "ask",
        "done",
    ]
    assert types.count(EventType.context_selected) >= 1
    keys = {s.key for s in rt.meta.list_steps(result.run_id)}
    assert {"plan:attempt1", "plan/extract:model", "plan/extract:anchor"} <= keys

    # second identical run hits the plan cache: no planner call
    again = Client(rt).run("Extract the invoice.", inputs=[invoice_pdf], mode="agent")
    assert again.status == RunStatus.accepted
    assert adapter.plan_calls == 1
    cached = next(
        e
        for e in rt.events(again.run_id)
        if e.type == EventType.plan_created and "plan_id" in e.payload
    )
    assert cached.payload["source"] == "cached"


def test_replan_after_invalid_plan(
    tmp_path: Path, invoice: GeneratedInvoice, invoice_pdf: Path
) -> None:
    bad = plan_json(
        [
            {"id": "extract", "kind": "call_model", "instruction": "x"},
            {"id": "done", "kind": "exit", "status": "success", "result": "$node.missing"},
        ]
    )
    adapter = AgentAdapter([bad, EXTRACT_PLAN], invoice)
    rt = _rt(tmp_path, adapter)
    result = Client(rt).run(
        "Extract the invoice.",
        inputs=[invoice_pdf],
        mode="agent",
        policy=Policy(planning=PlanningPolicy(cache=False)),
    )
    assert result.status == RunStatus.accepted
    assert adapter.plan_calls == 2
    rejected = [e for e in rt.events(result.run_id) if e.type == EventType.plan_rejected]
    assert rejected and any("unknown node" in p for p in rejected[0].payload["problems"])
    # feedback reached the second planner call
    second = [c for c in adapter.calls if c.output_schema is None][1]
    assert "PREVIOUS PLAN WAS REJECTED" in _first_text(second)

    exhausted = AgentAdapter([bad, bad, bad], invoice)
    failed = Client(_rt(tmp_path / "b", exhausted)).run(
        "Extract the invoice.",
        inputs=[invoice_pdf],
        mode="agent",
        policy=Policy(planning=PlanningPolicy(cache=False, max_replans=1)),
    )
    assert failed.status == RunStatus.failed
    assert failed.error is not None and failed.error["code"] == "plan_invalid"
    assert exhausted.plan_calls == 2


def test_branch_to_review_and_failure_exit(
    tmp_path: Path, invoice: GeneratedInvoice, invoice_pdf: Path
) -> None:
    plan = plan_json(
        [
            {
                "id": "extract",
                "kind": "call_model",
                "instruction": "Extract.",
                "output_schema": INVOICE_SCHEMA,
            },
            {"id": "big", "kind": "compute", "expression": "node.extract.total > 1000000"},
            {
                "id": "gate",
                "kind": "branch",
                "condition": "node.big",
                "then": ["done"],
                "else": ["ask", "stop"],
            },
            {"id": "ask", "kind": "review", "reason": "total is implausibly small"},
            {
                "id": "stop",
                "kind": "exit",
                "status": "failure",
                "reason": "needs a human",
                "result": "$node.extract",
            },
            {"id": "done", "kind": "exit", "status": "success", "result": "$node.extract"},
        ]
    )
    rt = _rt(tmp_path, AgentAdapter([plan], invoice))
    result = Client(rt).run("Check the invoice total.", inputs=[invoice_pdf], mode="agent")
    assert result.status == RunStatus.needs_review
    assert any(w.code == "plan_failure_exit" for w in result.warnings)
    assert any(i.rule_id == "plan.review" for i in result.validation.issues)


def test_loop_parallel_validate_and_budget_reservation(
    tmp_path: Path, invoice: GeneratedInvoice, invoice_pdf: Path
) -> None:
    plan = plan_json(
        [
            {
                "id": "a",
                "kind": "call_model",
                "instruction": "Extract.",
                "output_schema": INVOICE_SCHEMA,
            },
            {
                "id": "b",
                "kind": "call_model",
                "instruction": "Extract again.",
                "output_schema": INVOICE_SCHEMA,
                "context": {"include_nodes": ["a"]},
            },
            {"id": "par", "kind": "parallel", "branches": [["a"], ["b"]], "max_parallel": 2},
            {"id": "n", "kind": "compute", "expression": "len(node.a.line_items)"},
            {"id": "tick", "kind": "compute", "expression": "node.n + 1"},
            {"id": "lp", "kind": "loop", "body": ["tick"], "max_iterations": 3},
            {"id": "chk", "kind": "validate", "target": "a", "validators": ["core.schema"]},
            {
                "id": "done",
                "kind": "exit",
                "status": "success",
                "result": {
                    "first": "$node.a",
                    "count": "$node.n",
                    "loop": "$node.lp",
                    "check": "$node.chk",
                },
            },
        ]
    )
    rt = _rt(tmp_path, AgentAdapter([plan], invoice))
    result = Client(rt).run(
        "Extract twice.",
        inputs=[invoice_pdf],
        mode="agent",
        policy=Policy(limits=Limits(max_parallel=2, max_loop_iterations=5)),
    )
    assert result.status == RunStatus.accepted, result.error
    assert result.result is not None
    assert result.result["count"] == len(invoice.truth.line_items)
    assert result.result["loop"]["iterations"] == 2  # identical outputs -> no progress stop
    assert result.result["check"] == {"errors": 0, "warnings": 0, "ok": True}
    assert result.usage.totals.model_calls == 3  # plan + 2 extractions in parallel
    types = [e.type for e in rt.events(result.run_id)]
    assert EventType.budget_reserved in types and EventType.budget_settled in types
    assert EventType.repair_no_progress in types

    capped = Client(_rt(tmp_path / "cap", AgentAdapter([plan], invoice))).run(
        "Extract twice.",
        inputs=[invoice_pdf],
        mode="agent",
        policy=Policy(
            limits=Limits(max_cost_usd=0.0001, max_parallel=2), planning=PlanningPolicy(cache=False)
        ),
    )
    assert capped.status in (RunStatus.needs_review, RunStatus.failed)
    assert capped.error is not None and capped.error["code"] == "max_cost_usd"


def test_agent_calls_a_skill_as_a_tool(tmp_path: Path) -> None:
    pair = generate_pair(21, discrepancies=[])
    inv = tmp_path / "invoice.pdf"
    po = tmp_path / "purchase_order.pdf"
    inv.write_bytes(pair.invoice.pdf)
    po.write_bytes(pair.purchase_order.pdf)
    plan = plan_json(
        [
            {
                "id": "rec",
                "kind": "call_tool",
                "tool": "skill.invoice-po-reconciliation",
                "input": {"invoice": "$input.invoice", "purchase_order": "$input.purchase_order"},
            },
            {"id": "done", "kind": "exit", "status": "success", "result": "$node.rec"},
        ],
        goal="Reconcile the invoice against the purchase order.",
        inputs=["invoice", "purchase_order"],
    )
    adapter = AgentAdapter([plan], pair.invoice, pair=pair)
    rt = _rt(tmp_path, adapter)
    result = Client(rt).run(
        "Reconcile the invoice against the purchase order.", inputs=[inv, po], mode="agent"
    )
    assert result.status == RunStatus.accepted, result.error
    assert result.result is not None
    assert result.result["reconciliation"]["summary_status"] == "match"
    assert result.field_meta["/invoice/total"].evidence
    assert result.field_meta["/reconciliation/total_delta"].status == FieldStatus.derived
    planner_prompt = _first_text(next(c for c in adapter.calls if c.output_schema is None))
    assert "skill.invoice-po-reconciliation" in planner_prompt
    assert "prefer calling it" in planner_prompt  # the deterministic shortcut hint
    keys = {s.key for s in rt.meta.list_steps(result.run_id)}
    assert "plan/rec" in keys and "plan/rec/extract:invoice:model" in keys


def test_agent_mode_on_a_workflow_skill(tmp_path: Path) -> None:
    """--mode agent on a workflow skill: the planner builds the steps from its contract."""
    pair = generate_pair(22, discrepancies=[])
    inv = tmp_path / "invoice.pdf"
    po = tmp_path / "purchase_order.pdf"
    inv.write_bytes(pair.invoice.pdf)
    po.write_bytes(pair.purchase_order.pdf)
    plan = plan_json(
        [
            {
                "id": "inv",
                "kind": "call_model",
                "instruction": "Extract the invoice.",
                "output_schema": inline_refs(to_json_schema(Invoice)),
                "context": {"inputs": ["invoice"]},
            },
            {
                "id": "po",
                "kind": "call_model",
                "instruction": "Extract the PO.",
                "output_schema": inline_refs(to_json_schema(PurchaseOrder)),
                "context": {"inputs": ["purchase_order"]},
            },
            {
                "id": "align",
                "kind": "call_tool",
                "tool": "compute.align_line_items",
                "input": {"left": "$node.inv.line_items", "right": "$node.po.line_items"},
            },
            {
                "id": "rec",
                "kind": "call_tool",
                "tool": "compute.reconcile",
                "input": {"invoice": "$node.inv", "po": "$node.po", "alignment": "$node.align"},
            },
            {
                "id": "done",
                "kind": "exit",
                "status": "success",
                "result": {
                    "invoice": "$node.inv",
                    "purchase_order": "$node.po",
                    "reconciliation": "$node.rec",
                },
            },
        ],
        goal="Reconcile.",
        inputs=["invoice", "purchase_order"],
    )
    rt = _rt(tmp_path, AgentAdapter([plan], pair.invoice, pair=pair))
    result = Client(rt).run(skill="invoice-po-reconciliation", mode="agent", inputs=[inv, po])
    assert result.status == RunStatus.accepted, [i.message for i in result.validation.issues] + [
        result.error
    ]
    assert result.result is not None
    assert result.result["reconciliation"]["summary_status"] == "match"
    assert result.field_meta["/purchase_order/po_number"].evidence
    # the skill's validators ran over the planner-built result
    rules = {r.rule_id for r in result.validation.rules_run}
    assert "rules.expressions" in rules and "invoice_po.summary_consistent" in rules


def test_tool_input_schemas_are_inferred_and_enforced() -> None:
    spec = registry.get("compute.align_line_items").spec
    assert set(spec.input_schema["properties"]) == {
        "left",
        "right",
        "key",
        "description",
        "quantity",
        "price",
        "fuzzy_threshold",
    }
    assert spec.input_schema["required"] == ["left", "right"]
    assert spec.input_schema["properties"]["left"] == {"type": "array"}
    assert spec.input_schema["properties"]["fuzzy_threshold"] == {"type": "number"}
    plan = Plan.model_validate(
        plan_json(
            [
                {
                    "id": "a",
                    "kind": "call_tool",
                    "tool": "compute.align_line_items",
                    "input": {"invoice_items": [], "right": []},
                },
                {"id": "done", "kind": "exit", "status": "success", "result": "$node.a"},
            ]
        )
    )
    codes = {
        p.code: p.message
        for p in validate_plan(plan, registry=registry, policy=Policy(), inputs=["invoice"])
    }
    assert "tool_input_keys" in codes
    assert "unknown ['invoice_items']" in codes["tool_input_keys"]
    assert "missing ['left']" in codes["tool_input_keys"]
