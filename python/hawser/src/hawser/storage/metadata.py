"""Metadata storage protocol and the SQLite implementation.

The SQL is deliberately plain (no SQLite-specific features beyond ``json`` text columns) so the
Postgres port in Phase 5 is mechanical. Every table carries ``tenant_id`` (ADR-0006).
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any, Protocol

from hawser.clock import UtcDatetime, now
from hawser.ids import RunId, TenantId, new_id
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
    def list_runs(self, tenant_id: str, *, limit: int = 50) -> list[RunRecord]: ...
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
    def close(self) -> None: ...


_SCHEMA = """
CREATE TABLE IF NOT EXISTS assets (
  id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, scoped_hash TEXT NOT NULL,
  data TEXT NOT NULL, created_at TEXT NOT NULL,
  UNIQUE (tenant_id, scoped_hash));
CREATE TABLE IF NOT EXISTS artifacts (
  id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, run_id TEXT,
  data TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS artifacts_run ON artifacts (run_id);
CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, status TEXT NOT NULL,
  idempotency_key TEXT, data TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  UNIQUE (tenant_id, idempotency_key));
CREATE TABLE IF NOT EXISTS steps (
  id TEXT PRIMARY KEY, run_id TEXT NOT NULL, key TEXT NOT NULL, data TEXT NOT NULL,
  UNIQUE (run_id, key));
CREATE TABLE IF NOT EXISTS events (
  run_id TEXT NOT NULL, seq INTEGER NOT NULL, id TEXT NOT NULL, tenant_id TEXT NOT NULL,
  type TEXT NOT NULL, step_id TEXT, data TEXT NOT NULL, at TEXT NOT NULL,
  PRIMARY KEY (run_id, seq));
