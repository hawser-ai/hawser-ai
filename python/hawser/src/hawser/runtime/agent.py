"""Agent mode: admit → index → plan (or cached plan) → validate → execute → validate → decide."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from hawser.clock import now
from hawser.errors import PlanInvalid
from hawser.ids import new_id
from hawser.models import (
    ArtifactKind,
    EventType,
    ExecutionMode,
    PlanEnvelope,
    RunResult,
    RunStatus,
    ValidationReport,
)
from hawser.plan.executor import PlanRunner
from hawser.plan.planner import PlanCache, make_plan
from hawser.runtime.context import DocumentContext, RunContext
from hawser.runtime.direct import (
    admit,
    finalize,
    index_documents,
    inline_schema,
    plan_models,
    set_status,
)
from hawser.runtime.extract import Extraction
from hawser.runtime.repair import validate_extraction
from hawser.runtime.workflow import bind_inputs
from hawser.skills.bundle import Skill
from hawser.skills.registry import SkillRegistry
from hawser.tools.registry import registry as tool_registry
from hawser.validation import decide
from hawser.validation.registry import build_validators


def _goal(ctx: RunContext, skill: Skill | None) -> str:
    if ctx.request.instruction:
        return ctx.request.instruction
    if skill is not None:
        return skill.manifest.instruction or skill.manifest.description
    raise PlanInvalid("agent mode needs an instruction (the goal)", code="instruction_missing")


def _skill_match(goal: str, skills: list[Skill]) -> Skill | None:
    """Cheap deterministic shortcut (blueprint §3): a goal that names a skill should use it."""
    words = {w for w in goal.lower().replace("-", " ").split() if len(w) > 3}
    best: tuple[int, Skill] | None = None
    for s in skills:
        name_words = set(s.name.replace("-", " ").split())
        desc_words = {w for w in s.manifest.description.lower().split() if len(w) > 3}
        score = 3 * len(words & name_words) + len(words & desc_words)
        if score >= 3 and (best is None or score > best[0]):
            best = (score, s)
    return best[1] if best else None


def run_agent(ctx: RunContext, skills: SkillRegistry) -> RunResult:
    skill = ctx.skill
    goal = _goal(ctx, skill)
    admitted = admit(ctx)
    set_status(ctx, RunStatus.planning)

    # Bind inputs: a skill declares names; otherwise each input is addressed by its file stem.
    documents: dict[str, list[DocumentContext]] = {}
    if skill is not None:
        for name, assets in bind_inputs(skill, admitted.assets).items():
            documents[name] = index_documents(ctx, assets)
    else:
        for asset in admitted.assets:
            stem = (
                (asset.name or asset.id)
                .rsplit(".", 1)[0]
                .lower()
                .replace(" ", "_")
                .replace("-", "_")
            )
            documents.setdefault(stem, []).extend(index_documents(ctx, [asset]))
    all_docs = [d for docs in documents.values() for d in docs]
    output_schema = inline_schema(ctx.request.output_schema) or (
        skill.output_schema if skill else None
    )
    selection = plan_models(ctx, all_docs, output_schema)
    spec = ctx.gateway.registry.get(selection.candidates[0])

    available = skills.list()
    hinted = _skill_match(goal, available) if skill is None else None
    usable_skills = [
        s for s in available if s.run is not None and (skill is None or s.name != skill.name)
    ]
    if hinted is not None:
        goal = f"{goal}\n\n(The skill `skill.{hinted.name}` matches this goal; prefer calling it.)"
    allowed: list[list[str] | None] = [
        skill.manifest.tools if skill is not None else None,
        ctx.request.tools,
        ctx.request.policy.allowed_tools,
    ]
    inputs_assets = {name: docs[0].asset for name, docs in documents.items() if docs}
    cache = (
        PlanCache(Path(ctx.config.home) / "plans") if ctx.request.policy.planning.cache else None
    )
    planned = make_plan(
        ctx,
        goal=goal,
        inputs=inputs_assets,
        spec=spec,
        candidates=selection.candidates,
        registry=tool_registry,
        allowed=allowed,
        skills=usable_skills,
        output_schema=output_schema,
        cache=cache,
    )
    plan = planned.plan
    art = ctx.artifacts.put_json(
        plan.model_dump(mode="json", by_alias=True), kind=ArtifactKind.other, run_id=ctx.run_id
    )
    ctx.artifact_refs.append(art.ref())
    envelope = PlanEnvelope(
        id=new_id("plan"),
        run_id=ctx.run_id,
        mode=ExecutionMode.agent,
        goal=plan.goal,
        nodes=[n.model_dump(mode="json", by_alias=True) for n in plan.nodes],
        source="cached" if planned.cached else "planner",
        created_at=now(),
    )
    ctx.emitter.emit(
        EventType.plan_created,
        {
            "mode": "agent",
            "plan_id": envelope.id,
            "artifact_id": art.id,
            "source": envelope.source,
            "attempts": planned.attempts,
            "nodes": [{"id": n.id, "kind": n.kind.value, "tool": n.tool} for n in plan.nodes],
            "candidates": selection.candidates,
            "skill_hint": hinted.name if hinted else None,
        },
    )
    set_status(ctx, RunStatus.running)
    pages = sum(len(d.index.pages) for d in all_docs if d.index is not None)

    runner = PlanRunner(
        ctx,
        plan,
        documents=documents,
        inputs={name: a.id for name, a in inputs_assets.items()},
        spec=spec,
        candidates=selection.candidates,
        skills={s.name: s for s in available},
    )
    outcome = runner.run()
    extraction = Extraction(
        result=outcome.result, field_meta=outcome.field_meta, evidence=outcome.evidence
    )

    set_status(ctx, RunStatus.validating)
    report = ValidationReport()
    if output_schema is not None and outcome.status == "success":
        validators = build_validators(
            skill.manifest.validators if skill else [], local=skill.validators if skill else None
        )
        report = validate_extraction(ctx, extraction, output_schema, validators)
    report.issues.extend(outcome.issues)
    for issue in outcome.issues:
        if issue.field and issue.field in extraction.field_meta:
            extraction.field_meta[issue.field].issues.append(issue.id)
    if outcome.status == "failure":
        ctx.warn("plan_failure_exit", outcome.reason or "the plan took its failure exit")
        status = (
            RunStatus.needs_review if ctx.request.policy.review_on_failure else RunStatus.rejected
        )
    else:
        status = decide(report, ctx.request.policy)
        if status == RunStatus.repairing:
            status = (
                RunStatus.needs_review
                if ctx.request.policy.review_on_failure
                else RunStatus.rejected
            )
    result = finalize(
        ctx,
        status=status,
        extraction=extraction,
        report=report,
        mode=ExecutionMode.agent,
        pages=pages,
    )
    result.plan_id = envelope.id
    return result


def plan_payload(result_outputs: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in result_outputs.items()}


__all__ = ["run_agent"]
