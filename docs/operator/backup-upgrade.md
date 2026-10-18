# Backup, restore, upgrade

## What to back up

- **Postgres**: every record — runs, steps, events, results, revisions, reviews, approvals,
  tenants, API-key hashes, jobs, webhooks and their deliveries.
- **The artifact bucket**: originals, rendered pages, step outputs. Objects are
  content-addressed under `<tenant>/<hh>/<hash>` and never rewritten, so incremental sync is
  cheap (`mc mirror`, `aws s3 sync`, `rclone`).

The two must be consistent with each other: an artifact row without its object is a run that
cannot be inspected. Back up the bucket first, then the database (a database snapshot that is
slightly older than the bucket is harmless; the reverse is not).

```bash
mc mirror hawser/hawser /backups/artifacts/        # or aws s3 sync
pg_dump --format=custom hawser > /backups/hawser.dump
```

Restore in the same order: objects, then `pg_restore`. Nothing is cached outside those two
stores; workers and API processes are stateless.

## Upgrading

Migrations are forward-only and recorded in `schema_migrations`.

1. Back up as above.
2. Stop workers (let in-flight runs finish or accept that their leases will be re-taken after
   the upgrade; checkpoints make that safe).
3. Deploy the new image or package. Run `hawserd migrate` once (or start `hawserd serve
   --migrate`). `hawserd migrate --status` shows what is applied.
4. Start the API, then the workers.

Contract changes are versioned: `api/schemas/*` carries `x-hawser-contract-version`, and the
OpenAPI document's `info.version` is `<contract>+hawserd.<package>`. Clients should pin the
contract major version.

## Retention

Not automated yet. Artifacts are small per run (originals plus JSON); to prune, delete runs
older than your window from Postgres and then remove bucket objects that no artifact row
references. A scheduled `hawserd admin prune` is planned alongside the retention policy in
Phase 12.
