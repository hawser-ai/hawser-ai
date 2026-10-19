"""Direct mode (blueprint §3): admit → index → extract → validate → repair → decide."""

from __future__ import annotations

from hawser.clock import now
from hawser.errors import PlanInvalid
from hawser.evidence.resolve import resolve_to_source
from hawser.gateway.select import Requirements, Selection, candidates
from hawser.models import (
    EventType,
    ExecutionMode,
    MediaClass,
    Modality,
    ModelSpec,
    RunResult,
    RunStatus,
    StepKind,
    ValidationReport,
)
from hawser.models.common import JsonSchema
from hawser.runtime.admission import AssetList, admit_inputs
from hawser.runtime.budget import reconcile
from hawser.runtime.context import (
    RESERVED_TOKENS,
    DocumentContext,
    RunContext,
    estimate_text_tokens,
)
from hawser.runtime.extract import Extraction, extract_structured
from hawser.runtime.indexing import index_documents
from hawser.runtime.repair import repair_loop, validate_extraction
from hawser.runtime.state import OUTCOME_STATUSES, can_transition, transition
from hawser.structured import inline_refs
from hawser.validation import core_validators, decide


def requirements_for(docs: list[DocumentContext], schema: JsonSchema | None) -> Requirements:
    """What the model must support to handle these inputs."""
    mods = {Modality.text}
    tokens = 0
    for d in docs:
        if d.index is not None:
            for page in d.index.pages:
                if page.needs_ocr:
                    mods.add(Modality.image)
                    tokens += 1_600
                else:
                    tokens += estimate_text_tokens(page.text)
        elif d.asset.media_class == MediaClass.image:
            mods.add(Modality.image)
            tokens += 1_600
        else:
            tokens += d.asset.size_bytes // 4
    return Requirements(
        modalities=mods,
        structured_output=schema is not None,
        min_context=int(tokens * 1.2) + RESERVED_TOKENS,
    )


def plan_models(
    ctx: RunContext, docs: list[DocumentContext], schema: JsonSchema | None
) -> Selection:
    """Capability- and policy-aware candidate list (plan WP3.2). Raises PolicyViolation."""
    req = requirements_for(docs, schema)
    selection = candidates(
        ctx.gateway.registry,
        req,
        ctx.request.policy,
        ctx.request.models,
        default_model=ctx.config.default_model,
    )
    ctx.emitter.emit(
        EventType.plan_created,
        {
            "mode": (
                ctx.request.mode or (ctx.skill.manifest.mode if ctx.skill else ExecutionMode.direct)
            ).value,
            "candidates": selection.candidates,
            "strategy": selection.strategy,
            "excluded": selection.excluded,
            "requirements": req.model_dump(mode="json"),
            "source": "skill" if ctx.skill is not None else "direct",
        },
    )
    return selection


def select_model(ctx: RunContext) -> ModelSpec:
    """The first policy-safe candidate for text-only structured work (no documents yet)."""
    sel = candidates(
        ctx.gateway.registry,
        Requirements(structured_output=True),
        ctx.request.policy,
        ctx.request.models,
        default_model=ctx.config.default_model,
    )
    return ctx.gateway.registry.get(sel.candidates[0])


def set_status(ctx: RunContext, new: RunStatus) -> None:
    old = ctx.run.status
    if ctx.resuming and not can_transition(old, new):
        return  # a resumed run replays its pipeline; states already passed are not re-entered
    ctx.run.status = transition(old, new, run_id=ctx.run_id)
    ctx.run.updated_at = now()
    if new in OUTCOME_STATUSES:
        ctx.pending_transitions.append((old, new))  # published with the result by the engine
        return
    ctx.meta.put_run(ctx.run)
    ctx.emitter.emit(EventType.run_status_changed, {"from": old.value, "to": new.value})


