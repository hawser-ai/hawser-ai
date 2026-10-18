"""Plan executor (plan WP4.3): checkpointed nodes, budget reservation for parallel branches,
bounded loops with no-progress detection, branch/validate/review/repair/exit semantics."""

from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from hawser.errors import BudgetExceeded, PlanInvalid
from hawser.gateway import ModelRequest
from hawser.models import (
    EventType,
    Evidence,
    FieldMeta,
    Issue,
    ModelSpec,
    RepairKind,
    Severity,
    StepKind,
)
from hawser.plan.bindings import UnresolvedBinding, parse_binding, resolve
from hawser.plan.context import select_documents, summarize_output
from hawser.plan.ir import Node, NodeKind, Plan
from hawser.recording import canonical_json
from hawser.runtime.budget import estimate_call
from hawser.runtime.context import RESERVED_TOKENS, DocumentContext, RunContext
from hawser.runtime.extract import Extraction, extract_structured
from hawser.runtime.repair import repair_loop, validate_extraction
from hawser.runtime.steps import StepHandle
from hawser.skills.bundle import Skill
from hawser.structured import inline_refs
from hawser.tools.broker import Broker
from hawser.validation.core import make_issue
from hawser.validation.expr import ExpressionError, evaluate
from hawser.validation.registry import build_validators

DEFAULT_CONTEXT_TOKENS = 60_000


class ExitSignal(Exception):
    def __init__(self, node: Node) -> None:
        super().__init__(node.id)
        self.node = node


@dataclass
class AgentOutcome:
    result: Any = None
    field_meta: dict[str, FieldMeta] = field(default_factory=dict)
    evidence: list[Evidence] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    status: str = "success"  # success | failure
    reason: str | None = None
    outputs: dict[str, Any] = field(default_factory=dict)


class StepOutput(BaseModel):
    value: Any = None


