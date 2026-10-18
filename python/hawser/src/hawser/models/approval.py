"""Approvals (blueprint §11, §17): a human decision before a side-effecting tool runs."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import Field

from hawser.clock import UtcDatetime
from hawser.ids import ApprovalId, RunId, StepId, TenantId
from hawser.models.base import HawserModel


class ApprovalStatus(StrEnum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"


class Approval(HawserModel):
    id: ApprovalId
    run_id: RunId
    tenant_id: TenantId
    step_id: StepId | None = None
    tool: str
    #: Stable identifier for the external action, so retries reconcile instead of repeating.
    action_id: str
    #: What exactly would be done (tool input, redacted per spec). Recorded verbatim on decision.
    payload: dict[str, Any] = Field(default_factory=dict)
    status: ApprovalStatus = ApprovalStatus.pending
    requested_at: UtcDatetime
    decided_at: UtcDatetime | None = None
    decided_by: str | None = None
    comment: str | None = None


__all__ = ["Approval", "ApprovalStatus"]
