"""Metadata schema as forward-only migrations in a dialect-neutral SQL subset.

The same statements run on SQLite and Postgres: ``TEXT``/``INTEGER`` columns, ISO-8601
timestamps as text, JSON as text, ``ON CONFLICT`` upserts, ``CREATE ... IF NOT EXISTS``.
Every table carries ``tenant_id`` (ADR-0006). Applied through :func:`apply_migrations`,
which records each migration in ``schema_migrations`` so a store can be upgraded in place.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hawser.storage.sql import SqlBackend


@dataclass(frozen=True)
class Migration:
    name: str
    statements: tuple[str, ...]


CORE_MIGRATIONS: tuple[Migration, ...] = (
    Migration(
        "0001_core",
        (
            """CREATE TABLE IF NOT EXISTS assets (
  id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, scoped_hash TEXT NOT NULL,
  data TEXT NOT NULL, created_at TEXT NOT NULL,
  UNIQUE (tenant_id, scoped_hash))""",
            """CREATE TABLE IF NOT EXISTS artifacts (
  id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, run_id TEXT,
  data TEXT NOT NULL, created_at TEXT NOT NULL)""",
            "CREATE INDEX IF NOT EXISTS artifacts_run ON artifacts (run_id)",
            """CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, status TEXT NOT NULL,
  idempotency_key TEXT, data TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  UNIQUE (tenant_id, idempotency_key))""",
            "CREATE INDEX IF NOT EXISTS runs_tenant_created ON runs (tenant_id, created_at)",
            """CREATE TABLE IF NOT EXISTS steps (
  id TEXT PRIMARY KEY, run_id TEXT NOT NULL, key TEXT NOT NULL, data TEXT NOT NULL,
  UNIQUE (run_id, key))""",
            """CREATE TABLE IF NOT EXISTS events (
  run_id TEXT NOT NULL, seq INTEGER NOT NULL, id TEXT NOT NULL, tenant_id TEXT NOT NULL,
  type TEXT NOT NULL, step_id TEXT, data TEXT NOT NULL, at TEXT NOT NULL,
  PRIMARY KEY (run_id, seq))""",
            """CREATE TABLE IF NOT EXISTS results (
  run_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, data TEXT NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS revisions (
  id TEXT PRIMARY KEY, run_id TEXT NOT NULL, tenant_id TEXT NOT NULL, kind TEXT NOT NULL,
  data TEXT NOT NULL, created_at TEXT NOT NULL)""",
            "CREATE INDEX IF NOT EXISTS revisions_run ON revisions (run_id, created_at)",
            """CREATE TABLE IF NOT EXISTS approvals (
  id TEXT PRIMARY KEY, run_id TEXT NOT NULL, tenant_id TEXT NOT NULL, tool TEXT NOT NULL,
  action_id TEXT NOT NULL, status TEXT NOT NULL, data TEXT NOT NULL, requested_at TEXT NOT NULL,
  UNIQUE (run_id, tool, action_id))""",
            """CREATE TABLE IF NOT EXISTS reviews (
  run_id TEXT PRIMARY KEY, id TEXT NOT NULL, tenant_id TEXT NOT NULL, status TEXT NOT NULL,
  version INTEGER NOT NULL, data TEXT NOT NULL, updated_at TEXT NOT NULL)""",
        ),
    ),
    Migration(
        "0002_signals",
        (
            # Written by an API or CLI, read by the engine at step boundaries.
            """CREATE TABLE IF NOT EXISTS run_signals (
  run_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, cancel_requested_at TEXT)""",
        ),
    ),
)


def apply_migrations(backend: SqlBackend, migrations: tuple[Migration, ...]) -> list[str]:
    """Apply the migrations not yet recorded; return the names applied (in order)."""
    applied: list[str] = []
    with backend.transaction(write=True) as tx:
        tx.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations"
            " (name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        done = {str(r[0]) for r in tx.execute("SELECT name FROM schema_migrations")}
    for m in migrations:
        if m.name in done:
            continue
        with backend.transaction(write=True) as tx:
            for stmt in m.statements:
                tx.execute(stmt)
            tx.execute(
                "INSERT INTO schema_migrations (name, applied_at) VALUES (?, ?)",
                (m.name, _utcnow()),
            )
        applied.append(m.name)
    return applied


def _utcnow() -> str:
    from hawser.clock import now  # noqa: PLC0415 - avoid import cycle at module load

    return now().isoformat()


__all__ = ["CORE_MIGRATIONS", "Migration", "apply_migrations"]
