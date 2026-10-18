"""Budget enforcement (blueprint §10.3, plan WP3.3).

Checks happen *before* a paid call using a pre-call estimate, and the run's usage totals are
derived from events/steps, never from counters — so they reconcile by construction.
"""

from __future__ import annotations

from dataclasses import dataclass

from hawser.errors import BudgetExceeded, UsageMismatch
from hawser.gateway.base import ImagePart, ModelRequest
from hawser.models import ModelSpec, Policy, Step, Usage
from hawser.runtime.context import estimate_image_tokens, estimate_text_tokens

#: Fraction of ``max_output_tokens`` assumed for the pre-call cost estimate.
EXPECTED_OUTPUT_FRACTION = 0.25


@dataclass
class Estimate:
    input_tokens: int
    output_tokens: int
    cost_usd: float


def estimate_call(request: ModelRequest, spec: ModelSpec) -> Estimate:
    tokens = estimate_text_tokens(request.system or "")
    for m in request.messages:
        for p in m.parts:
            if isinstance(p, ImagePart):
                tokens += estimate_image_tokens(1000, 1000)  # conservative until decoded
            elif p.kind == "text":
                tokens += estimate_text_tokens(p.text)  # type: ignore[union-attr]
    if request.output_schema is not None:
        tokens += estimate_text_tokens(str(request.output_schema))
    out = int(min(request.max_output_tokens, spec.max_output_tokens) * EXPECTED_OUTPUT_FRACTION)
    cost = 0.0
    if spec.pricing is not None:
        cost = (tokens * spec.pricing.input_per_mtok + out * spec.pricing.output_per_mtok) / 1e6
    return Estimate(input_tokens=tokens, output_tokens=out, cost_usd=cost)


def check_before_model_call(
    usage: Usage, policy: Policy, est: Estimate, *, repair_attempts: int = 0
) -> None:
    """Raise ``BudgetExceeded`` if this call would breach a policy limit."""
    lim = policy.limits
    spent = sum(c.cost_usd for c in usage.model_calls) + sum(t.cost_usd for t in usage.tool_calls)
    if lim.max_cost_usd is not None and spent + est.cost_usd > lim.max_cost_usd:
        raise BudgetExceeded(
            f"estimated cost ${spent + est.cost_usd:.4f} exceeds ${lim.max_cost_usd:.4f}",
            code="max_cost_usd",
            details={
                "spent_usd": spent,
                "estimate_usd": est.cost_usd,
                "limit_usd": lim.max_cost_usd,
            },
        )
    if lim.max_model_calls is not None and len(usage.model_calls) + 1 > lim.max_model_calls:
        raise BudgetExceeded(
            f"model call {len(usage.model_calls) + 1} exceeds the limit {lim.max_model_calls}",
            code="max_model_calls",
            details={"calls": len(usage.model_calls), "limit": lim.max_model_calls},
        )
    used_in = sum(c.input_tokens for c in usage.model_calls)
    if lim.max_input_tokens is not None and used_in + est.input_tokens > lim.max_input_tokens:
        raise BudgetExceeded(
            f"input tokens {used_in + est.input_tokens} exceed the limit {lim.max_input_tokens}",
            code="max_input_tokens",
            details={"used": used_in, "estimate": est.input_tokens, "limit": lim.max_input_tokens},
        )
    if repair_attempts > policy.repair.attempts:
        raise BudgetExceeded("repair attempts exhausted", code="repair_attempts")


def check_before_tool_call(usage: Usage, policy: Policy, est_cost: float) -> None:
    lim = policy.limits
    if lim.max_tool_calls is not None and len(usage.tool_calls) + 1 > lim.max_tool_calls:
        raise BudgetExceeded(
            f"tool call {len(usage.tool_calls) + 1} exceeds max_tool_calls {lim.max_tool_calls}",
            code="max_tool_calls",
        )
    spent = sum(c.cost_usd for c in usage.model_calls) + sum(t.cost_usd for t in usage.tool_calls)
    if lim.max_cost_usd is not None and spent + est_cost > lim.max_cost_usd:
        raise BudgetExceeded(
            f"estimated cost ${spent + est_cost:.4f} exceeds max_cost_usd ${lim.max_cost_usd:.4f}",
            code="max_cost_usd",
        )


def reconcile(run_usage: Usage, steps: list[Step]) -> None:
    """The run's usage must equal the sum of its steps' usage (blueprint §30)."""
    step_calls = sum(len(s.usage.model_calls) for s in steps)
    step_cost = sum(c.cost_usd for s in steps for c in s.usage.model_calls) + sum(
        t.cost_usd for s in steps for t in s.usage.tool_calls
    )
    run_cost = sum(c.cost_usd for c in run_usage.model_calls) + sum(
        t.cost_usd for t in run_usage.tool_calls
    )
    if step_calls != len(run_usage.model_calls) or abs(step_cost - run_cost) > 1e-9:
        raise UsageMismatch(
            "run usage does not reconcile with step usage",
            details={
                "run_model_calls": len(run_usage.model_calls),
                "step_model_calls": step_calls,
                "run_cost_usd": run_cost,
                "step_cost_usd": step_cost,
            },
        )


__all__ = [
    "Estimate",
    "check_before_model_call",
    "check_before_tool_call",
    "estimate_call",
    "reconcile",
]
