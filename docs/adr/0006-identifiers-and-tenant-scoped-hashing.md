# ADR-0006: Prefixed ULID identifiers and tenant-scoped content hashes

- **Status:** accepted
- **Date:** 2026-10-18
- **Phase:** 0

## Context

Ids appear in logs, events, URLs and cross-language RPC. Blueprint §4.2 requires a
"tenant-scoped content hash" for admitted inputs; §24 requires tenant isolation.

## Decision

**Identifiers.** `<prefix>_<ULID>` with a registered prefix per object type
(`run_`, `asset_`, `art_`, `ev_`, ...; see `hawser.ids.PREFIXES`). ULIDs are time-sortable and
128-bit. Ids are validated at every boundary; typed aliases (`RunId`, `AssetId`, ...) enforce the
prefix inside Pydantic models. Malformed ids are rejected, never coerced.

**Hashes.** Every input has:

- `content_hash = sha256:<hex of bytes>` — stored, never used as a key visible across tenants.
- `scoped_hash = sha256(tenant_id || 0x00 || raw content_hash bytes)` — the dedup and artifact
  key.

Admission is idempotent on `scoped_hash`. Because the key includes the tenant, a caller cannot
learn whether another tenant holds a file by submitting it and observing a cache hit.

## Consequences

- Every store (local SQLite, Postgres, S3) has a `tenant_id` column/prefix from the first
  migration. The local runtime uses `tenant_id = "ten_local"`.
- Cross-tenant deduplication of identical bytes is impossible by design; storage cost is
  accepted.

## Alternatives considered

- UUIDv4: not sortable, no self-description. Rejected.
- UUIDv7: sortable but no prefix convention; ULID's Crockford alphabet is friendlier in URLs.
- Global content-addressed dedup: cheaper storage, leaks existence across tenants. Rejected.
