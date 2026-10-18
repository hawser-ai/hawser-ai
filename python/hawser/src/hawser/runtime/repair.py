"""Bounded repair (blueprint §16, plan WP2.3).

Strategies are chosen from the error-severity issues' ``repair`` kinds:

* ``ask_field`` on top-level fields  -> re-extract only those fields (sub-schema) and merge.
* anything else re-extractable       -> full re-extraction with the issues as corrections.
* ``review`` / ``none``              -> not repairable here.

Bounds: ``policy.repair.attempts``, ``policy.repair.max_cost_usd``, ``policy.repair.max_seconds``.
Equivalent attempts (same issues, same result) stop the loop: ``repair.no_progress``.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass, field

from hawser.clock import monotonic_ms
from hawser.models import EventType, ModelSpec, RepairKind, RunStatus, Severity, ValidationReport
from hawser.models.common import JsonSchema
from hawser.recording import canonical_json
from hawser.runtime.context import DocumentContext, RunContext
from hawser.runtime.extract import Extraction, extract_structured
from hawser.structured.subset import merge_values, subschema, top_level_only
from hawser.validation import ValidationContext, Validator, decide, run_validators

_RE_EXTRACT = {
    RepairKind.retrieve,
    RepairKind.remodel,
    RepairKind.ask_field,
    RepairKind.reexecute,
    RepairKind.reocr,
}


@dataclass
class RepairOutcome:
    extraction: Extraction
    report: ValidationReport
    attempts: int = 0
    stopped_reason: str | None = None
    history: list[dict[str, object]] = field(default_factory=list)


def fingerprint(report: ValidationReport, result: object) -> str:
    issues = sorted(
        (i.rule_id, i.field or "") for i in report.issues if i.severity == Severity.error
    )
    payload = canonical_json({"issues": issues, "result": result})
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def validate_extraction(
    ctx: RunContext, extraction: Extraction, schema: JsonSchema, validators: Sequence[Validator]
) -> ValidationReport:
    vctx = ValidationContext(
        result=extraction.result,
        schema=schema,
        field_meta=extraction.field_meta,
        evidence=extraction.evidence_map(),
        policy=ctx.request.policy,
        parse_issues=extraction.parse_issues,
    )
    report = run_validators(validators, vctx)
    for issue in report.issues:
        if issue.field and issue.field in extraction.field_meta:
            extraction.field_meta[issue.field].issues.append(issue.id)
    ctx.emitter.emit(
        EventType.validation_completed,
        {
            "errors": report.count_errors(),
            "warnings": report.count_warnings(),
            "rules": [r.rule_id for r in report.rules_run],
        },
    )
    return report


def repair_loop(
    ctx: RunContext,
    docs: list[DocumentContext],
    *,
    schema: JsonSchema,
    instruction: str,
    spec: ModelSpec,
    extraction: Extraction,
    report: ValidationReport,
    validators: Sequence[Validator],
    prefix: str = "extract",
    candidates: list[str] | None = None,
) -> RepairOutcome:
    policy = ctx.request.policy
    out = RepairOutcome(extraction=extraction, report=report)
    seen = {fingerprint(report, extraction.result)}
    started = monotonic_ms()
    cost_at_start = sum(c.cost_usd for c in ctx.usage.model_calls)

    while decide(out.report, policy, repair_attempts_used=out.attempts) == RunStatus.repairing:
        if (
            policy.repair.max_seconds is not None
            and (monotonic_ms() - started) / 1000 > policy.repair.max_seconds
        ):
            out.stopped_reason = "time_budget"
            break
        spent = sum(c.cost_usd for c in ctx.usage.model_calls) - cost_at_start
        if policy.repair.max_cost_usd is not None and spent >= policy.repair.max_cost_usd:
            out.stopped_reason = "cost_budget"
            break

        errors = [
            i for i in out.report.issues if i.severity == Severity.error and i.repair in _RE_EXTRACT
        ]
        if not errors:
            out.stopped_reason = "not_repairable"
            break
        out.attempts += 1
        fields = sorted({i.field for i in errors if i.field})
        corrections = [f"{i.field or '(document)'}: {i.message} [{i.rule_id}]" for i in errors]
        strategy = (
            "ask_field"
            if fields
            and top_level_only(fields)
            and all(i.repair == RepairKind.ask_field for i in errors)
            else "re_extract"
        )
        ctx.emitter.emit(
            EventType.repair_started,
            {
                "attempt": out.attempts,
                "strategy": strategy,
                "fields": fields,
                "rules": sorted({i.rule_id for i in errors}),
            },
        )
        step_prefix = f"{prefix}:repair{out.attempts}"
        if strategy == "ask_field":
            partial = extract_structured(
                ctx,
                docs,
                schema=subschema(schema, fields),
                instruction=instruction,
                spec=spec,
                prefix=step_prefix,
                candidates=candidates,
                corrections=corrections,
                previous={f.strip("/"): _get(out.extraction.result, f) for f in fields},
            )
            merged = merge_values(out.extraction.result, partial.result, fields)
            new = Extraction(
                result=merged,
                field_meta={**out.extraction.field_meta, **partial.field_meta},
                evidence=[*out.extraction.evidence, *partial.evidence],
                parse_issues=partial.parse_issues,
                truncated=partial.truncated,
                structured_fallback=partial.structured_fallback,
                model=partial.model,
            )
            for ptr in fields:  # repaired fields drop their old issues
                if ptr in new.field_meta:
                    new.field_meta[ptr].issues = []
        else:
            new = extract_structured(
                ctx,
                docs,
                schema=schema,
                instruction=instruction,
                spec=spec,
                prefix=step_prefix,
                candidates=candidates,
                corrections=corrections,
                previous=out.extraction.result,
            )
        new_report = validate_extraction(ctx, new, schema, validators)
        fp = fingerprint(new_report, new.result)
        out.history.append(
            {
                "attempt": out.attempts,
                "strategy": strategy,
                "errors_before": len(errors),
                "errors_after": new_report.count_errors(),
            }
        )
        if new_report.count_errors() <= out.report.count_errors():
            out.extraction, out.report = new, new_report
        if fp in seen:
            ctx.emitter.emit(
                EventType.repair_no_progress, {"attempt": out.attempts, "fingerprint": fp}
            )
            out.stopped_reason = "no_progress"
            break
        seen.add(fp)
    return out


def _get(data: object, ptr: str) -> object:
    cur = data
    for tok in ptr.split("/")[1:]:
        try:
            cur = cur[int(tok)] if isinstance(cur, list) else cur[tok]  # type: ignore[index]
        except (KeyError, IndexError, TypeError, ValueError):
            return None
    return cur


__all__ = ["RepairOutcome", "fingerprint", "repair_loop", "validate_extraction"]
