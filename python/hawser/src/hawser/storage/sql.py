"""Dialect-neutral SQL metadata store.

``SqlMetadataStore`` writes the SQL once (``?`` placeholders, ISO timestamps, JSON text) and a
``SqlBackend`` adapts it to a driver: SQLite here, Postgres in ``hawser-server``. The only
dialect-specific pieces are placeholder style, transaction start, the per-run lock used to
assign event sequence numbers, and the ``SKIP LOCKED`` suffix used by job queues.
"""

from __future__ import annotations

import sqlite3
import threading
from abc import ABC, abstractmethod
from collections.abc import Generator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from hawser.clock import now
from hawser.ids import new_id
from hawser.models import (
    Approval,
    Artifact,
    Asset,
    Event,
    EventType,
    Revision,
    RunResult,
    Step,
)
from hawser.models.review import Review
from hawser.storage.metadata import RunRecord
from hawser.storage.schema import CORE_MIGRATIONS, Migration, apply_migrations

Row = tuple[Any, ...]


class Tx(ABC):
    """A transaction-bound connection. ``execute`` returns all rows (empty for writes)."""

    @abstractmethod
    def execute(self, sql: str, params: Sequence[Any] = ()) -> list[Row]: ...

    def lock_run(self, run_id: str) -> None:
        """Serialise writers of one run for the rest of the transaction (default: no-op)."""
        return


class SqlBackend(ABC):
    #: Appended to a ``SELECT`` that leases queue rows; empty where the transaction serialises.
    skip_locked: str = ""

    @abstractmethod
    @contextmanager
    def transaction(self, *, write: bool = False) -> Generator[Tx]: ...

    @abstractmethod
    def close(self) -> None: ...


# -- SQLite --------------------------------------------------------------------------------


class _SqliteTx(Tx):
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def execute(self, sql: str, params: Sequence[Any] = ()) -> list[Row]:
        cur = self._conn.execute(sql, tuple(params))
        return [tuple(r) for r in cur.fetchall()] if cur.description else []


