# ADR-0001: Monorepo layout and Python package names

- **Status:** accepted; the core distribution name is superseded by ADR-0011 (`hawser-ai`)
- **Date:** 2026-10-18
- **Phase:** 0

## Context

The blueprint (§32) prescribes a monorepo with directories such as `python/hawser_sdk`,
`python/workers`, `python/media` and `python/evals`. Those are directory names, not importable
package names, and `workers`/`media`/`evals` would collide with common third-party names.

## Decision

Keep the blueprint's top-level layout. Inside `python/`, use importable, namespaced package
names that match their directory:

| Directory | Distribution | Import name | Purpose |
|---|---|---|---|
| `python/hawser` | `hawser` | `hawser` | SDK, local runtime, CLI, contract models |
| `python/hawser_media` | `hawser-media` | `hawser_media` | document/image/audio/video primitives |
| `python/hawser_evals` | `hawser-evals` | `hawser_evals` | evaluation engine |
| `python/hawser_workers` (Phase 5) | `hawser-workers` | `hawser_workers` | Temporal activities |

`uv` workspaces manage them from the root `pyproject.toml`. Additional repositories are created
only when independent release cycles or ownership require them (blueprint §32).

## Consequences

- `pip install hawser` is the only thing a developer needs for Phases 1–4; media and evals are
  extras/dependencies of `hawser`, not separate installs.
- Cross-package imports are explicit and lint-checked (`known-first-party`).

## Alternatives considered

- Single package `hawser` with sub-packages: simpler, but media dependencies (ffmpeg, OCR
  engines, torch) would bloat the core install. Rejected.
- Separate repositories per package: premature; coordination cost outweighs benefit before 1.0.