def admit(ctx: RunContext) -> AssetList:
    set_status(ctx, RunStatus.admitting)
    return ctx.steps.run(
        "admit",
        "Admit inputs",
        StepKind.admit,
        lambda h: admit_inputs(ctx, ctx.request.inputs),
        output_type=AssetList,
    )


def inline_schema(schema: JsonSchema | None) -> JsonSchema | None:
    if schema is None:
        return None
    try:
        return inline_refs(schema)
    except ValueError as exc:
        raise PlanInvalid(
            f"output schema is not supported: {exc}", code="schema_unsupported"
        ) from exc


def finalize(
    ctx: RunContext,
    *,
    status: RunStatus,
    extraction: Extraction,
    report: ValidationReport,
    mode: ExecutionMode,
    pages: int,
) -> RunResult:
    set_status(ctx, status)
    ctx.usage.recompute_totals(pages_processed=pages, repair_attempts=ctx.repair_attempts)
    reconcile(ctx.usage, ctx.meta.list_steps(ctx.run_id))
    return RunResult(
        run_id=ctx.run_id,
        status=status,
        mode=mode,
        result=extraction.result,
        field_meta=extraction.field_meta,
        evidence=resolve_to_source(extraction.evidence),
        validation=report,
        artifacts=list(ctx.artifact_refs),
        usage=ctx.usage,
        trace_id=ctx.run.trace_id,
        warnings=list(ctx.warnings),
        created_at=ctx.run.created_at,
        completed_at=now(),
    )


def run_direct(ctx: RunContext) -> RunResult:
    req = ctx.request
    if req.mode not in (None, ExecutionMode.direct):
        raise PlanInvalid(
            f"mode {req.mode} is not available in direct execution", code="mode_unavailable"
        )
    if not req.instruction:
        raise PlanInvalid("direct mode needs an instruction", code="instruction_missing")

    admitted = admit(ctx)
    set_status(ctx, RunStatus.planning)
    docs = index_documents(ctx, admitted.assets)
    schema = inline_schema(req.output_schema)
    selection = plan_models(ctx, docs, schema)
    spec = ctx.gateway.registry.get(selection.candidates[0])
    set_status(ctx, RunStatus.running)
    pages = sum(len(d.index.pages) for d in docs if d.index is not None)

    extraction = extract_structured(
        ctx,
        docs,
        schema=schema,
        instruction=req.instruction,
        spec=spec,
        candidates=selection.candidates,
    )
    set_status(ctx, RunStatus.validating)
    if schema is None:
        return finalize(
            ctx,
            status=RunStatus.accepted,
            extraction=extraction,
            report=ValidationReport(),
            mode=ExecutionMode.direct,
            pages=pages,
        )

    validators = core_validators()
    report = validate_extraction(ctx, extraction, schema, validators)
    status = decide(report, req.policy)
    if status == RunStatus.repairing:
        set_status(ctx, RunStatus.repairing)
        outcome = repair_loop(
            ctx,
            docs,
            schema=schema,
            instruction=req.instruction,
            spec=spec,
            extraction=extraction,
            report=report,
            validators=validators,
            candidates=selection.candidates,
        )
        extraction, report = outcome.extraction, outcome.report
        ctx.repair_attempts = outcome.attempts
        if outcome.stopped_reason:
            ctx.warn(
                "repair_stopped",
                f"repair stopped: {outcome.stopped_reason}",
                attempts=outcome.attempts,
            )
        set_status(ctx, RunStatus.running)
        set_status(ctx, RunStatus.validating)
        status = decide(
            report,
            req.policy,
            repair_attempts_used=max(outcome.attempts, req.policy.repair.attempts),
        )
    return finalize(
        ctx,
        status=status,
        extraction=extraction,
        report=report,
        mode=ExecutionMode.direct,
        pages=pages,
    )


__all__ = [
    "admit",
    "finalize",
    "index_documents",
    "inline_schema",
    "plan_models",
    "requirements_for",
    "run_direct",
    "select_model",
    "set_status",
]