class SQLiteBackend(SqlBackend):
    """One connection, one process-wide lock; ``BEGIN IMMEDIATE`` for writes."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            str(self.path), check_same_thread=False, isolation_level=None, timeout=30
        )
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._depth = 0

    @contextmanager
    def transaction(self, *, write: bool = False) -> Generator[Tx]:
        with self._lock:
            outer = self._depth == 0
            if outer:
                self._conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            self._depth += 1
            try:
                yield _SqliteTx(self._conn)
            except BaseException:
                self._depth -= 1
                if outer:
                    self._conn.execute("ROLLBACK")
                raise
            self._depth -= 1
            if outer:
                self._conn.execute("COMMIT")

    def close(self) -> None:
        with self._lock:
            self._conn.close()


# -- the store -----------------------------------------------------------------------------


class SqlMetadataStore:
    """Implements :class:`hawser.storage.metadata.MetadataStore` over any :class:`SqlBackend`."""

    def __init__(
        self, backend: SqlBackend, *, migrations: tuple[Migration, ...] = CORE_MIGRATIONS
    ) -> None:
        self.backend = backend
        if migrations:
            apply_migrations(backend, migrations)

    # -- helpers ---------------------------------------------------------------------------
    def _one(self, sql: str, params: Sequence[Any]) -> str | None:
        with self.backend.transaction() as tx:
            rows = tx.execute(sql, params)
        return None if not rows else str(rows[0][0])

    def _many(self, sql: str, params: Sequence[Any]) -> list[str]:
        with self.backend.transaction() as tx:
            rows = tx.execute(sql, params)
        return [str(r[0]) for r in rows]

    def _write(self, sql: str, params: Sequence[Any]) -> None:
        with self.backend.transaction(write=True) as tx:
            tx.execute(sql, params)

    # -- assets ----------------------------------------------------------------------------
    def put_asset(self, asset: Asset) -> None:
        self._write(
            "INSERT INTO assets (id, tenant_id, scoped_hash, data, created_at)"
            " VALUES (?,?,?,?,?) ON CONFLICT DO NOTHING",
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
        self._write(
            "INSERT INTO artifacts (id, tenant_id, run_id, data, created_at) VALUES (?,?,?,?,?)"
            " ON CONFLICT (id) DO UPDATE SET data = excluded.data, run_id = excluded.run_id",
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
        self._write(
            "INSERT INTO runs"
            " (id, tenant_id, status, idempotency_key, data, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?)"
            " ON CONFLICT (id) DO UPDATE SET status = excluded.status, data = excluded.data,"
            " updated_at = excluded.updated_at",
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

    def list_runs(
        self,
        tenant_id: str,
        *,
        limit: int = 50,
        before: tuple[str, str] | None = None,
        status: str | None = None,
    ) -> list[RunRecord]:
        sql = "SELECT data FROM runs WHERE tenant_id = ?"
        params: list[Any] = [tenant_id]
        if status is not None:
            sql += " AND status = ?"
            params.append(status)
        if before is not None:
            sql += " AND (created_at < ? OR (created_at = ? AND id < ?))"
            params.extend([before[0], before[0], before[1]])
        sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
        params.append(limit)
        return [RunRecord.model_validate_json(r) for r in self._many(sql, params)]

    # -- steps -----------------------------------------------------------------------------
    def put_step(self, step: Step) -> None:
        self._write(
            "INSERT INTO steps (id, run_id, key, data) VALUES (?,?,?,?)"
            " ON CONFLICT (run_id, key) DO UPDATE SET id = excluded.id, data = excluded.data",
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
        with self.backend.transaction(write=True) as tx:
            tx.lock_run(run_id)
            rows = tx.execute(
                "SELECT COALESCE(MAX(seq), 0) FROM events WHERE run_id = ?", (run_id,)
            )
            seq = int(rows[0][0]) + 1
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
            tx.execute(
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
        self._write(
            "INSERT INTO results (run_id, tenant_id, data) VALUES (?,?,?)"
            " ON CONFLICT (run_id) DO UPDATE SET data = excluded.data",
            (result.run_id, tenant, result.model_dump_json()),
        )

    def get_result(self, run_id: str) -> RunResult | None:
        raw = self._one("SELECT data FROM results WHERE run_id = ?", (run_id,))
        return None if raw is None else RunResult.model_validate_json(raw)

    # -- revisions -------------------------------------------------------------------------
    def put_revision(self, revision: Revision) -> None:
        self._write(
            "INSERT INTO revisions (id, run_id, tenant_id, kind, data, created_at)"
            " VALUES (?,?,?,?,?,?) ON CONFLICT (id) DO UPDATE SET data = excluded.data",
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
        self._write(
            "INSERT INTO reviews (run_id, id, tenant_id, status, version, data, updated_at)"
            " VALUES (?,?,?,?,?,?,?) ON CONFLICT (run_id) DO UPDATE SET status = excluded.status,"
            " version = excluded.version, data = excluded.data, updated_at = excluded.updated_at",
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
        sql = "SELECT data FROM reviews WHERE tenant_id = ?"
        params: list[Any] = [tenant_id]
        if status is not None:
            sql += " AND status = ?"
            params.append(status)
        rows = self._many(sql + " ORDER BY updated_at DESC", params)
        return [Review.model_validate_json(r) for r in rows]

    # -- approvals -------------------------------------------------------------------------
    def put_approval(self, approval: Approval) -> None:
        self._write(
            "INSERT INTO approvals"
            " (id, run_id, tenant_id, tool, action_id, status, data, requested_at)"
            " VALUES (?,?,?,?,?,?,?,?) ON CONFLICT (id) DO UPDATE SET status = excluded.status,"
            " data = excluded.data",
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
        params: list[Any] = [tenant_id]
        if run_id is not None:
            sql += " AND run_id = ?"
            params.append(run_id)
        if status is not None:
            sql += " AND status = ?"
            params.append(status)
        rows = self._many(sql + " ORDER BY requested_at DESC", params)
        return [Approval.model_validate_json(r) for r in rows]

    # -- signals ---------------------------------------------------------------------------
    def request_cancel(self, run_id: str, tenant_id: str) -> None:
        self._write(
            "INSERT INTO run_signals (run_id, tenant_id, cancel_requested_at) VALUES (?,?,?)"
            " ON CONFLICT (run_id) DO UPDATE SET"
            " cancel_requested_at = excluded.cancel_requested_at",
            (run_id, tenant_id, now().isoformat()),
        )

    def cancel_requested(self, run_id: str) -> bool:
        raw = self._one(
            "SELECT cancel_requested_at FROM run_signals WHERE run_id = ?"
            " AND cancel_requested_at IS NOT NULL",
            (run_id,),
        )
        return raw is not None

    def close(self) -> None:
        self.backend.close()


class SQLiteMetadataStore(SqlMetadataStore):
    """The local store: a SQLite file (or ``:memory:``) with the core schema applied."""

    def __init__(self, path: Path | str) -> None:
        super().__init__(SQLiteBackend(path))
        self.path = Path(path)


__all__ = ["Row", "SQLiteBackend", "SQLiteMetadataStore", "SqlBackend", "SqlMetadataStore", "Tx"]