CREATE TABLE IF NOT EXISTS results (
  run_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS revisions (
  id TEXT PRIMARY KEY, run_id TEXT NOT NULL, tenant_id TEXT NOT NULL, kind TEXT NOT NULL,
  data TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS revisions_run ON revisions (run_id, created_at);
CREATE TABLE IF NOT EXISTS approvals (
  id TEXT PRIMARY KEY, run_id TEXT NOT NULL, tenant_id TEXT NOT NULL, tool TEXT NOT NULL,
  action_id TEXT NOT NULL, status TEXT NOT NULL, data TEXT NOT NULL, requested_at TEXT NOT NULL,
  UNIQUE (run_id, tool, action_id));
CREATE TABLE IF NOT EXISTS reviews (
  run_id TEXT PRIMARY KEY, id TEXT NOT NULL, tenant_id TEXT NOT NULL, status TEXT NOT NULL,
  version INTEGER NOT NULL, data TEXT NOT NULL, updated_at TEXT NOT NULL);
"""


class SQLiteMetadataStore:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        with self._lock:
            self._conn.executescript(_SCHEMA)

    # -- helpers ---------------------------------------------------------------------------
    def _one(self, sql: str, params: tuple[Any, ...]) -> str | None:
        with self._lock:
            row = self._conn.execute(sql, params).fetchone()
        return None if row is None else str(row[0])

    def _many(self, sql: str, params: tuple[Any, ...]) -> list[str]:
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [str(r[0]) for r in rows]

    # -- assets ----------------------------------------------------------------------------
    def put_asset(self, asset: Asset) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO assets (id, tenant_id, scoped_hash, data, created_at)"
                " VALUES (?,?,?,?,?)",
                (
                    asset.id,
                    asset.tenant_id,
                    asset.scoped_hash,
                    asset.model_dump_json(),
                    asset.created_at.isoformat(),
                ),
            )

    def get_asset(self, asset_id: str) -> Asset | None:
        raw = self._one("SELECT data FROM assets WHERE id = ?", (asset_id,))
        return None if raw is None else Asset.model_validate_json(raw)

    def find_asset(self, tenant_id: str, scoped_hash: str) -> Asset | None:
        raw = self._one(
            "SELECT data FROM assets WHERE tenant_id = ? AND scoped_hash = ?",
            (tenant_id, scoped_hash),
        )
        return None if raw is None else Asset.model_validate_json(raw)

    # -- artifacts -------------------------------------------------------------------------
    def put_artifact(self, artifact: Artifact) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO artifacts (id, tenant_id, run_id, data, created_at)"
                " VALUES (?,?,?,?,?)",
                (
                    artifact.id,
                    artifact.tenant_id,
                    artifact.run_id,
                    artifact.model_dump_json(),
                    artifact.created_at.isoformat(),
                ),
            )

    def get_artifact(self, artifact_id: str) -> Artifact | None:
        raw = self._one("SELECT data FROM artifacts WHERE id = ?", (artifact_id,))
        return None if raw is None else Artifact.model_validate_json(raw)

    # -- runs ------------------------------------------------------------------------------
    def put_run(self, run: RunRecord) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO runs"
                " (id, tenant_id, status, idempotency_key, data, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?)",
                (
                    run.id,
                    run.tenant_id,
                    run.status.value,
                    run.request.idempotency_key,
                    run.model_dump_json(),
                    run.created_at.isoformat(),
                    run.updated_at.isoformat(),
                ),
            )

    def get_run(self, run_id: str) -> RunRecord | None:
        raw = self._one("SELECT data FROM runs WHERE id = ?", (run_id,))
        return None if raw is None else RunRecord.model_validate_json(raw)

    def find_run_by_idempotency_key(self, tenant_id: str, key: str) -> RunRecord | None:
        raw = self._one(
            "SELECT data FROM runs WHERE tenant_id = ? AND idempotency_key = ?", (tenant_id, key)
        )
        return None if raw is None else RunRecord.model_validate_json(raw)

    def list_runs(self, tenant_id: str, *, limit: int = 50) -> list[RunRecord]:
        rows = self._many(
            "SELECT data FROM runs WHERE tenant_id = ? ORDER BY created_at DESC, id DESC LIMIT ?",
            (tenant_id, limit),
        )
        return [RunRecord.model_validate_json(r) for r in rows]

    # -- steps -----------------------------------------------------------------------------
    def put_step(self, step: Step) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO steps (id, run_id, key, data) VALUES (?,?,?,?)",
                (step.id, step.run_id, step.key, step.model_dump_json()),
            )

    def get_step(self, run_id: str, key: str) -> Step | None:
        raw = self._one("SELECT data FROM steps WHERE run_id = ? AND key = ?", (run_id, key))
        return None if raw is None else Step.model_validate_json(raw)

    def list_steps(self, run_id: str) -> list[Step]:
        rows = self._many("SELECT data FROM steps WHERE run_id = ? ORDER BY id", (run_id,))
        return [Step.model_validate_json(r) for r in rows]

    # -- events ----------------------------------------------------------------------------
    def append_event(
        self,
        *,
        run_id: str,
        tenant_id: str,
        type: EventType,
        payload: dict[str, Any] | None = None,
        step_id: str | None = None,
    ) -> Event:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._conn.execute(
                    "SELECT COALESCE(MAX(seq), 0) FROM events WHERE run_id = ?", (run_id,)
                ).fetchone()
                seq = int(row[0]) + 1
                event = Event(
                    id=new_id("evt"),
                    run_id=run_id,
                    tenant_id=tenant_id,
                    seq=seq,
                    type=type,
                    at=now(),
                    step_id=step_id,
                    payload=payload or {},
                )
                self._conn.execute(
                    "INSERT INTO events (run_id, seq, id, tenant_id, type, step_id, data, at)"
                    " VALUES (?,?,?,?,?,?,?,?)",
                    (
                        run_id,
                        seq,
                        event.id,
                        tenant_id,
                        type.value,
                        step_id,
                        event.model_dump_json(),
                        event.at.isoformat(),
                    ),
                )
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
        return event

    def list_events(self, run_id: str, *, after_seq: int = 0) -> list[Event]:
        rows = self._many(
            "SELECT data FROM events WHERE run_id = ? AND seq > ? ORDER BY seq", (run_id, after_seq)
        )
        return [Event.model_validate_json(r) for r in rows]

    # -- results ---------------------------------------------------------------------------
    def put_result(self, result: RunResult) -> None:
        run = self.get_run(result.run_id)
        tenant = run.tenant_id if run else ""
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO results (run_id, tenant_id, data) VALUES (?,?,?)",
                (result.run_id, tenant, result.model_dump_json()),
            )

    def get_result(self, run_id: str) -> RunResult | None:
        raw = self._one("SELECT data FROM results WHERE run_id = ?", (run_id,))
        return None if raw is None else RunResult.model_validate_json(raw)

    # -- revisions -------------------------------------------------------------------------
    def put_revision(self, revision: Revision) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO revisions (id, run_id, tenant_id, kind, data, created_at)"
                " VALUES (?,?,?,?,?,?)",
                (
                    revision.id,
                    revision.run_id,
                    revision.tenant_id,
                    revision.kind,
                    revision.model_dump_json(),
                    revision.created_at.isoformat(),
                ),
            )

    def get_revision(self, revision_id: str) -> Revision | None:
        raw = self._one("SELECT data FROM revisions WHERE id = ?", (revision_id,))
        return None if raw is None else Revision.model_validate_json(raw)

    def list_revisions(self, run_id: str) -> list[Revision]:
        rows = self._many(
            "SELECT data FROM revisions WHERE run_id = ? ORDER BY created_at, id", (run_id,)
        )
        return [Revision.model_validate_json(r) for r in rows]

    # -- reviews ---------------------------------------------------------------------------
    def put_review(self, review: Review) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO reviews"
                " (run_id, id, tenant_id, status, version, data, updated_at)"
                " VALUES (?,?,?,?,?,?,?)",
                (
                    review.run_id,
                    review.id,
                    review.tenant_id,
                    review.status.value,
                    review.version,
                    review.model_dump_json(),
                    review.updated_at.isoformat(),
                ),
            )

    def get_review(self, run_id: str) -> Review | None:
        raw = self._one("SELECT data FROM reviews WHERE run_id = ?", (run_id,))
        return None if raw is None else Review.model_validate_json(raw)

    def list_reviews(self, tenant_id: str, *, status: str | None = None) -> list[Review]:
        if status is None:
            rows = self._many(
                "SELECT data FROM reviews WHERE tenant_id = ? ORDER BY updated_at DESC",
                (tenant_id,),
            )
        else:
            rows = self._many(
                "SELECT data FROM reviews WHERE tenant_id = ? AND status = ?"
                " ORDER BY updated_at DESC",
                (tenant_id, status),
            )
        return [Review.model_validate_json(r) for r in rows]

    # -- approvals -------------------------------------------------------------------------
    def put_approval(self, approval: Approval) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO approvals"
                " (id, run_id, tenant_id, tool, action_id, status, data, requested_at)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (
                    approval.id,
                    approval.run_id,
                    approval.tenant_id,
                    approval.tool,
                    approval.action_id,
                    approval.status.value,
                    approval.model_dump_json(),
                    approval.requested_at.isoformat(),
                ),
            )

    def get_approval(self, approval_id: str) -> Approval | None:
        raw = self._one("SELECT data FROM approvals WHERE id = ?", (approval_id,))
        return None if raw is None else Approval.model_validate_json(raw)

    def list_approvals(
        self, tenant_id: str, *, run_id: str | None = None, status: str | None = None
    ) -> list[Approval]:
        sql = "SELECT data FROM approvals WHERE tenant_id = ?"
        params: list[object] = [tenant_id]
        if run_id is not None:
            sql += " AND run_id = ?"
            params.append(run_id)
        if status is not None:
            sql += " AND status = ?"
            params.append(status)
        rows = self._many(sql + " ORDER BY requested_at DESC", tuple(params))
        return [Approval.model_validate_json(r) for r in rows]

    def close(self) -> None:
        with self._lock:
            self._conn.close()


__all__ = ["MetadataStore", "RunRecord", "SQLiteMetadataStore"]
