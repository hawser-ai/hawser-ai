"""Run events (blueprint §20, §22). Append-only, strictly ordered per run by ``seq``."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import Field

from hawser.clock import UtcDatetime
from hawser.ids import EventId, RunId, StepId, TenantId
from hawser.models.base import HawserModel


class EventType(StrEnum):
    run_created = "run.created"
    run_status_changed = "run.status_changed"
    asset_admitted = "asset.admitted"
    asset_rejected = "asset.rejected"
    plan_created = "plan.created"
    plan_rejected = "plan.rejected"
    step_started = "step.started"
    step_completed = "step.completed"
    step_failed = "step.failed"
    step_skipped = "step.skipped"
    model_called = "model.called"
    model_fallback = "model.fallback"
    tool_called = "tool.called"
    tool_denied = "tool.denied"
    context_selected = "context.selected"
    evidence_anchored = "evidence.anchored"
    validation_completed = "validation.completed"
    repair_started = "repair.started"
    repair_no_progress = "repair.no_progress"
    review_requested = "review.requested"
    review_decided = "review.decided"
    approval_requested = "approval.requested"
    approval_decided = "approval.decided"
    budget_reserved = "budget.reserved"
    budget_settled = "budget.settled"
    budget_released = "budget.released"
    budget_exceeded = "budget.exceeded"
    result_assembled = "result.assembled"
    artifact_created = "artifact.created"
    sandbox_executed = "sandbox.executed"
    webhook_delivered = "webhook.delivered"


class Event(HawserModel):
    id: EventId
    run_id: RunId
    tenant_id: TenantId
    #: Monotonic per run, starting at 1. Used for SSE resume (``Last-Event-ID``).
    seq: int = Field(ge=1)
    type: EventType
    at: UtcDatetime
    step_id: StepId | None = None
    #: Event-specific payload. Shapes are documented per type in docs/architecture/events.md.
    payload: dict[str, Any] = {}


__all__ = ["Event", "EventType"]
