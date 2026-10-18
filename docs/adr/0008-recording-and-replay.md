# ADR-0008: Every model and tool call is recordable; tests run from recordings

- **Status:** accepted
- **Date:** 2026-10-18
- **Phase:** 0 (implemented in Phase 1)

## Context

Blueprint §2 requires a recorded example that runs without a provider key; §20 requires
deterministic replay for debugging; §21 requires evaluations under explicit budgets. Tests that
call live models are slow, costly, flaky and non-deterministic.

## Decision

1. The model gateway and the tool broker both pass through a `Recorder`. A recording
   ("cassette") is a directory of JSON files keyed by a hash of the canonicalised request
   (provider, model/tool, version, request body with volatile fields removed, artifact hashes
   instead of bytes).
2. `HAWSER_RECORD_MODE` ∈ `replay` (default for tests; a miss is an error), `record`,
   `auto` (replay hit, record miss), `live` (never touch cassettes).
3. Replayed calls are marked `replayed=True` with `cost_usd=0` in usage and in the trace. They
   are never billed and never presented as live results.
4. Cassettes are committed. Re-recording requires an explicit PR label; CI posts the usage/cost
   diff.
5. Live calls happen only in the nightly workflow under `HAWSER_LIVE_BUDGET_USD`.

## Consequences

- `make test` needs no keys and no network.
- Prompt changes invalidate cassettes (by design: the key includes the request). Keep prompts in
  files so diffs are reviewable.
- Deterministic replay of a whole run is possible by replaying its recorded step outputs
  (blueprint §20).

## Alternatives considered

- VCR-style HTTP recording: couples tests to provider wire formats; breaks on SDK upgrades.
  Rejected in favour of recording at Hawser's own request/response boundary.
- Mock adapters hand-written per test: unmaintainable at scale. Rejected.
