"""Model selection (blueprint §9, plan WP3.2).

``candidates()`` filters the registry by what the request *needs* (modalities, structured
output, context) and by what the policy *permits*, then ranks by the caller's strategy. The
result is an ordered list: the gateway tries them in order and falls back only within it, so
fallback can never reach a model the policy excluded.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from hawser.errors import PolicyViolation
from hawser.gateway.gateway import Gateway
from hawser.gateway.registry import Registry
from hawser.models import Modality, ModelPreference, ModelSpec, Policy

Strategy = Literal["cheapest", "fastest", "best_quality", "balanced"]


class Requirements(BaseModel):
    modalities: set[Modality] = Field(default_factory=lambda: {Modality.text})
    structured_output: bool = False
    tool_calling: bool = False
    #: Estimated input tokens the call needs to fit (plus headroom for output).
    min_context: int = 0


def capable(spec: ModelSpec, req: Requirements) -> str | None:
    """Return why ``spec`` cannot serve ``req``, or ``None`` when it can."""
    missing = req.modalities - set(spec.modalities)
    if missing:
        return f"lacks modalities {sorted(m.value for m in missing)}"
    if req.structured_output and not spec.structured_output:
        return "no structured output"
    if req.tool_calling and not spec.tool_calling:
        return "no tool calling"
    if req.min_context and spec.context_window < req.min_context:
        return f"context window {spec.context_window} < {req.min_context}"
    return None


def permitted(spec: ModelSpec, policy: Policy) -> str | None:
    try:
        Gateway.check_policy(spec, policy)
    except PolicyViolation as exc:
        return exc.code
    return None


def _cost_score(spec: ModelSpec) -> float:
    """Blended $/Mtok assuming ~4:1 input:output. Local models (no pricing) are free."""
    if spec.pricing is None:
        return 0.0
    return (4 * spec.pricing.input_per_mtok + spec.pricing.output_per_mtok) / 5


def rank(specs: list[ModelSpec], strategy: Strategy) -> list[ModelSpec]:
    if not specs:
        return []
    max_cost = max(_cost_score(s) for s in specs) or 1.0

    def key(s: ModelSpec) -> tuple[float, str]:
        if strategy == "cheapest":
            return (_cost_score(s), s.id)
        if strategy == "fastest":
            return (-s.speed_prior, s.id)
        if strategy == "best_quality":
            return (-s.quality_prior, s.id)
        # balanced: quality minus normalised cost
        return (-(s.quality_prior - 0.5 * _cost_score(s) / max_cost), s.id)

    return sorted(specs, key=key)


class Selection(BaseModel):
    candidates: list[str]
    strategy: str
    #: model id -> reason it was excluded
    excluded: dict[str, str] = Field(default_factory=dict)


def candidates(
    registry: Registry,
    req: Requirements,
    policy: Policy,
    preference: ModelPreference | None,
    *,
    default_model: str,
) -> Selection:
    """Ordered, policy-safe candidate list. Raises ``PolicyViolation`` when nothing qualifies."""
    pref = preference or ModelPreference()
    excluded: dict[str, str] = {}

    def check(spec: ModelSpec) -> bool:
        why = permitted(spec, policy) or capable(spec, req)
        if why:
            excluded[spec.id] = why
            return False
        return True

    if pref.pinned:
        spec = registry.get(pref.pinned)
        if not check(spec):
            raise PolicyViolation(
                f"pinned model {spec.id} cannot be used: {excluded[spec.id]}",
                code="model_pinned_unusable",
                details={"excluded": excluded},
            )
        return Selection(candidates=[spec.id], strategy="pinned", excluded=excluded)

    pool: list[ModelSpec]
    if pref.candidates:
        pool = [registry.get(mid) for mid in pref.candidates]
        ordered = [s for s in pool if check(s)]  # caller's order is kept
        strategy = "explicit"
    else:
        pool = [s for s in registry.all() if s.enabled]
        passing = [s for s in pool if check(s)]
        ordered = rank(passing, pref.strategy)
        strategy = pref.strategy
        # The configured default leads when it qualifies; the ranking decides the rest.
        if default_model in {s.id for s in ordered}:
            ordered = [s for s in ordered if s.id == default_model] + [
                s for s in ordered if s.id != default_model
            ]
    if not ordered:
        raise PolicyViolation(
            "no model satisfies the policy and the request's requirements",
            code="no_model_available",
            details={"excluded": excluded, "requirements": req.model_dump(mode="json")},
        )
    if not policy.fallback.enabled:
        ordered = ordered[:1]
    elif policy.fallback.candidates:
        allowed = set(policy.fallback.candidates)
        ordered = [s for s in ordered if s.id in allowed or s is ordered[0]]
    return Selection(candidates=[s.id for s in ordered], strategy=strategy, excluded=excluded)


__all__ = ["Requirements", "Selection", "Strategy", "candidates", "capable", "permitted", "rank"]
