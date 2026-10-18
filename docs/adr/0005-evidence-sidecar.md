# ADR-0005: Field-level evidence lives in a `field_meta` sidecar, not wrapped around values

- **Status:** accepted
- **Date:** 2026-10-18
- **Phase:** 0

## Context

Blueprint §15 shows a field response as `{"value": ..., "evidence": {...}, "validation": {...}}`.
If `result` itself had that shape, it would no longer conform to the caller's `output_schema`,
and every consumer would have to unwrap values before use.

## Decision

- `RunResult.result` conforms exactly to the caller's schema.
- `RunResult.field_meta` is a map from JSON pointer (RFC 6901, e.g. `/line_items/3/unit_price`)
  to `FieldMeta {status, evidence: [evidence ids], issues: [issue ids], anchor_confidence}`.
- `RunResult.evidence` and `RunResult.validation.issues` hold the referenced objects once.
- The blueprint's wrapped form is produced on demand by a helper (`result.annotated()`, Phase 1)
  and by the web UI; it is a view, not the storage shape.
- Internally, extraction uses an *envelope* in which the model returns
  `{value, source_text, page, status}` per leaf field; the runtime unwraps `value` into `result`
  and turns the rest into evidence. The envelope never leaves the runtime.

## Consequences

- Callers can hand `result` straight to their own typed code.
- Pointers must be computed consistently for arrays; the runtime owns a single
  `pointer_for(path)` helper.

## Alternatives considered

- Wrapped values: breaks schema conformance. Rejected.
- Parallel "shadow" object with the same shape as `result`: harder to address sparse fields
  and arrays; pointers are simpler. Rejected.
