"""Validator protocol, core rules, and the deterministic acceptance decision."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from hawser.clock import monotonic_ms
from hawser.evidence.anchor import normalise
from hawser.ids import new_id
from hawser.models import (
    DocumentRegionEvidence,
    Evidence,
    FieldMeta,
    FieldStatus,
    Issue,
    Policy,
    RepairKind,
    RunStatus,
    Severity,
    ValidationReport,
)
from hawser.models.common import JsonSchema
from hawser.models.validation import RuleRun
from hawser.structured.pointers import pointer
from hawser.structured.schema import is_array, is_nullable, is_object, structural_union
from hawser.structured.unwrap import validate_against


@dataclass
class ValidationContext:
    result: Any
    schema: JsonSchema | None
    field_meta: dict[str, FieldMeta]
    evidence: dict[str, Evidence]
    policy: Policy
    parse_issues: list[str] = field(default_factory=list)
    extras: dict[str, Any] = field(default_factory=dict)


class Validator(Protocol):
    id: str
    version: str

    def validate(self, ctx: ValidationContext) -> list[Issue]: ...


def make_issue(
    rule: str,
    severity: Severity,
    message: str,
    *,
    field_ptr: str | None = None,
    repair: RepairKind = RepairKind.none,
    evidence: list[str] | None = None,
    **details: Any,
) -> Issue:
    return Issue(
        id=new_id("iss"),
        rule_id=rule,
        severity=severity,
        field=field_ptr,
        message=message,
        repair=repair,
        evidence=evidence or [],
        details=details,
    )


class SchemaValidator:
    """The result must conform to the caller's schema."""

    id = "core.schema"
    version = "1"

    def validate(self, ctx: ValidationContext) -> list[Issue]:
        if ctx.schema is None:
            return []
        issues = [
            make_issue(
                self.id, Severity.error, msg, field_ptr=ptr or None, repair=RepairKind.ask_field
            )
            for ptr, msg in validate_against(ctx.schema, ctx.result)
        ]
        for p in ctx.parse_issues:
            issues.append(
                make_issue("core.envelope", Severity.error, p, repair=RepairKind.reexecute)
            )
        return issues


class UnknownRequiredValidator:
    """A required, non-nullable leaf the model marked ``unknown`` or ``not_applicable``."""

    id = "core.unknown_required"
    version = "1"

    def validate(self, ctx: ValidationContext) -> list[Issue]:
        if ctx.schema is None:
            return []
        issues: list[Issue] = []
        for ptr, nullable, required in _leaves(ctx.schema, ctx.result):
            meta = ctx.field_meta.get(ptr)
            if meta is None or meta.status in (FieldStatus.found, FieldStatus.derived):
                continue
            if required and not nullable:
                issues.append(
                    make_issue(
                        self.id,
                        Severity.error,
                        f"required field is {meta.status.value}",
                        field_ptr=ptr,
                        repair=RepairKind.ask_field,
                    )
                )
        return issues


class EvidencePresentValidator:
    """Found values should carry anchored evidence. Severity follows policy (warning by default)."""

    id = "core.evidence_missing"
    version = "1"

    def validate(self, ctx: ValidationContext) -> list[Issue]:
        issues: list[Issue] = []
        for ptr, meta in ctx.field_meta.items():
            if meta.status == FieldStatus.found and not meta.evidence:
                issues.append(
                    make_issue(
                        self.id,
                        Severity.warning,
                        "no evidence anchored for this value",
                        field_ptr=ptr,
                        repair=RepairKind.retrieve,
                    )
                )
        return issues


_NUM_RE = re.compile(r"[-+]?\d[\d,.\s']*\d|\d")


def _value_tokens(value: Any) -> list[str]:
    if isinstance(value, bool) or value is None:
        return []
    if isinstance(value, int | float):
        s = f"{value:.2f}".rstrip("0").rstrip(".") if isinstance(value, float) else str(value)
        return [s.replace(",", ""), str(value)]
    return [str(value)]


