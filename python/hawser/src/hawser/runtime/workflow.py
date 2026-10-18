"""Workflow mode: run a skill's ``run(ctx)`` with checkpointed steps, then validate and decide."""

from __future__ import annotations

from hawser.errors import PlanInvalid
from hawser.models import (
    Asset,
    EventType,
    ExecutionMode,
    ModelSpec,
    RunResult,
    RunStatus,
    StepKind,
)
from hawser.runtime.context import DocumentContext, RunContext
from hawser.runtime.direct import admit, finalize, index_documents, plan_models, set_status
from hawser.runtime.extract import Extraction
from hawser.runtime.repair import validate_extraction
from hawser.skills.bundle import Skill
from hawser.validation import decide
from hawser.validation.registry import build_validators
from hawser.validation.rules import EvidenceRequiredValidator
from hawser.workflow.context import WorkflowContext, WorkflowOutput


def bind_inputs(skill: Skill, assets: list[Asset]) -> dict[str, list[Asset]]:
    """Map admitted assets to the skill's declared inputs: by name match, then by position."""
    declared = list(skill.manifest.inputs.items())
    bound: dict[str, list[Asset]] = {name: [] for name, _ in declared}
    unbound: list[Asset] = []
    for asset in assets:
        label = (asset.name or "").lower()
        stem = label.rsplit(".", 1)[0]
        hit = next(
            (name for name, _ in declared if stem == name or stem.startswith(name) or name in stem),
            None,
        )
        if hit is not None:
            bound[hit].append(asset)
        else:
            unbound.append(asset)
    for name, _spec in declared:
        if bound[name]:
            continue
        if unbound:
            bound[name].append(unbound.pop(0))
    for name, spec in declared:
        if spec.required and not bound[name]:
            raise PlanInvalid(
                f"skill {skill.name!r} needs input {name!r}: "
                "name a file after it or pass inputs in order",
                code="input_missing",
                details={
                    "declared": [n for n, _ in declared],
                    "received": [a.name for a in assets],
                },
            )
        if not spec.multiple and len(bound[name]) > 1:
            bound[name] = bound[name][:1]
    return bound


def execute_skill(
    ctx: RunContext,
    skill: Skill,
    documents: dict[str, list[DocumentContext]],
    spec: ModelSpec,
    candidates: list[str],
    *,
    prefix: str = "workflow",
) -> WorkflowOutput:
    """Run a skill's ``run(ctx)`` as one checkpointed step tree under ``prefix``.

    Used by workflow mode and by agent plans that call ``skill.<name>``.
    """
    run_fn = skill.run
    if run_fn is None:
        raise PlanInvalid(f"skill {skill.name!r} has no workflow", code="skill_workflow_missing")
    wctx = WorkflowContext(ctx, skill, documents, spec, candidates=candidates, prefix=prefix)
    return ctx.steps.run(
        prefix,
        f"workflow {skill.name}",
        StepKind.workflow,
        lambda h: wctx.assemble(run_fn(wctx)),
        output_type=WorkflowOutput,  # type: ignore[misc]
    )


def run_workflow(ctx: RunContext, skill: Skill) -> RunResult:
    if skill.run is None:
        raise PlanInvalid(f"skill {skill.name!r} has no workflow", code="skill_workflow_missing")

    admitted = admit(ctx)
    set_status(ctx, RunStatus.planning)
    bound = bind_inputs(skill, admitted.assets)
    documents: dict[str, list[DocumentContext]] = {}
    for name, assets in bound.items():
        documents[name] = index_documents(ctx, assets)
    all_docs = [d for docs in documents.values() for d in docs]
    selection = plan_models(ctx, all_docs, skill.output_schema)
    spec = ctx.gateway.registry.get(selection.candidates[0])
    ctx.emitter.emit(
        EventType.plan_created,
        {
            "mode": "workflow",
            "skill": skill.name,
            "version": skill.version,
            "candidates": selection.candidates,
            "source": "skill",
            "inputs": {k: [a.id for a in v] for k, v in bound.items()},
        },
    )
    set_status(ctx, RunStatus.running)
    pages = sum(len(d.index.pages) for d in all_docs if d.index is not None)

    output = execute_skill(ctx, skill, documents, spec, selection.candidates)
    extraction = Extraction(
        result=output.result, field_meta=output.field_meta, evidence=output.evidence
    )

    set_status(ctx, RunStatus.validating)
    validators = build_validators(skill.manifest.validators, local=skill.validators)
    ev = skill.manifest.evidence
    if ev.required or ev.preferred:
        validators.append(EvidenceRequiredValidator(list(ev.required), list(ev.preferred)))
    report = validate_extraction(ctx, extraction, skill.output_schema, validators)
    report.issues.extend(output.issues)
    for issue in output.issues:
        if issue.field and issue.field in extraction.field_meta:
            extraction.field_meta[issue.field].issues.append(issue.id)
    status = decide(report, ctx.request.policy)
    if status == RunStatus.repairing:
        # Workflow-level repair (re-running named steps) is Phase 4 territory; route per policy.
        ctx.warn("repair_unavailable", "workflow results are routed to review instead of repaired")
        status = (
            RunStatus.needs_review if ctx.request.policy.review_on_failure else RunStatus.rejected
        )
    return finalize(
        ctx,
        status=status,
        extraction=extraction,
        report=report,
        mode=ExecutionMode.workflow,
        pages=pages,
    )


__all__ = ["bind_inputs", "execute_skill", "run_workflow"]
