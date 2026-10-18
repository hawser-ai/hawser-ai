# ADR-0003: One developer CLI in Python; the Go binary is the server

- **Status:** accepted (ADR-0010: `hawserd` is Python too; this ADR's `hawserd` references read as the Python server)
- **Date:** 2026-10-18
- **Phase:** 0

## Context

Blueprint §22 lists a "Go command-line interface". The developer CLI must drive the local
runtime, skills, evaluations and inspection — all Python. A Go CLI would either duplicate that
logic or shell out to Python.

## Decision

- `hawser` (Python, `typer`) is the single user-facing CLI for the whole product lifetime:
  `init`, `run`, `inspect`, `eval`, `compare`, `skill ...`, `review ...`, `models ...`.
  It talks to `LocalRuntime` or to a server via the same `Runtime` protocol.
- `hawserd` (Go, Phase 5) is the server binary with operator sub-commands
  (`serve`, `migrate`, `admin ...`). It is not a developer CLI.
- If a Go distribution of the developer CLI is later wanted (single static binary), it will be a
  thin, generated OpenAPI client limited to server-backed commands (Phase 11 at the earliest).

## Consequences

- One place for CLI UX; one set of docs.
- Developers need Python to use the CLI. Acceptable: the SDK is Python anyway.

## Alternatives considered

- Go CLI from the start: blocks on Phase 5, duplicates eval/skill logic. Rejected.
