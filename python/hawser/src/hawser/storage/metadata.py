"""Metadata storage protocol and the run record.

Implementations: :class:`hawser.storage.sql.SqlMetadataStore` (SQLite here, Postgres in
``hawser-server``). Every table carries ``tenant_id`` (ADR-0006).
"""

from __future__ import annotations

from typing import Any, Protocol

from hawser.clock import UtcDatetime
from hawser.ids import RunId, TenantId
from hawser.models import (
    Approval,
    Artifact,
    Asset,
    Event,
    EventType,
    Revision,
    RunRequest,
    RunResult,
    RunStatus,
    Step,
)
from hawser.models.base import HawserModel
from hawser.models.review import Review


class RunRecord(HawserModel):
    id: RunId
    tenant_id: TenantId
    status: RunStatus
    request: RunRequest
    trace_id: str
    created_at: UtcDatetime
    updated_at: UtcDatetime
    completed_at: UtcDatetime | None = None
    error: dict[str, Any] | None = None


class MetadataStore(Protocol):
    # assets
    def put_asset(self, asset: Asset) -> None: ...
    def get_asset(self, asset_id: str) -> Asset | None: ...
    def find_asset(self, tenant_id: str, scoped_hash: str) -> Asset | None: ...
    # artifacts
    def put_artifact(self, artifact: Artifact) -> None: ...
    def get_artifact(self, artifact_id: str) -> Artifact | None: ...
    # runs
    def put_run(self, run: RunRecord) -> None: ...
    def get_run(self, run_id: str) -> RunRecord | None: ...
    def find_run_by_idempotency_key(self, tenant_id: str, key: str) -> RunRecord | None: ...
    def list_runs(
        self,
        tenant_id: str,
        *,
        limit: int = 50,
        before: tuple[str, str] | None = None,
        status: str | None = None,
    ) -> list[RunRecord]: ...
    # steps
    def put_step(self, step: Step) -> None: ...
    def get_step(self, run_id: str, key: str) -> Step | None: ...
    def list_steps(self, run_id: str) -> list[Step]: ...
    # events
    def append_event(
        self,
        *,
        run_id: str,
        tenant_id: str,
        type: EventType,
        payload: dict[str, Any] | None = None,
        step_id: str | None = None,
    ) -> Event: ...
    def list_events(self, run_id: str, *, after_seq: int = 0) -> list[Event]: ...
    # results
    def put_result(self, result: RunResult) -> None: ...
    def get_result(self, run_id: str) -> RunResult | None: ...
    # revisions
    def put_revision(self, revision: Revision) -> None: ...
    def get_revision(self, revision_id: str) -> Revision | None: ...
    def list_revisions(self, run_id: str) -> list[Revision]: ...
    # reviews
    def put_review(self, review: Review) -> None: ...
    def get_review(self, run_id: str) -> Review | None: ...
    def list_reviews(self, tenant_id: str, *, status: str | None = None) -> list[Review]: ...
    # approvals
    def put_approval(self, approval: Approval) -> None: ...
    def get_approval(self, approval_id: str) -> Approval | None: ...
    def list_approvals(
        self, tenant_id: str, *, run_id: str | None = None, status: str | None = None
    ) -> list[Approval]: ...
    # signals (written by an API/CLI, read by the engine between steps)
    def request_cancel(self, run_id: str, tenant_id: str) -> None: ...
    def cancel_requested(self, run_id: str) -> bool: ...
    def close(self) -> None: ...


__all__ = ["MetadataStore", "RunRecord"]
