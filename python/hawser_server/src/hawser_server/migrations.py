"""Server-side tables, applied after the core schema (``hawserd migrate``)."""

from __future__ import annotations

from hawser.storage.schema import CORE_MIGRATIONS, Migration

SERVER_MIGRATIONS: tuple[Migration, ...] = (
    Migration(
        "1001_tenants_keys",
        (
            """CREATE TABLE IF NOT EXISTS tenants (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS api_keys (
  id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, name TEXT NOT NULL,
  prefix TEXT NOT NULL UNIQUE, hash TEXT NOT NULL, created_at TEXT NOT NULL,
  revoked_at TEXT, last_used_at TEXT)""",
            "CREATE INDEX IF NOT EXISTS api_keys_tenant ON api_keys (tenant_id)",
        ),
    ),
    Migration(
        "1002_jobs",
        (
            """CREATE TABLE IF NOT EXISTS jobs (
  run_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, state TEXT NOT NULL,
  attempts INTEGER NOT NULL, max_attempts INTEGER NOT NULL, available_at TEXT NOT NULL,
  lease_owner TEXT, lease_expires_at TEXT, resume INTEGER NOT NULL,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
            "CREATE INDEX IF NOT EXISTS jobs_state ON jobs (state, available_at)",
        ),
    ),
    Migration(
        "1003_webhooks",
        (
            """CREATE TABLE IF NOT EXISTS webhooks (
  id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, url TEXT NOT NULL, events TEXT NOT NULL,
  secret TEXT NOT NULL, active INTEGER NOT NULL, created_at TEXT NOT NULL)""",
            "CREATE INDEX IF NOT EXISTS webhooks_tenant ON webhooks (tenant_id)",
            """CREATE TABLE IF NOT EXISTS webhook_deliveries (
  id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, webhook_id TEXT NOT NULL, run_id TEXT,
  event TEXT NOT NULL, payload TEXT NOT NULL, state TEXT NOT NULL, attempts INTEGER NOT NULL,
  next_attempt_at TEXT NOT NULL, last_status INTEGER, last_error TEXT,
  created_at TEXT NOT NULL, delivered_at TEXT)""",
            "CREATE INDEX IF NOT EXISTS deliveries_due"
            " ON webhook_deliveries (state, next_attempt_at)",
            "CREATE INDEX IF NOT EXISTS deliveries_hook"
            " ON webhook_deliveries (webhook_id, created_at)",
        ),
    ),
    Migration(
        "1004_evals_settings",
        (
            """CREATE TABLE IF NOT EXISTS eval_reports (
  id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, name TEXT NOT NULL, dataset TEXT NOT NULL,
  skill TEXT, model TEXT NOT NULL, mode TEXT, cases INTEGER NOT NULL, metrics TEXT NOT NULL,
  artifact_id TEXT NOT NULL, created_at TEXT NOT NULL)""",
            "CREATE INDEX IF NOT EXISTS eval_reports_tenant"
            " ON eval_reports (tenant_id, created_at)",
            """CREATE TABLE IF NOT EXISTS tenant_settings (
  tenant_id TEXT PRIMARY KEY, data TEXT NOT NULL, updated_at TEXT NOT NULL)""",
        ),
    ),
)

ALL_MIGRATIONS: tuple[Migration, ...] = CORE_MIGRATIONS + SERVER_MIGRATIONS

__all__ = ["ALL_MIGRATIONS", "SERVER_MIGRATIONS"]
