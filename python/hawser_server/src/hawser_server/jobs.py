"""The durable job queue (ADR-0010): one row per run that needs machine work.

Workers *lease* a row for ``lease_seconds`` and heartbeat it; an expired lease is re-leased by
anyone. The run itself re-executes from step checkpoints, so a re-lease never repeats paid work.
Each lease increments ``attempts``; past ``max_attempts`` the worker abandons the run.
"""

from __future__ import annotations

from dataclasses import dataclass

from hawser.storage.sql import SqlBackend
from hawser_server.util import ts, ts_after


@dataclass(frozen=True)
class Job:
    run_id: str
    tenant_id: str
    state: str
    attempts: int
    max_attempts: int
    resume: bool
    lease_owner: str | None
    lease_expires_at: str | None
    available_at: str


_COLS = (
    "run_id, tenant_id, state, attempts, max_attempts, resume, lease_owner, lease_expires_at,"
    " available_at"
)


def _job(row: tuple[object, ...]) -> Job:
    return Job(
        run_id=str(row[0]),
        tenant_id=str(row[1]),
        state=str(row[2]),
        attempts=int(row[3]),  # type: ignore[arg-type]
        max_attempts=int(row[4]),  # type: ignore[arg-type]
        resume=bool(int(row[5])),  # type: ignore[arg-type]
        lease_owner=None if row[6] is None else str(row[6]),
        lease_expires_at=None if row[7] is None else str(row[7]),
        available_at=str(row[8]),
    )


class JobQueue:
    def __init__(self, backend: SqlBackend, *, max_attempts: int = 3) -> None:
        self.backend = backend
        self.max_attempts = max_attempts

    def enqueue(self, run_id: str, tenant_id: str, *, resume: bool = False) -> Job:
        """Make the run pending. Re-enqueueing a done/leased run resets it to pending."""
        now = ts()
        with self.backend.transaction(write=True) as tx:
            tx.execute(
                "INSERT INTO jobs (run_id, tenant_id, state, attempts, max_attempts, available_at,"
                " lease_owner, lease_expires_at, resume, created_at, updated_at)"
                " VALUES (?,?,'pending',0,?,?,NULL,NULL,?,?,?)"
                " ON CONFLICT (run_id) DO UPDATE SET state = 'pending',"
                " available_at = excluded.available_at,"
                " lease_owner = NULL, lease_expires_at = NULL, resume = excluded.resume,"
                " attempts = 0, updated_at = excluded.updated_at",
                (run_id, tenant_id, self.max_attempts, now, 1 if resume else 0, now, now),
            )
            rows = tx.execute(f"SELECT {_COLS} FROM jobs WHERE run_id = ?", (run_id,))
        return _job(rows[0])

    def lease(self, owner: str, lease_seconds: int) -> Job | None:
        now = ts()
        with self.backend.transaction(write=True) as tx:
            rows = tx.execute(
                f"SELECT {_COLS} FROM jobs WHERE (state = 'pending' AND available_at <= ?)"
                " OR (state = 'leased' AND lease_expires_at < ?)"
                " ORDER BY available_at LIMIT 1" + self.backend.skip_locked,
                (now, now),
            )
            if not rows:
                return None
            run_id = str(rows[0][0])
            tx.execute(
                "UPDATE jobs SET state = 'leased', lease_owner = ?, lease_expires_at = ?,"
                " attempts = attempts + 1, updated_at = ? WHERE run_id = ?",
                (owner, ts_after(lease_seconds), now, run_id),
            )
            rows = tx.execute(f"SELECT {_COLS} FROM jobs WHERE run_id = ?", (run_id,))
        return _job(rows[0])

    def heartbeat(self, run_id: str, owner: str, lease_seconds: int) -> bool:
        """Extend the lease; ``False`` means the lease was lost (expired and re-leased)."""
        with self.backend.transaction(write=True) as tx:
            tx.execute(
                "UPDATE jobs SET lease_expires_at = ?, updated_at = ?"
                " WHERE run_id = ? AND lease_owner = ? AND state = 'leased'",
                (ts_after(lease_seconds), ts(), run_id, owner),
            )
            rows = tx.execute(
                "SELECT 1 FROM jobs WHERE run_id = ? AND lease_owner = ? AND state = 'leased'",
                (run_id, owner),
            )
        return bool(rows)

    def owns(self, run_id: str, owner: str) -> bool:
        with self.backend.transaction() as tx:
            rows = tx.execute(
                "SELECT 1 FROM jobs WHERE run_id = ? AND lease_owner = ? AND state = 'leased'"
                " AND lease_expires_at >= ?",
                (run_id, owner, ts()),
            )
        return bool(rows)

    def complete(self, run_id: str, owner: str) -> bool:
        with self.backend.transaction(write=True) as tx:
            tx.execute(
                "UPDATE jobs SET state = 'done', updated_at = ?"
                " WHERE run_id = ? AND lease_owner = ? AND state = 'leased'",
                (ts(), run_id, owner),
            )
            rows = tx.execute("SELECT 1 FROM jobs WHERE run_id = ? AND state = 'done'", (run_id,))
        return bool(rows)

    def remove(self, run_id: str) -> None:
        """Drop the job regardless of owner (cancel of a run that is not executing)."""
        with self.backend.transaction(write=True) as tx:
            tx.execute("DELETE FROM jobs WHERE run_id = ?", (run_id,))

    def get(self, run_id: str) -> Job | None:
        with self.backend.transaction() as tx:
            rows = tx.execute(f"SELECT {_COLS} FROM jobs WHERE run_id = ?", (run_id,))
        return None if not rows else _job(rows[0])

    def counts(self) -> dict[str, int]:
        with self.backend.transaction() as tx:
            rows = tx.execute("SELECT state, COUNT(*) FROM jobs GROUP BY state")
        return {str(r[0]): int(r[1]) for r in rows}  # type: ignore[arg-type]


__all__ = ["Job", "JobQueue"]
