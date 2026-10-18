"""Usage and cost accounting (blueprint §9, §30).

Run-level totals are *derived* from the per-call records, never maintained as separate
counters, so they always reconcile.
"""

from __future__ import annotations

from pydantic import Field

from hawser.ids import StepId
from hawser.models.base import HawserModel


class ModelCallUsage(HawserModel):
    step_id: StepId | None = None
    provider: str
    model: str
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cache_read_tokens: int = Field(default=0, ge=0)
    cache_write_tokens: int = Field(default=0, ge=0)
    cost_usd: float = Field(ge=0)
    pricing_version: str | None = None
    latency_ms: int = Field(ge=0)
    attempts: int = Field(default=1, ge=1)
    #: True when served from a recording cassette; cost is then 0 and must not be billed.
    replayed: bool = False
    provider_request_id: str | None = None


class ToolCallUsage(HawserModel):
    step_id: StepId | None = None
    tool: str
    version: str | None = None
    cost_usd: float = Field(default=0, ge=0)
    latency_ms: int = Field(ge=0)
    cpu_ms: int | None = Field(default=None, ge=0)
    attempts: int = Field(default=1, ge=1)
    replayed: bool = False


class UsageTotals(HawserModel):
    cost_usd: float = Field(default=0, ge=0)
    model_calls: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    pages_processed: int = Field(default=0, ge=0)
    pixels_decoded: int = Field(default=0, ge=0)
    media_duration_ms: int = Field(default=0, ge=0)
    sampled_frames: int = Field(default=0, ge=0)
    repair_attempts: int = Field(default=0, ge=0)
    sandbox_runs: int = Field(default=0, ge=0)
    wall_time_ms: int = Field(default=0, ge=0)


class Usage(HawserModel):
    model_calls: list[ModelCallUsage] = []
    tool_calls: list[ToolCallUsage] = []
    totals: UsageTotals = UsageTotals()
    models_used: list[str] = []
    #: Hash of the model registry snapshot used for pricing and capabilities.
    registry_version: str | None = None

    def recompute_totals(self, **extra: int) -> UsageTotals:
        """Derive ``totals`` from the call lists. Non-call counters are passed in ``extra``."""
        totals = UsageTotals(
            cost_usd=sum(c.cost_usd for c in self.model_calls)
            + sum(t.cost_usd for t in self.tool_calls),
            model_calls=len(self.model_calls),
            tool_calls=len(self.tool_calls),
            input_tokens=sum(c.input_tokens for c in self.model_calls),
            output_tokens=sum(c.output_tokens for c in self.model_calls),
            **extra,
        )
        self.totals = totals
        self.models_used = sorted({f"{c.provider}/{c.model}" for c in self.model_calls})
        return totals


__all__ = ["ModelCallUsage", "ToolCallUsage", "Usage", "UsageTotals"]