class EvidenceConsistentValidator:
    """The anchored text should contain the value (normalised). Mismatch is a warning."""

    id = "core.evidence_consistent"
    version = "1"

    def validate(self, ctx: ValidationContext) -> list[Issue]:
        issues: list[Issue] = []
        values = flatten_values(ctx.result)
        for ptr, meta in ctx.field_meta.items():
            if meta.status != FieldStatus.found or not meta.evidence or ptr not in values:
                continue
            value = values[ptr]
            tokens = _value_tokens(value)
            if not tokens:
                continue
            ok = False
            for ev_id in meta.evidence:
                ev = ctx.evidence.get(ev_id)
                text = getattr(ev, "text", None) if isinstance(ev, DocumentRegionEvidence) else None
                if not text:
                    continue
                tn, _ = normalise(text)
                digits = re.sub(r"[^\d.]", "", tn)
                for tok in tokens:
                    tkn, _ = normalise(tok)
                    if tkn and (
                        tkn in tn
                        or (isinstance(value, int | float) and re.sub(r"[^\d.]", "", tkn) in digits)
                    ):
                        ok = True
                        break
                if ok:
                    break
            if not ok:
                issues.append(
                    make_issue(
                        self.id,
                        Severity.warning,
                        "anchored text does not contain the extracted value",
                        field_ptr=ptr,
                        repair=RepairKind.ask_field,
                        evidence=list(meta.evidence),
                        value=value,
                    )
                )
        return issues


def flatten_values(
    data: Any, path: list[str | int] | None = None, out: dict[str, Any] | None = None
) -> dict[str, Any]:
    out = {} if out is None else out
    path = path or []
    if isinstance(data, dict):
        for k, v in data.items():  # type: ignore[union-attr]
            flatten_values(v, [*path, str(k)], out)
    elif isinstance(data, list):
        for i, v in enumerate(data):  # type: ignore[arg-type]
            flatten_values(v, [*path, i], out)
    else:
        out[pointer(path)] = data
    return out


def _leaves(schema: JsonSchema, data: Any) -> list[tuple[str, bool, bool]]:
    """``(pointer, nullable, required)`` for every leaf position present in ``data``'s shape."""
    out: list[tuple[str, bool, bool]] = []

    def walk(node: JsonSchema, value: Any, path: list[str | int], required: bool) -> None:
        structural = structural_union(node)
        if structural is not None:
            if value is None:
                return
            walk(structural, value, path, required)
            return
        if is_object(node):
            props: dict[str, Any] = node.get("properties", {}) or {}
            req = set(node.get("required", []) or [])
            if not isinstance(value, dict):
                return
            value_d: dict[str, Any] = value  # type: ignore[assignment]
            for k, sub in props.items():
                walk(sub, value_d.get(k), [*path, k], k in req)
            return
        if is_array(node):
            if not isinstance(value, list):
                return
            items: JsonSchema = node.get("items", {}) or {}
            value_l: list[Any] = value  # type: ignore[assignment]
            for i, v in enumerate(value_l):
                walk(items, v, [*path, i], True)
            return
        out.append((pointer(path), is_nullable(node), required))

    walk(schema, data, [], True)
    return out


def core_validators() -> list[Validator]:
    return [
        SchemaValidator(),
        UnknownRequiredValidator(),
        EvidencePresentValidator(),
        EvidenceConsistentValidator(),
    ]


def run_validators(validators: Sequence[Validator], ctx: ValidationContext) -> ValidationReport:
    report = ValidationReport()
    started = monotonic_ms()
    for v in validators:
        t0 = monotonic_ms()
        issues = v.validate(ctx)
        for issue in issues:
            issue.rule_version = issue.rule_version or v.version
            issue.severity = _apply_policy(issue, ctx.policy)
        report.issues.extend(issues)
        report.rules_run.append(
            RuleRun(
                rule_id=v.id, version=v.version, duration_ms=monotonic_ms() - t0, issues=len(issues)
            )
        )
    report.duration_ms = monotonic_ms() - started
    return report


def _apply_policy(issue: Issue, policy: Policy) -> Severity:
    if issue.rule_id in policy.validation.escalate:
        return policy.validation.escalate[issue.rule_id]
    short = issue.rule_id.rsplit(".", 1)[-1]
    if short in policy.validation.escalate:
        return policy.validation.escalate[short]
    if issue.rule_id in policy.validation.downgrade:
        return policy.validation.downgrade[issue.rule_id]
    if short in policy.validation.downgrade:
        return policy.validation.downgrade[short]
    return issue.severity


def decide(report: ValidationReport, policy: Policy, *, repair_attempts_used: int = 0) -> RunStatus:
    """Deterministic acceptance (plan WP2.2). Repair routing is wired in Phase 2."""
    if not report.has_errors:
        return RunStatus.accepted
    repairable = any(
        i.severity == Severity.error and i.repair not in (RepairKind.none, RepairKind.review)
        for i in report.issues
    )
    if repairable and repair_attempts_used < policy.repair.attempts:
        return RunStatus.repairing
    return RunStatus.needs_review if policy.review_on_failure else RunStatus.rejected


__all__ = [
    "EvidenceConsistentValidator",
    "EvidencePresentValidator",
    "SchemaValidator",
    "UnknownRequiredValidator",
    "ValidationContext",
    "Validator",
    "core_validators",
    "decide",
    "run_validators",
]
