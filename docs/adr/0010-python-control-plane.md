# ADR-0010: Python control plane with a Postgres-leased durable executor; Go and Temporal deferred

- **Status:** accepted (supersedes the Go/Temporal parts of ADR-0002 and ADR-0003's note on `hawserd`)
- **Date:** 2026-10-18
- **Phase:** 5

## Context

ADR-0002 planned Phase 5 as a Go control plane (`chi`, `sqlc`, Temporal workflows in Go) with
Python activity workers, so that a stable control plane would be separated from fast-moving AI
dependencies. Four phases later the facts are:

- Everything that does work is Python: the runtime, gateway, broker, validators, planner, media
  primitives, evals. The Go sequencer would call Python for every activity.
- The runtime already has the durability primitive Temporal would supply: every step is
  idempotent on a `step_key`, outputs are checkpointed artifacts, and a run re-executes from its
  checkpoints (`LocalRuntime.resume`, `ctx.resuming`).
- The team is one engineer plus an assistant. A second language doubles the schema surface
  (generated Go types, drift checks between builds) and the tooling.
- The ten-minute quickstart must keep working with no infrastructure; the server has to be an
  *addition*, not a replacement.

## Decision

1. **The control plane is Python.** A new workspace package `hawser-server` provides `hawserd`:
   a FastAPI application (`hawserd serve`), a worker (`hawserd worker`), migrations
   (`hawserd migrate`) and admin commands (`hawserd admin ...`). The OpenAPI document is generated
   from the same Pydantic contract models the SDK uses and is committed under `api/openapi/`
   with a drift check, exactly like `api/schemas/`.
2. **Durable execution is a Postgres-leased worker, not Temporal.** A `jobs` table holds one row
   per run that needs machine work. Workers lease a row (`FOR UPDATE SKIP LOCKED`), heartbeat
   the lease, and execute the run with the Phase 1–4 engine. A crashed worker's lease expires
   and another worker re-executes the run; completed steps replay from checkpoints so no paid
   work repeats. Signals are rows: cancel requests, approval decisions and review decisions are
   written by the API and observed by the engine at step boundaries. Wall-time limits are
   enforced by the worker between steps and by lease expiry.
3. **One production durability model.** Postgres + the leased worker is the production mode.
   `LocalRuntime` (SQLite, in-process, synchronous) stays development-grade. `hawserd` can also run
   on SQLite for single-node evaluation (`--dev`); that mode is documented as not durable across
   hosts. Temporal is not added as a second orchestrator. If scale or operational needs ever
   require it, it *replaces* the leased worker behind the same `Engine` and `JobQueue`
   interfaces; it does not run alongside.
4. **Go is deferred indefinitely.** Its remaining advantages (single static binary, cheaper SSE
   fan-out) are addressed by a container image and by horizontally scaled API processes. The
   `control/` directory is removed from the layout; `api/proto/` stays reserved for a future
   internal RPC if one becomes necessary.

## Consequences

- Phase 5 shrinks: no codegen pipeline, no Temporal server, no deterministic-workflow sandbox.
  Compose runs four services (Postgres, MinIO, `hawserd`, worker) instead of seven.
- We own the lease/heartbeat/retry logic. It is small (one job kind, one table) but it is ours
  to test: crash recovery, poison runs, lost heartbeats, concurrent cancel.
- A second `MetadataStore` (Postgres) and `ArtifactStore` (S3) implementation; the conformance
  suite runs against both backends and the SQLite/Postgres pair runs the same store contract
  tests. Tests use an embedded Postgres (`pgserver`) so no service is needed locally; CI also
  runs the Postgres path.
- SSE fan-out is polling-based on the events table (short interval); `LISTEN/NOTIFY` is an
  optimisation available later without API change.
- ADR-0002's text on Temporal is superseded by this ADR; its Python-first local runtime
  decision stands.

## Alternatives considered

- **Go control plane + Temporal (the original plan).** Highest operational maturity; rejected
  for cost: two languages, two schema builds, an orchestration server, for a thin sequencer.
- **Python Temporal workers (Temporal SDK for Python).** Removes the language problem, keeps the
  infrastructure weight and a second source of truth for run state; the runtime's checkpoint
  model would be duplicated by Temporal's event history. Deferred; the `JobQueue` boundary
  keeps it possible.
- **An existing Postgres task-queue library (Procrastinate, Celery+Postgres).** Generic retries
  and scheduling we do not need, without the one thing we do need (re-execute from Hawser
  checkpoints with run-specific signals). The bespoke table is under 300 lines.
- **Redis-backed queue.** One more service and no transactional link to the run row.
