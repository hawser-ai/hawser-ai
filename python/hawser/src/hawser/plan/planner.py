"""The planner (blueprint §10, plan WP4.2): goal + catalogue + limits -> validated Plan.

The model returns plain JSON (prompt-guided; the IR is validated here, not by the provider).
Invalid plans get one round of feedback per ``policy.planning.max_replans``. Validated plans are
cached by goal, tool catalogue, policy and input shape so a repeated goal skips the planner.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from hawser.errors import PlanInvalid
from hawser.gateway import Message, ModelRequest, TextPart
from hawser.gateway.gateway import parse_json_text
from hawser.models import Asset, EventType, ModelSpec, Policy, StepKind
from hawser.plan.ir import Plan
from hawser.plan.validate import PlanProblem, validate_plan
from hawser.recording import canonical_json
from hawser.runtime.budget import check_before_model_call, estimate_call
from hawser.runtime.context import RunContext
from hawser.runtime.extract import prompt
from hawser.runtime.steps import StepHandle
from hawser.skills.bundle import Skill
from hawser.tools.registry import ToolRegistry

PLAN_EXAMPLE = {
    "goal": "Extract the invoice header and line items and check the totals",
    "inputs": ["invoice"],
    "notes": "One extraction, then deterministic arithmetic.",
    "nodes": [
        {
            "id": "extract",
            "kind": "call_model",
            "instruction": "Extract the invoice.",
            "output_schema": {
                "type": "object",
                "properties": {
                    "invoice_number": {"type": "string"},
                    "total": {"type": "number"},
                    "line_items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "description": {"type": "string"},
                                "line_total": {"type": "number"},
                            },
                            "required": ["description", "line_total"],
                        },
                    },
                },
                "required": ["invoice_number", "total", "line_items"],
            },
            "context": {"inputs": ["invoice"], "pages": "all"},
        },
        {
            "id": "sum_ok",
            "kind": "compute",
            "expression": (
                "approx(sum(node.extract.line_items[:].line_total), node.extract.total, 0.05)"
            ),
        },
        {
            "id": "gate",
            "kind": "branch",
            "condition": "node.sum_ok",
            "then": ["done"],
            "else": ["ask"],
        },
        {"id": "ask", "kind": "review", "reason": "line items do not add up to the printed total"},
        {
            "id": "done",
            "kind": "exit",
            "status": "success",
            "result": {"invoice": "$node.extract", "totals_consistent": "$node.sum_ok"},
        },
    ],
}


class PlannerOutput(BaseModel):
    plan: Plan
    attempts: int
    cached: bool = False
    problems_seen: list[str] = Field(default_factory=list)


def catalogue(
    registry: ToolRegistry, allowed: list[list[str] | None], skills: list[Skill]
) -> list[dict[str, Any]]:
    import fnmatch  # noqa: PLC0415

    def ok(name: str) -> bool:
        return all(
            any(name == p or fnmatch.fnmatchcase(name, p) for p in layer)
            for layer in allowed
            if layer is not None
        )

    out: list[dict[str, Any]] = []
    for spec in registry.specs():
        if spec.name == "document.extract" or not ok(spec.name):
            continue
        props = spec.input_schema.get("properties", {})
        out.append(
            {
                "tool": spec.name,
                "description": spec.description,
                "input": {k: v.get("type", "any") for k, v in props.items()} if props else "object",
                "side_effect": spec.side_effect.value,
            }
        )
    for s in skills:
        out.append(
            {
                "tool": f"skill.{s.name}",
                "description": s.manifest.description,
                "input": {name: spec.type.value for name, spec in s.manifest.inputs.items()},
                "side_effect": "external_read",
                "output_fields": sorted(s.output_schema.get("properties", {})),
            }
        )
    return out


def plan_cache_key(
    goal: str,
    inputs: dict[str, Asset],
    tools: list[dict[str, Any]],
    policy: Policy,
    output_schema: dict[str, Any] | None,
) -> str:
    shape = {
        name: (a.media_class.value, a.manifest.pdf.page_count if a.manifest.pdf else 1)
        for name, a in inputs.items()
    }
    payload = canonical_json(
        {
            "goal": goal,
            "inputs": shape,
            "tools": [t["tool"] for t in tools],
            "policy": policy.model_dump(mode="json"),
            "schema": output_schema,
        }
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def _user_message(  # noqa: PLR0917
    goal: str,
    inputs: dict[str, Asset],
    tools: list[dict[str, Any]],
    policy: Policy,
    output_schema: dict[str, Any] | None,
    feedback: list[str],
) -> str:
    lines = [f"GOAL:\n{goal}", "", "INPUTS (name: media, pages):"]
    for name, a in inputs.items():
        pages = a.manifest.pdf.page_count if a.manifest.pdf else 1
        text_layer = (
            "text"
            if (a.manifest.pdf and any(c > 20 for c in a.manifest.pdf.chars_per_page))
            else "image-only"
        )
        lines.append(f"- {name}: {a.media_class.value}, {pages} page(s), {text_layer}")
    lines += ["", "TOOLS:"]
    for t in tools:
        extra = f", outputs {t['output_fields']}" if "output_fields" in t else ""
        lines.append(
            f"- {t['tool']} ({t['side_effect']}): {t['description']} "
            f"input={json.dumps(t['input'])}{extra}"
        )
    lim = policy.limits
    lines += [
        "",
        "LIMITS:",
        f"- max model calls: {lim.max_model_calls or 'unbounded'}; "
        f"max cost USD: {lim.max_cost_usd or 'unbounded'}",
        f"- max plan steps: {lim.max_plan_steps or 'unbounded'}; "
        f"max loop iterations: {lim.max_loop_iterations or 100}; "
        f"max parallel: {lim.max_parallel or 4}",
        f"- external writes pre-approved: {policy.approvals.pre_approved or 'none'} "
        "(otherwise a review node must precede them)",
    ]
    if output_schema is not None:
        lines += [
            "",
            "REQUIRED FINAL RESULT SCHEMA (the success exit's result must conform):",
            json.dumps(output_schema, separators=(",", ":")),
        ]
    lines += ["", "EXAMPLE PLAN:", json.dumps(PLAN_EXAMPLE, separators=(",", ":"))]
    if feedback:
        lines += [
            "",
            "YOUR PREVIOUS PLAN WAS REJECTED. Fix these problems:",
            *[f"- {f}" for f in feedback],
        ]
    lines += ["", "Return only the plan JSON."]
    return "\n".join(lines)


class PlanCache:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def get(self, key: str) -> Plan | None:
        p = self.directory / f"{key}.json"
        if not p.exists():
            return None
        try:
            return Plan.model_validate_json(p.read_text(encoding="utf-8"))
        except ValidationError:
            return None

    def put(self, key: str, plan: Plan) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        (self.directory / f"{key}.json").write_text(
            plan.model_dump_json(indent=2, by_alias=True) + "\n", encoding="utf-8"
        )


def make_plan(
    ctx: RunContext,
    *,
    goal: str,
    inputs: dict[str, Asset],
    spec: ModelSpec,
    candidates: list[str],
    registry: ToolRegistry,
    allowed: list[list[str] | None],
    skills: list[Skill],
    output_schema: dict[str, Any] | None,
    cache: PlanCache | None,
) -> PlannerOutput:
    policy = ctx.request.policy
    tools = catalogue(registry, allowed, skills)
    key = plan_cache_key(goal, inputs, tools, policy, output_schema)
    if cache is not None and policy.planning.cache:
        hit = cache.get(key)
        if hit is not None:
            problems = validate_plan(
                hit,
                registry=registry,
                policy=policy,
                inputs=list(inputs),
                allowed_tools=allowed,
                skills={s.name for s in skills},
            )
            if not problems:
                ctx.emitter.emit(
                    EventType.plan_created,
                    {"source": "cached", "cache_key": key, "nodes": [n.id for n in hit.nodes]},
                )
                return PlannerOutput(plan=hit, attempts=0, cached=True)

    feedback: list[str] = []
    seen: list[str] = []
    attempts = 0
    max_attempts = 1 + policy.planning.max_replans
    while attempts < max_attempts:
        attempts += 1
        request = ModelRequest(
            model=spec.id,
            system=prompt("planner.md"),
            messages=[
                Message(
                    role="user",
                    parts=[
                        TextPart(
                            text=_user_message(goal, inputs, tools, policy, output_schema, feedback)
                        )
                    ],
                )
            ],
            output_schema=None,
            max_output_tokens=min(ctx.config.max_output_tokens, 8_000),
            effort=ctx.config.default_effort,
        )

        def _call(h: StepHandle, req: ModelRequest = request) -> dict[str, Any]:
            check_before_model_call(ctx.usage, policy, estimate_call(req, spec))
            resp = ctx.gateway.complete_any(req, candidates, policy=policy)
            h.record_model_call(resp.usage)
            parsed, err = parse_json_text(resp.text or "")
            ctx.emitter.emit(
                EventType.model_called,
                {
                    "prefix": "plan",
                    "model": f"{resp.usage.provider}/{resp.usage.model}",
                    "replayed": resp.usage.replayed,
                    "input_tokens": resp.usage.input_tokens,
                    "output_tokens": resp.usage.output_tokens,
                    "cost_usd": resp.usage.cost_usd,
                    "latency_ms": resp.usage.latency_ms,
                },
                step_id=h.step_id,
            )
            return {"plan": parsed, "error": err}

        out = ctx.steps.run(
            f"plan:attempt{attempts}", f"plan attempt {attempts}", StepKind.plan, _call
        )
        if out.get("plan") is None:
            feedback = [f"the response was not valid JSON: {out.get('error')}"]
            seen.extend(feedback)
            continue
        try:
            plan = Plan.model_validate(out["plan"])
        except ValidationError as exc:
            feedback = [
                f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()[:8]
            ]
            seen.extend(feedback)
            ctx.emitter.emit(EventType.plan_rejected, {"attempt": attempts, "problems": feedback})
            continue
        problems: list[PlanProblem] = validate_plan(
            plan,
            registry=registry,
            policy=policy,
            inputs=list(inputs),
            allowed_tools=allowed,
            skills={s.name for s in skills},
        )
        if problems:
            feedback = [str(p) for p in problems[:10]]
            seen.extend(feedback)
            ctx.emitter.emit(EventType.plan_rejected, {"attempt": attempts, "problems": feedback})
            continue
        if cache is not None and policy.planning.cache:
            cache.put(key, plan)
        return PlannerOutput(plan=plan, attempts=attempts, problems_seen=seen)
    raise PlanInvalid(
        f"no valid plan after {attempts} attempt(s): {'; '.join(seen[-5:])}",
        code="plan_invalid",
        details={"attempts": attempts, "problems": seen},
    )


__all__ = ["PLAN_EXAMPLE", "PlanCache", "PlannerOutput", "catalogue", "make_plan", "plan_cache_key"]
