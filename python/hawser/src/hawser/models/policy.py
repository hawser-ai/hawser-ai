"""Execution policy (blueprint §10.3, §9, §16, §18).

The policy is fixed at admission and immutable for the life of a run. Nothing a model or a
tool returns can change it.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import Field

from hawser.models.base import HawserModel


class ExecutionMode(StrEnum):
    direct = "direct"
    workflow = "workflow"
    agent = "agent"


class Severity(StrEnum):
    error = "error"
    warning = "warning"
    info = "info"


class Limits(HawserModel):
    """Hard caps. ``None`` means "use the deployment default"."""

    max_cost_usd: float | None = Field(default=None, ge=0)
    max_model_calls: int | None = Field(default=None, ge=0)
    max_tool_calls: int | None = Field(default=None, ge=0)
    max_input_tokens: int | None = Field(default=None, ge=0)
    max_output_tokens: int | None = Field(default=None, ge=0)
    max_pages: int | None = Field(default=None, ge=0)
    max_pixels: int | None = Field(default=None, ge=0)
    max_media_duration_ms: int | None = Field(default=None, ge=0)
    max_sampled_frames: int | None = Field(default=None, ge=0)
    max_plan_steps: int | None = Field(default=None, ge=0)
    max_loop_iterations: int | None = Field(default=None, ge=0)
    max_parallel: int | None = Field(default=None, ge=1)
    max_sandbox_runs: int | None = Field(default=None, ge=0)
    max_wall_time_s: int | None = Field(default=None, ge=0)


class RepairPolicy(HawserModel):
    attempts: int = Field(default=1, ge=0)
    max_cost_usd: float | None = Field(default=None, ge=0)
    max_seconds: int | None = Field(default=None, ge=0)


class PrivacyPolicy(HawserModel):
    #: Forbid hosted providers entirely (local / self-hosted models only).
    no_hosted: bool = False
    allowed_regions: list[str] | None = None
    #: Forbid providers that retain data for training or beyond the request.
    no_data_retention: bool = False


class FallbackPolicy(HawserModel):
    enabled: bool = True
    #: Ordered candidates; ``None`` lets the selector choose among policy-permitted models.
    candidates: list[str] | None = None


class ValidationPolicy(HawserModel):
    #: Raise the severity of named rules (``{"evidence_missing": "error"}``).
    escalate: dict[str, Severity] = {}
    #: Lower the severity of named rules.
    downgrade: dict[str, Severity] = {}
    #: Model-based judges are off unless explicitly enabled.
    allow_model_judges: bool = False
    #: Maximum fraction of leaf fields allowed to be ``unknown`` before an issue is raised.
    max_unknown_fraction: float | None = Field(default=None, ge=0, le=1)


class ApprovalPolicy(HawserModel):
    #: Side-effecting tools require an approval record unless matched by a pre-approval.
    require_for_external_write: bool = True
    #: Glob-style patterns ``tool_name:action`` that are pre-approved.
    pre_approved: list[str] = []


class PlanningPolicy(HawserModel):
    #: How many times the planner may retry after a validation failure.
    max_replans: int = Field(default=2, ge=0)
    #: Reuse a validated plan for the same goal/tools/policy/inputs shape.
    cache: bool = True
    #: Per-model-call token budget for agent context selection (``None`` = runtime default).
    context_tokens: int | None = Field(default=None, ge=500)


class Policy(HawserModel):
    mode: ExecutionMode | None = None
    planning: PlanningPolicy = PlanningPolicy()
    limits: Limits = Limits()
    repair: RepairPolicy = RepairPolicy()
    review_on_failure: bool = True
    allowed_models: list[str] | None = None
    allowed_providers: list[str] | None = None
    allowed_tools: list[str] | None = None
    privacy: PrivacyPolicy = PrivacyPolicy()
    fallback: FallbackPolicy = FallbackPolicy()
    validation: ValidationPolicy = ValidationPolicy()
    approvals: ApprovalPolicy = ApprovalPolicy()
    code_mode: Literal["disabled", "enabled"] = "disabled"
    generation: Literal["disabled", "enabled"] = "disabled"
    #: Public URL ingestion is off by default (SSRF, reproducibility).
    allow_public_urls: bool = False


__all__ = [
    "ApprovalPolicy",
    "ExecutionMode",
    "FallbackPolicy",
    "Limits",
    "PlanningPolicy",
    "Policy",
    "PrivacyPolicy",
    "RepairPolicy",
    "Severity",
    "ValidationPolicy",
]
