# ADR-0002: Python-first local runtime; Temporal is the only production durability model

- **Status:** accepted
- **Date:** 2026-10-18
- **Phase:** 0

## Context

Blueprint §25 specifies a Go control plane, Python workers, gRPC, Temporal, PostgreSQL and
S3-compatible storage. Blueprint §2 and §30 require a developer to reach a successful run in
under ten minutes, and a recorded example to run without any provider key. Building the server
stack first makes the quickstart impossible to meet and delays every feature behind
infrastructure.

Blueprint §25 also states: "A simpler local runner may support development, but Hawser has only
one production durability model."

## Decision

1. Phases 1–4 implement an **in-process `LocalRuntime`** in the `hawser` package. It implements
   the *exact* public contracts: `RunRequest`/`RunResult`, the run state machine, the event
   stream, `ArtifactStore`/`MetadataStore`/`EventSink` protocols, idempotent `step_key`
   checkpoints, and recording/replay.
2. Phase 5 adds the Go control plane and Temporal as a **second backend** behind the same SDK
   `Runtime` protocol. A conformance suite runs against both.
3. `LocalRuntime` is documented as development-grade (SQLite + local filesystem, no crash
   recovery across processes). **Temporal is the only production durability model.** No second
   production orchestrator will be added.

## Consequences

- The quickstart is `pip install hawser` and nothing else.
- Every runtime abstraction must be designed so Phase 5 is a re-hosting, not a rewrite:
  no SQLite-specific SQL, no in-memory state that is not reconstructible from events and
  checkpoints, no step without a `step_key`.
- Two backends to test. The conformance suite is the price, and it is also the guarantee that
  local behaviour matches production behaviour.

## Alternatives considered

- Server-first (blueprint order): fails the ten-minute quickstart; slows every feature.
- Local-only forever: cannot meet durability, multitenancy or managed-service requirements.
- Python Temporal workflows instead of Go: viable, but the blueprint's separation of a stable
  control plane from fast-moving AI dependencies is worth keeping; revisit in Phase 5 if the
  Go/Python boundary proves more costly than expected.
