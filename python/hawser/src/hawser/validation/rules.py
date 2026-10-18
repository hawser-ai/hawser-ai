"""Declarative validators configured from skill manifests or policies."""

from __future__ import annotations

import fnmatch
import re
from datetime import date
from typing import Any

from hawser.models import FieldStatus, Issue, RepairKind, Severity
from hawser.validation.core import ValidationContext, flatten_values, make_issue
from hawser.validation.expr import ExpressionError, evaluate

_ISO4217 = re.compile(r"^[A-Z]{3}$")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class ExpressionRule:
    """One rule: ``expr`` must evaluate truthy over the result document."""

    def __init__(
        self,
        name: str,
        expr: str,
        *,
        message: str | None = None,
        severity: Severity = Severity.error,
        field: str | None = None,
        repair: RepairKind = RepairKind.review,
    ) -> None:
        self.name = name
        self.expr = expr
        self.message = message or f"rule failed: {expr}"
        self.severity = severity
        self.field = field
        self.repair = repair


class ExpressionValidator:
    """``rules.expressions``: arithmetic and cross-field checks (blueprint §16)."""

    id = "rules.expressions"
    version = "1"

    def __init__(self, rules: list[ExpressionRule]) -> None:
        self.rules = rules

    def validate(self, ctx: ValidationContext) -> list[Issue]:
        if not isinstance(ctx.result, dict):
            return []
        issues: list[Issue] = []
        for rule in self.rules:
            try:
                ok = evaluate(rule.expr, ctx.result)  # type: ignore[arg-type]
            except ExpressionError as exc:
                issues.append(
                    make_issue(
                        f"{self.id}.{rule.name}",
                        Severity.warning,
                        f"rule could not be evaluated: {exc}",
                    )
                )
                continue
            if not ok:
                issues.append(
                    make_issue(
                        f"{self.id}.{rule.name}",
                        rule.severity,
                        rule.message,
                        field_ptr=rule.field,
                        repair=rule.repair,
                        expression=rule.expr,
                    )
                )
        return issues


def _check_format(value: Any, fmt: str) -> str | None:  # noqa: PLR0911
    """Return an error message, or None when ``value`` satisfies ``fmt``."""
    if value is None:
        return None
    if fmt == "date":
        try:
            date.fromisoformat(str(value))
        except ValueError:
            return f"{value!r} is not an ISO date (YYYY-MM-DD)"
        return None
    if fmt == "iso4217":
        return None if _ISO4217.match(str(value)) else f"{value!r} is not an ISO 4217 currency code"
    if fmt == "email":
        return None if _EMAIL.match(str(value)) else f"{value!r} is not an email address"
    if fmt == "positive":
        try:
            return None if float(value) > 0 else f"{value!r} is not positive"
        except (TypeError, ValueError):
            return f"{value!r} is not a number"
    if fmt == "non_negative":
        try:
            return None if float(value) >= 0 else f"{value!r} is negative"
        except (TypeError, ValueError):
            return f"{value!r} is not a number"
    if fmt == "integer":
        try:
            return None if float(value) == int(float(value)) else f"{value!r} is not an integer"
        except (TypeError, ValueError):
            return f"{value!r} is not a number"
    if fmt.startswith("regex:"):
        pattern = fmt[len("regex:") :]
        return None if re.fullmatch(pattern, str(value)) else f"{value!r} does not match {pattern}"
    return f"unknown format {fmt!r}"


def pointer_matches(pattern: str, ptr: str) -> bool:
    """Glob-style pointer patterns: ``/line_items/*/sku`` matches any index."""
    return fnmatch.fnmatchcase(ptr, pattern)


class FormatValidator:
    """``rules.formats``: ``{pointer_pattern: format}`` checks on leaf values."""

    id = "rules.formats"
    version = "1"

    def __init__(self, formats: dict[str, str], *, severity: Severity = Severity.error) -> None:
        self.formats = formats
        self.severity = severity

    def validate(self, ctx: ValidationContext) -> list[Issue]:
        issues: list[Issue] = []
        for ptr, value in flatten_values(ctx.result).items():
            for pattern, fmt in self.formats.items():
                if pointer_matches(pattern, ptr):
                    msg = _check_format(value, fmt)
                    if msg:
                        issues.append(
                            make_issue(
                                self.id,
                                self.severity,
                                msg,
                                field_ptr=ptr,
                                repair=RepairKind.ask_field,
                                format=fmt,
                            )
                        )
        return issues


class EvidenceRequiredValidator:
    """``rules.evidence_required``: listed fields must carry anchored evidence (error)."""

    id = "rules.evidence_required"
    version = "1"

    def __init__(self, required: list[str], preferred: list[str] | None = None) -> None:
        self.required = required
        self.preferred = preferred or []

    def validate(self, ctx: ValidationContext) -> list[Issue]:
        issues: list[Issue] = []
        for ptr, meta in ctx.field_meta.items():
            if meta.status != FieldStatus.found or meta.evidence:
                continue
            if any(pointer_matches(p, ptr) for p in self.required):
                issues.append(
                    make_issue(
                        self.id,
                        Severity.error,
                        "required evidence is missing",
                        field_ptr=ptr,
                        repair=RepairKind.retrieve,
                    )
                )
            elif any(pointer_matches(p, ptr) for p in self.preferred):
                issues.append(
                    make_issue(
                        self.id,
                        Severity.warning,
                        "preferred evidence is missing",
                        field_ptr=ptr,
                        repair=RepairKind.retrieve,
                    )
                )
        return issues


class UnknownBudgetValidator:
    """``core.unknown_budget``: too many unknown leaves (policy ``max_unknown_fraction``)."""

    id = "core.unknown_budget"
    version = "1"

    def validate(self, ctx: ValidationContext) -> list[Issue]:
        limit = ctx.policy.validation.max_unknown_fraction
        if limit is None or not ctx.field_meta:
            return []
        present = (FieldStatus.found, FieldStatus.derived)
        unknown = sum(1 for m in ctx.field_meta.values() if m.status not in present)
        frac = unknown / len(ctx.field_meta)
        if frac > limit:
            return [
                make_issue(
                    self.id,
                    Severity.error,
                    f"{unknown}/{len(ctx.field_meta)} fields unknown ({frac:.0%} > {limit:.0%})",
                    repair=RepairKind.reexecute,
                    unknown=unknown,
                    total=len(ctx.field_meta),
                )
            ]
        return []


__all__ = [
    "EvidenceRequiredValidator",
    "ExpressionRule",
    "ExpressionValidator",
    "FormatValidator",
    "UnknownBudgetValidator",
    "pointer_matches",
]
