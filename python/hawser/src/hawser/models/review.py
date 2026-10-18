"""Reviews (blueprint §17): a human decision on a ``needs_review`` run."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import Field

from hawser.clock import UtcDatetime
from hawser.ids import ReviewId, RunId, TenantId
from hawser.models.base import HawserModel
from hawser.models.common import JsonPointer


class ReviewStatus(StrEnum):
    open = "open"
    approved = "approved"
    rejected = "rejected"


class ReviewDecision(HawserModel):
    field: JsonPointer
    old: Any = None
    new: Any = None
    comment: str | None = None
    reviewer: str
    at: UtcDatetime


class Review(HawserModel):
    id: ReviewId
    run_id: RunId
    tenant_id: TenantId
    status: ReviewStatus = ReviewStatus.open
    assigned_to: str | None = None
    decisions: list[ReviewDecision] = Field(default_factory=list)
    comment: str | None = None
    #: Optimistic-concurrency token; every change increments it (``If-Match`` in the API).
    version: int = Field(default=1, ge=1)
    decided_by: str | None = None
    decided_at: UtcDatetime | None = None
    created_at: UtcDatetime
    updated_at: UtcDatetime

    @property
    def etag(self) -> str:
        return f'W/"{self.version}"'


__all__ = ["Review", "ReviewDecision", "ReviewStatus"]