class PlanRunner:
    def __init__(
        self,
        ctx: RunContext,
        plan: Plan,
        *,
        documents: dict[str, list[DocumentContext]],
        inputs: dict[str, Any],
        spec: ModelSpec,
        candidates: list[str],
        skills: dict[str, Skill],
    ) -> None:
        self.ctx = ctx
        self.plan = plan
        self.documents = documents
        self.inputs = inputs
        self.spec = spec
        self.candidates = candidates
        self.skills = skills
        self.broker = Broker(ctx)
        self.outputs: dict[str, Any] = {}
        self.extractions: dict[str, Extraction] = {}
        self.issues: list[Issue] = []
        self.context_tokens = ctx.request.policy.planning.context_tokens or min(
            DEFAULT_CONTEXT_TOKENS,
            int(spec.context_window * ctx.config.context_fraction) - RESERVED_TOKENS,
        )

    # -- driver ----------------------------------------------------------------------------
    def run(self) -> AgentOutcome:
        try:
            for node in self.plan.top_level():
                self._execute(node, suffix="")
        except ExitSignal as sig:
            return self._finish(sig.node)
        success = next(
            n for n in self.plan.nodes if n.kind == NodeKind.exit and n.status == "success"
        )
        return self._finish(success)

    def _finish(self, node: Node) -> AgentOutcome:
        out = AgentOutcome(
            outputs=dict(self.outputs), status=node.status or "success", reason=node.reason
        )
        if node.status == "failure":
            out.issues = list(self.issues)
            out.result = (
                resolve(node.result, inputs=self.inputs, outputs=self.outputs)
                if node.result is not None
                else None
            )
            return out
        out.result = resolve(node.result, inputs=self.inputs, outputs=self.outputs)
        self._attach(node.result, [], out)
        out.issues = list(self.issues)
        return out

    # -- evidence attachment ---------------------------------------------------------------
    def _attach(self, template: Any, path: list[str], out: AgentOutcome) -> None:
        """Carry extraction field_meta/evidence under the pointer where the result places it."""
        if isinstance(template, dict):
            for k, v in template.items():  # type: ignore[union-attr]
                self._attach(v, [*path, str(k)], out)
            return
        parsed = parse_binding(template)
        if not parsed or parsed[0] != "node":
            return
        _, node_id, sub = parsed
        ext = self.extractions.get(node_id)
        if ext is None:
            return
        prefix = "".join(f"/{p}" for p in path)
        sub_ptr = "".join(f"/{p}" for p in sub)
        for ptr, meta in ext.field_meta.items():
            if sub_ptr and not ptr.startswith(sub_ptr):
                continue
            out.field_meta[prefix + ptr[len(sub_ptr) :]] = meta.model_copy(deep=True)
        out.evidence.extend(ext.evidence)

    # -- node execution --------------------------------------------------------------------
    def _execute(self, node: Node, *, suffix: str) -> Any:  # noqa: PLR0911
        key = f"plan/{node.id}{suffix}"
        k = node.kind
        if k == NodeKind.call_model:
            return self._call_model(node, key)
        if k == NodeKind.call_tool:
            return self._call_tool(node, key)
        if k == NodeKind.compute:
            return self._store(node, self._eval(node.expression or ""))
        if k == NodeKind.branch:
            cond = bool(self._eval(node.condition or ""))
            self._store(node, cond)
            for nid in node.then if cond else node.otherwise:
                self._execute(self.plan.node(nid), suffix=suffix)
            return cond
        if k == NodeKind.loop:
            return self._loop(node, suffix)
        if k == NodeKind.parallel:
            return self._parallel(node, suffix)
        if k == NodeKind.validate:
            return self._validate(node)
        if k == NodeKind.review:
            issue = make_issue(
                "plan.review",
                Severity.error,
                node.reason or "review requested",
                repair=RepairKind.review,
            )
            self.issues.append(issue)
            return self._store(node, {"requested": True, "reason": node.reason})
        if k == NodeKind.repair:
            return self._repair(node)
        if k == NodeKind.exit:
            raise ExitSignal(node)
        raise PlanInvalid(f"unsupported node kind {k}", code="plan_invalid")

    def _store(self, node: Node, value: Any) -> Any:
        self.outputs[node.id] = value
        return value

    def _eval(self, expression: str) -> Any:
        try:
            return evaluate(expression, {"node": self.outputs, "input": self.inputs})
        except ExpressionError as exc:
            raise PlanInvalid(f"expression failed: {exc}", code="expression_failed") from exc

    def _resolve(self, value: Any) -> Any:
        try:
            return resolve(value, inputs=self.inputs, outputs=self.outputs)
        except UnresolvedBinding as exc:
            raise PlanInvalid(str(exc), code="binding_unresolved") from exc

    # -- call_model ------------------------------------------------------------------------
    def _call_model(self, node: Node, key: str) -> Any:
        docs: list[DocumentContext] = []
        names = node.context.inputs or list(self.documents)
        for name in names:
            docs.extend(self.documents.get(name, []))
        budget = node.context.max_tokens or self.context_tokens
        narrowed, report = select_documents(docs, node.context, budget_tokens=budget)
        self.ctx.emitter.emit(
            EventType.context_selected, {"prefix": key, **report, "budget_tokens": budget}
        )
        instruction = node.instruction or ""
        if node.context.include_nodes:
            summaries = "\n".join(
                summarize_output(nid, self.outputs.get(nid)) for nid in node.context.include_nodes
            )
            instruction = f"{instruction}\n\nPrior results:\n{summaries}"
        schema = inline_refs(node.output_schema) if node.output_schema is not None else None
        extraction = extract_structured(
            self.ctx,
            narrowed,
            schema=schema,
            instruction=instruction,
            spec=self.spec,
            prefix=key,
            candidates=self.candidates,
        )
        self.extractions[node.id] = extraction
        return self._store(node, extraction.result)

    # -- call_tool -------------------------------------------------------------------------
    def _call_tool(self, node: Node, key: str) -> Any:
        assert node.tool is not None
        payload = self._resolve(node.input)
        if node.tool.startswith("skill."):
            return self._store(node, self._run_skill(node, key, payload))

        def via_broker(h: StepHandle) -> StepOutput:
            result = self.broker.call(node.tool or "", payload, handle=h, action_id=node.action_id)
            return StepOutput(value=result.output)

        out = self.ctx.steps.run(key, node.tool, StepKind.tool, via_broker, output_type=StepOutput)
        return self._store(node, out.value)

    def _run_skill(self, node: Node, key: str, payload: dict[str, Any]) -> Any:
        from hawser.runtime.workflow import execute_skill  # noqa: PLC0415 - avoid import cycle

        name = (node.tool or "")[6:]
        skill = self.skills[name]
        by_asset = {d.asset.id: name for name, docs in self.documents.items() for d in docs}
        documents: dict[str, list[DocumentContext]] = {}
        for input_name in skill.manifest.inputs:
            raw = node.input.get(input_name)
            parsed = parse_binding(raw)
            if parsed and parsed[0] == "input":
                source = parsed[1]
            else:
                resolved = payload.get(input_name)
                source = (
                    by_asset.get(resolved, input_name) if isinstance(resolved, str) else input_name
                )
            documents[input_name] = self.documents.get(source, [])
        output = execute_skill(self.ctx, skill, documents, self.spec, self.candidates, prefix=key)
        self.extractions[node.id] = Extraction(
            result=output.result, field_meta=output.field_meta, evidence=output.evidence
        )
        self.issues.extend(output.issues)
        return output.result

    # -- containers ------------------------------------------------------------------------
    def _loop(self, node: Node, suffix: str) -> Any:
        limit = node.max_iterations or 1
        seen: set[str] = set()
        iterations = 0
        for i in range(limit):
            iterations = i + 1
            for nid in node.body:
                self._execute(self.plan.node(nid), suffix=f"{suffix}/{i}")
            fp = hashlib.sha256(
                canonical_json({nid: self.outputs.get(nid) for nid in node.body}).encode()
            ).hexdigest()[:16]
            if node.until and bool(self._eval(node.until)):
                break
            if fp in seen:
                self.ctx.emitter.emit(
                    EventType.repair_no_progress, {"node": node.id, "iteration": i}
                )
                break
            seen.add(fp)
        return self._store(node, {"iterations": iterations})

    def _parallel(self, node: Node, suffix: str) -> Any:
        policy = self.ctx.request.policy
        width = min(node.max_parallel or len(node.branches), policy.limits.max_parallel or 4)
        # Reserve: estimate each branch's model calls up front against the cost cap.
        est_per_call = (
            estimate_call(ModelRequest(model=self.spec.id, messages=[]), self.spec).cost_usd + 0.01
        )
        est_total = sum(
            sum(est_per_call for nid in br if self.plan.node(nid).kind == NodeKind.call_model)
            for br in node.branches
        )
        spent = sum(c.cost_usd for c in self.ctx.usage.model_calls) + sum(
            t.cost_usd for t in self.ctx.usage.tool_calls
        )
        cap = policy.limits.max_cost_usd
        if cap is not None and spent + est_total > cap:
            self.ctx.emitter.emit(
                EventType.budget_exceeded,
                {"node": node.id, "reserve_usd": est_total, "spent_usd": spent, "cap_usd": cap},
            )
            raise BudgetExceeded(
                f"parallel node {node.id} needs ~${est_total:.4f}; "
                f"${max(cap - spent, 0):.4f} remains",
                code="max_cost_usd",
            )
        self.ctx.emitter.emit(
            EventType.budget_reserved,
            {
                "node": node.id,
                "reserve_usd": est_total,
                "branches": len(node.branches),
                "width": width,
            },
        )

        def run_branch(ids: list[str]) -> None:
            for nid in ids:
                self._execute(self.plan.node(nid), suffix=suffix)

        with ThreadPoolExecutor(max_workers=max(1, width)) as pool:
            list(pool.map(run_branch, node.branches))
        actual = (
            sum(c.cost_usd for c in self.ctx.usage.model_calls)
            + sum(t.cost_usd for t in self.ctx.usage.tool_calls)
            - spent
        )
        self.ctx.emitter.emit(
            EventType.budget_settled,
            {"node": node.id, "reserve_usd": est_total, "actual_usd": round(actual, 6)},
        )
        if est_total > actual:
            self.ctx.emitter.emit(
                EventType.budget_released,
                {"node": node.id, "released_usd": round(est_total - actual, 6)},
            )
        return self._store(node, {"branches": len(node.branches)})

    # -- validate / repair -----------------------------------------------------------------
    def _validators(self, node: Node) -> list[Any]:
        return build_validators(node.validators or [])

    def _validate(self, node: Node) -> Any:
        target = node.target or ""
        ext = self.extractions.get(target)
        value = self.outputs.get(target)
        schema = (
            inline_refs(self.plan.node(target).output_schema or {"type": "object"})
            if self.plan.node(target).kind == NodeKind.call_model
            and self.plan.node(target).output_schema
            else None
        )
        if ext is None:
            ext = Extraction(result=value)
        report = validate_extraction(
            self.ctx, ext, schema or {"type": "object"}, self._validators(node)
        )
        self.issues.extend(report.issues)
        summary = {
            "errors": report.count_errors(),
            "warnings": report.count_warnings(),
            "ok": not report.has_errors,
        }
        return self._store(node, summary)

    def _repair(self, node: Node) -> Any:
        target = node.target or ""
        ext = self.extractions.get(target)
        tnode = self.plan.node(target)
        if ext is None or tnode.kind != NodeKind.call_model or tnode.output_schema is None:
            return self._store(
                node, {"attempts": 0, "skipped": "target is not a schema extraction"}
            )
        schema = inline_refs(tnode.output_schema)
        validators = self._validators(node)
        report = validate_extraction(self.ctx, ext, schema, validators)
        if not report.has_errors:
            return self._store(node, {"attempts": 0, "errors": 0})
        docs: list[DocumentContext] = []
        for name in tnode.context.inputs or list(self.documents):
            docs.extend(self.documents.get(name, []))
        narrowed, _ = select_documents(
            docs, tnode.context, budget_tokens=tnode.context.max_tokens or self.context_tokens
        )
        outcome = repair_loop(
            self.ctx,
            narrowed,
            schema=schema,
            instruction=tnode.instruction or "",
            spec=self.spec,
            extraction=ext,
            report=report,
            validators=validators,
            prefix=f"plan/{target}",
            candidates=self.candidates,
        )
        self.ctx.repair_attempts += outcome.attempts
        self.extractions[target] = outcome.extraction
        self.outputs[target] = outcome.extraction.result
        # issues from the pre-repair validation are superseded
        self.issues = [i for i in self.issues if i.id not in {x.id for x in report.issues}] + list(
            outcome.report.issues
        )
        return self._store(
            node,
            {
                "attempts": outcome.attempts,
                "errors": outcome.report.count_errors(),
                "stopped": outcome.stopped_reason,
            },
        )


__all__ = ["AgentOutcome", "PlanRunner"]
