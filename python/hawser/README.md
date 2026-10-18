# hawser

The Hawser Python package: public SDK, local runtime, CLI and contract models.

Phase 0 contents:

- `hawser.models` — Pydantic contract models; exported to `api/schemas/` by `python -m hawser.models.export`.
- `hawser.ids` — prefixed ULID identifiers.
- `hawser.hashing` — content hashes and tenant-scoped hashes.
- `hawser.errors` — the error taxonomy.
- `hawser.runtime.state` — the run state machine.
