"""Build validator lists from manifest/policy configuration."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from hawser.errors import PlanInvalid
from hawser.models import RepairKind, Severity
from hawser.models.skill import ValidatorConfig
from hawser.validation.core import Validator, core_validators
from hawser.validation.expr import ExpressionError, compile_check
from hawser.validation.rules import (
    EvidenceRequiredValidator,
    ExpressionRule,
    ExpressionValidator,
    FormatValidator,
    UnknownBudgetValidator,
)

Factory = Callable[[dict[str, Any]], Validator]


def _expressions(params: dict[str, Any]) -> Validator:
    rules: list[ExpressionRule] = []
    for item in params.get("rules", []):
        raw = {"expr": item} if isinstance(item, str) else item
        if not isinstance(raw, dict) or "expr" not in raw:
            raise PlanInvalid(
                "rules.expressions needs rules with an 'expr'", code="validator_config_invalid"
            )
        expr = str(raw["expr"])
        try:
            compile_check(expr)
        except ExpressionError as exc:
            raise PlanInvalid(
                f"invalid rule expression: {exc}", code="validator_config_invalid"
            ) from exc
        name = str(raw.get("name") or f"rule{len(rules) + 1}")
        rules.append(
            ExpressionRule(
                name,
                expr,
                message=raw.get("message"),
                severity=Severity(raw.get("severity", "error")),
                field=raw.get("field"),
                repair=RepairKind(raw.get("repair", "review")),
            )
        )
    return ExpressionValidator(rules)


def _formats(params: dict[str, Any]) -> Validator:
    formats = params.get("formats") or {k: v for k, v in params.items() if k.startswith("/")}
    return FormatValidator(
        {str(k): str(v) for k, v in formats.items()},
        severity=Severity(params.get("severity", "error")),
    )


def _evidence_required(params: dict[str, Any]) -> Validator:
    return EvidenceRequiredValidator(
        list(params.get("required", [])), list(params.get("preferred", []))
    )


BUILTIN: dict[str, Factory] = {
    "rules.expressions": _expressions,
    "rules.formats": _formats,
    "rules.evidence_required": _evidence_required,
    "core.unknown_budget": lambda _p: UnknownBudgetValidator(),
}


def build_validators(
    configs: list[str | ValidatorConfig] | None = None,
    *,
    local: dict[str, Validator] | None = None,
    include_core: bool = True,
) -> list[Validator]:
    """Core validators + configured built-ins + skill-local validators by id."""
    out: list[Validator] = core_validators() if include_core else []
    out.append(UnknownBudgetValidator())
    local = local or {}
    for cfg in configs or []:
        vid, params = (cfg, {}) if isinstance(cfg, str) else (cfg.id, dict(cfg.params))
        if vid.startswith("core.") and vid in {v.id for v in out}:
            continue
        if vid in local:
            out.append(local[vid])
        elif vid in BUILTIN:
            out.append(BUILTIN[vid](params))
        else:
            raise PlanInvalid(
                f"unknown validator {vid!r}",
                code="validator_unknown",
                details={"known": sorted(BUILTIN)},
            )
    return out


__all__ = ["BUILTIN", "build_validators"]
