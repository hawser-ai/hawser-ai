# ADR-0009: Apache-2.0 licence, DCO sign-off, name check

- **Status:** accepted
- **Date:** 2026-10-18
- **Phase:** 0

## Context

Blueprint §28 defines an open-source edition that "must remain useful without the commercial
service" and a managed service built on the same runtime. The licence must allow embedding in
users' products, allow the managed service, and keep contributions flowing.

## Decision

- **Licence:** Apache-2.0 for everything in this repository. No dual licensing, no "open core"
  feature gates in the runtime; commercial value is operation, scale, security administration
  and support (blueprint §28).
- **Contributions:** Developer Certificate of Origin (`Signed-off-by`) rather than a CLA; a
  CLA may be reconsidered before 1.0 if enterprise contributors require it.
- **Dependencies:** no copyleft (GPL/AGPL/LGPL/SSPL) in any package; enforced in CI.
- **Name:** "Hawser" has been chosen as the working name. Before any public release
  (Phase 11), run a trademark search in the target markets and register the domain and package
  names (`hawser` on PyPI and npm). If the name is unavailable, renaming is a search-and-replace
  on this repository and must happen before 1.0, never after.

## Consequences

- Hawser Cloud (Phase 12) runs the open runtime unchanged; proprietary code is limited to
  billing, identity, multitenant administration and operations tooling, and lives in a separate
  repository.

## Alternatives considered

- AGPL / SSPL / BSL: protects against hosted competitors but deters embedding and enterprise
  adoption. Rejected.
- MIT: fine, but Apache-2.0's explicit patent grant matters for enterprise users.
