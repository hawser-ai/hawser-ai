"""Static plan validation (blueprint §10.1): reject before any work begins."""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from hawser.models import Policy, SideEffect
from hawser.plan.bindings import parse_binding, references
from hawser.plan.ir import Node, NodeKind, Plan
from hawser.structured import inline_refs
from hawser.tools.registry import ToolRegistry
from hawser.validation.expr import ExpressionError, compile_check


@dataclass
class PlanProblem:
    node: str | None
    code: str
    message: str

    def __str__(self) -> str:
        return f"{self.node or '(plan)'}: {self.message} [{self.code}]"


class CostModel(BaseModel):
    """Per-call estimates used for the static budget check."""

    model_call_usd: float = 0.02
    tool_call_usd: float = 0.0


def _allowed(name: str, layers: list[list[str] | None]) -> bool:
    return all(
        any(name == p or fnmatch.fnmatchcase(name, p) for p in layer)
        for layer in layers
        if layer is not None
    )


def validate_plan(  # noqa: PLR0912, PLR0915
    plan: Plan,
    *,
    registry: ToolRegistry,
    policy: Policy,
    inputs: list[str],
    allowed_tools: list[list[str] | None] | None = None,
    skills: set[str] | None = None,
    cost: CostModel | None = None,
) -> list[PlanProblem]:
    cost = cost or CostModel()
    problems: list[PlanProblem] = []
    ids = [n.id for n in plan.nodes]
    if len(set(ids)) != len(ids):
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        problems.append(PlanProblem(None, "duplicate_ids", f"duplicate node ids {dupes}"))
    known = set(ids)
    skills = skills or set()

    def ref_ok(node: Node, rid: str, what: str) -> None:
        if rid not in known:
            problems.append(
                PlanProblem(node.id, "unknown_node", f"{what} references unknown node {rid!r}")
            )

    # exits
    exits = [n for n in plan.nodes if n.kind == NodeKind.exit]
    if sum(1 for n in exits if n.status == "success") != 1:
        problems.append(PlanProblem(None, "exit", "a plan needs exactly one success exit"))

    # per-node checks
    seen_review = False
    est_usd = 0.0
    steps = 0
    for node in plan.nodes:
        steps += 1
        for rid in references(node.input) | references(node.result):
            ref_ok(node, rid, "binding")
        for ref in (node.target,):
            if ref:
                ref_ok(node, ref, "target")
        for rid in [
            *node.then,
            *node.otherwise,
            *node.body,
            *[x for br in node.branches for x in br],
        ]:
            ref_ok(node, rid, "container")
        for value in _bindings_in(node.input) + _bindings_in(node.result):
            parsed = parse_binding(value)
            if parsed and parsed[0] == "input" and parsed[1] not in inputs:
                problems.append(
                    PlanProblem(
                        node.id, "unknown_input", f"unknown input {parsed[1]!r}; have {inputs}"
                    )
                )

        if node.kind == NodeKind.call_tool:
            assert node.tool is not None
            if node.tool.startswith("skill."):
                if node.tool[6:] not in skills:
                    problems.append(
                        PlanProblem(node.id, "unknown_skill", f"unknown skill {node.tool!r}")
                    )
                est_usd += 2 * cost.model_call_usd
                continue
            try:
                spec = registry.get(node.tool).spec
            except Exception:
                problems.append(PlanProblem(node.id, "unknown_tool", f"unknown tool {node.tool!r}"))
                continue
            if allowed_tools is not None and not _allowed(node.tool, allowed_tools):
                problems.append(
                    PlanProblem(node.id, "tool_not_allowed", f"tool {node.tool!r} is not permitted")
                )
            props = spec.input_schema.get("properties") or {}
            if props and spec.input_schema.get("additionalProperties") is False:
                unknown = sorted(set(node.input) - set(props))
                missing = sorted(set(spec.input_schema.get("required", [])) - set(node.input))
                if unknown or missing:
                    problems.append(
                        PlanProblem(
                            node.id,
                            "tool_input_keys",
                            f"{node.tool} takes {sorted(props)}; "
                            f"unknown {unknown}, missing {missing}",
                        )
                    )
            if spec.side_effect == SideEffect.external_write and not seen_review:
                pre = policy.approvals.pre_approved
                aid = node.action_id or ""
                if not any(fnmatch.fnmatchcase(f"{node.tool}:{aid}", p) for p in pre):
                    problems.append(
                        PlanProblem(
                            node.id,
                            "side_effect_unreviewed",
                            f"{node.tool} writes externally; review it first or pre-approve it",
                        )
                    )
            est_usd += spec.cost_estimate.fixed_usd if spec.cost_estimate else cost.tool_call_usd
        elif node.kind == NodeKind.call_model:
            if node.output_schema is not None:
                try:
                    inline_refs(node.output_schema)
                except ValueError as exc:
                    problems.append(PlanProblem(node.id, "schema_invalid", str(exc)))
            if node.context.pages == "matching" and not node.context.query:
                problems.append(
                    PlanProblem(node.id, "context_query_missing", "pages=matching needs a query")
                )
            for rid in node.context.include_nodes:
                ref_ok(node, rid, "context.include_nodes")
            est_usd += cost.model_call_usd
        elif node.kind in (NodeKind.compute, NodeKind.branch, NodeKind.loop):
            expr = node.expression or node.condition or node.until
            if expr:
                try:
                    compile_check(expr)
                except ExpressionError as exc:
                    problems.append(PlanProblem(node.id, "expression_invalid", str(exc)))
            if node.kind == NodeKind.loop:
                limit = policy.limits.max_loop_iterations
                assert node.max_iterations is not None
                if limit is not None and node.max_iterations > limit:
                    problems.append(
                        PlanProblem(
                            node.id,
                            "loop_bound",
                            f"max_iterations {node.max_iterations} exceeds policy {limit}",
                        )
                    )
                body_cost = (
                    sum(
                        cost.model_call_usd
                        for b in node.body
                        if plan.node(b).kind == NodeKind.call_model
                    )
                    if all(b in known for b in node.body)
                    else 0.0
                )
                est_usd += body_cost * max(node.max_iterations - 1, 0)
        elif node.kind == NodeKind.parallel:
            width = node.max_parallel or len(node.branches)
            limit = policy.limits.max_parallel
            if limit is not None and width > limit:
                problems.append(
                    PlanProblem(
                        node.id, "parallel_limit", f"parallel width {width} exceeds policy {limit}"
                    )
                )
        elif node.kind == NodeKind.review:
            seen_review = True
        elif node.kind == NodeKind.validate and node.validators:
            from hawser.validation.registry import BUILTIN  # noqa: PLC0415

            for v in node.validators:
                vid = v if isinstance(v, str) else v.id
                if not (vid.startswith("core.") or vid in BUILTIN):
                    problems.append(
                        PlanProblem(node.id, "validator_unknown", f"unknown validator {vid!r}")
                    )

    lim = policy.limits
    if lim.max_plan_steps is not None and steps > lim.max_plan_steps:
        problems.append(
            PlanProblem(
                None, "plan_steps", f"{steps} nodes exceed max_plan_steps {lim.max_plan_steps}"
            )
        )
    if lim.max_cost_usd is not None and est_usd > lim.max_cost_usd:
        problems.append(
            PlanProblem(
                None,
                "budget",
                f"estimated cost ${est_usd:.4f} exceeds max_cost_usd ${lim.max_cost_usd:.4f}",
            )
        )
    if any(
        n.kind == NodeKind.exit and n.status == "success" and n.result is None for n in plan.nodes
    ):
        problems.append(
            PlanProblem(
                None,
                "exit_result",
                "the success exit must set a result (a binding or an object of bindings)",
            )
        )
    return problems


def _bindings_in(value: Any) -> list[str]:
    out: list[str] = []
    if isinstance(value, dict):
        for v in value.values():  # type: ignore[union-attr]
            out += _bindings_in(v)
    elif isinstance(value, list):
        for v in value:  # type: ignore[union-attr]
            out += _bindings_in(v)
    elif isinstance(value, str) and value.startswith("$"):
        out.append(value)
    return out


def estimated_cost(plan: Plan, cost: CostModel | None = None) -> float:
    cost = cost or CostModel()
    total = 0.0
    for n in plan.nodes:
        if n.kind == NodeKind.call_model:
            total += cost.model_call_usd
        elif n.kind == NodeKind.call_tool and (n.tool or "").startswith("skill."):
            total += 2 * cost.model_call_usd
    return total


__all__ = ["CostModel", "PlanProblem", "estimated_cost", "validate_plan"]
