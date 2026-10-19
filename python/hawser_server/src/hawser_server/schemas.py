"""Request and response bodies that are specific to the HTTP API (the rest are contract models)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from hawser.models import Approval, RunStatus, RunSummary


class RunPage(BaseModel):
    items: list[RunSummary]
    next: str | None = None


class CancelResponse(BaseModel):
    run_id: str
    cancelled: bool
    status: RunStatus


class ReviewOpenRequest(BaseModel):
    assigned_to: str | None = None


class ReviewEditRequest(BaseModel):
    changes: dict[str, Any]
    reviewer: str = "api"
    comment: str | None = None


class ReviewDecideRequest(BaseModel):
    reviewer: str = "api"
    comment: str | None = None


class CurrentResult(BaseModel):
    run_id: str
    result: Any = None


class ApprovalDecideRequest(BaseModel):
    approve: bool
    by: str = "api"
    comment: str | None = None
    #: Re-queue the run once no approvals are pending (the default).
    resume: bool = True


class ApprovalDecided(BaseModel):
    approval: Approval
    resumed: bool


class WebhookCreate(BaseModel):
    url: str = Field(pattern=r"^https?://")
    events: list[str] = []


class WebhookOut(BaseModel):
    id: str
    url: str
    events: list[str]
    active: bool
    created_at: str


class WebhookCreated(BaseModel):
    webhook: WebhookOut
    #: Shown once. Verify ``Hawser-Signature`` with it.
    secret: str


class DeliveryOut(BaseModel):
    id: str
    webhook_id: str
    run_id: str | None
    event: str
    state: str
    attempts: int
    next_attempt_at: str
    last_status: int | None
    last_error: str | None
    created_at: str
    delivered_at: str | None


class ArtifactUrl(BaseModel):
    artifact_id: str
    url: str
    expires_in_s: int | None = None


class UsageReport(BaseModel):
    tenant_id: str
    since: str | None
    runs: int
    model_calls: int
    input_tokens: int
    output_tokens: int
    cost_usd: float


class UsageBucket(BaseModel):
    key: str
    runs: int = 0
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    model_calls: int = 0


class UsageRun(BaseModel):
    id: str
    status: str
    skill: str | None
    created_at: str
    cost_usd: float
    max_cost_usd: float | None
    model_calls: int


class UsageBreakdown(BaseModel):
    tenant_id: str
    since: str | None
    by_day: list[UsageBucket] = []
    by_skill: list[UsageBucket] = []
    by_model: list[UsageBucket] = []
    by_status: list[UsageBucket] = []
    runs: list[UsageRun] = []


class Health(BaseModel):
    status: str
    version: str
    database: bool | None = None
    jobs: dict[str, int] = {}


class TenantOut(BaseModel):
    id: str
    name: str
    created_at: str


__all__ = [
    "ApprovalDecideRequest",
    "ApprovalDecided",
    "ArtifactUrl",
    "CancelResponse",
    "CurrentResult",
    "DeliveryOut",
    "Health",
    "ReviewDecideRequest",
    "ReviewEditRequest",
    "ReviewOpenRequest",
    "RunPage",
    "TenantOut",
    "UsageBreakdown",
    "UsageBucket",
    "UsageReport",
    "UsageRun",
    "WebhookCreate",
    "WebhookCreated",
    "WebhookOut",
]
