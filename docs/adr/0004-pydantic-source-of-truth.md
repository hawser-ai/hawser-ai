# ADR-0004: Pydantic models are the contract source of truth; JSON Schema and Go types are generated

- **Status:** accepted
- **Date:** 2026-10-18
- **Phase:** 0

## Context

Blueprint §25 requires public data contracts in REST/OpenAPI and JSON Schema, with internal
Protobuf/gRPC contracts and a Go control plane. Hand-maintaining the same shapes in Python,
JSON Schema and Go guarantees drift.

## Decision

1. `hawser.models` (Pydantic v2, `extra="forbid"`) is the single source of truth for every
   public contract object.
2. `python -m hawser.models.export` writes one `api/schemas/<Name>.schema.json` per exported
   model (draft 2020-12, deterministic output, `x-hawser-contract-version`). The files are
   committed; CI and pre-commit fail if they are stale.
3. `api/openapi/openapi.yaml` (Phase 5) references those schemas by `$ref`; Go types and server
   stubs are generated from the OpenAPI document (`oapi-codegen`). Go never hand-writes a
   contract type.
4. Internal Protobuf messages (Phase 5) carry ids and artifact references, not contract
   objects; large payloads travel as JSON validated against the exported schemas.
5. `CONTRACT_VERSION` is semver. Breaking changes bump the major and require an ADR. Pre-1.0,
   minor bumps may break with a changelog note.

## Consequences

- Schema review happens in pull requests as diffs to `api/schemas/`.
- Pydantic features that do not map to JSON Schema (custom validators) are documented as
  "runtime-only" rules; the Go side re-implements only the ones listed in
  `docs/architecture/contract-rules.md` (created when the first such rule exists).

## Alternatives considered

- JSON Schema as source with generated Pydantic: weaker typing and ergonomics in the language
  where 90% of the logic lives. Rejected.
- Protobuf as the universal source: poor fit for JSON Schema features (patterns, discriminated
  unions with defaults) and for the public REST API. Rejected.
