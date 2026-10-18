"""Tool specifications (blueprint §11). The broker and registry arrive in Phase 3."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from hawser.models.base import HawserModel
from hawser.models.common import JsonSchema, SemVer, Slug


class SideEffect(StrEnum):
    none = "none"  # pure computation over inputs
    local_artifact = "local_artifact"  # writes artifacts only
    external_read = "external_read"  # calls out (hosted OCR, search) without mutation
    external_write = "external_write"  # mutates an external system; requires approval


class RetryClass(StrEnum):
    never = "never"
    safe = "safe"  # idempotent by nature
    idempotent_with_key = "idempotent_with_key"  # safe only when an action_id is supplied


class ToolLocation(StrEnum):
    inprocess = "inprocess"
    worker = "worker"
    remote = "remote"
    sandbox = "sandbox"


class ToolLimits(HawserModel):
    cpu_s: float | None = Field(default=None, ge=0)
    memory_mb: int | None = Field(default=None, ge=0)
    wall_s: float | None = Field(default=None, ge=0)
    max_input_bytes: int | None = Field(default=None, ge=0)
    max_output_bytes: int | None = Field(default=None, ge=0)


class CostEstimate(HawserModel):
    fixed_usd: float = Field(default=0, ge=0)
    per_page_usd: float = Field(default=0, ge=0)
    per_minute_usd: float = Field(default=0, ge=0)
    per_megapixel_usd: float = Field(default=0, ge=0)


class ToolSpec(HawserModel):
    name: Slug
    version: SemVer
    description: str
    input_schema: JsonSchema
    output_schema: JsonSchema
    modalities: list[str] = []
    #: Permission strings, e.g. ``document.read``, ``network.egress:api.example.com``.
    permissions: list[str] = []
    side_effect: SideEffect = SideEffect.none
    limits: ToolLimits = ToolLimits()
    cost_estimate: CostEstimate | None = None
    retry: RetryClass = RetryClass.safe
    idempotency: str = Field(default="pure", pattern=r"^(pure|keyed|none)$")
    location: ToolLocation = ToolLocation.inprocess
    #: Whether the tool can return evidence objects.
    produces_evidence: bool = False


__all__ = ["CostEstimate", "RetryClass", "SideEffect", "ToolLimits", "ToolLocation", "ToolSpec"]
