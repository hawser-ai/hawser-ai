# Architecture Decision Records

| ADR | Title | Status |
|---|---|---|
| [0001](0001-repo-layout-and-package-names.md) | Monorepo layout and Python package names | accepted |
| [0002](0002-python-first-local-runtime.md) | Python-first local runtime; Temporal is the only production durability model | accepted |
| [0003](0003-single-python-cli.md) | One developer CLI in Python; the Go binary is the server | accepted |
| [0004](0004-pydantic-source-of-truth.md) | Pydantic models are the contract source of truth | accepted |
| [0005](0005-evidence-sidecar.md) | Field-level evidence lives in a `field_meta` sidecar | accepted |
| [0006](0006-identifiers-and-tenant-scoped-hashing.md) | Prefixed ULID identifiers and tenant-scoped content hashes | accepted |
| [0007](0007-pdf-engine.md) | PDF engine is pypdfium2, not PyMuPDF | accepted |
| [0008](0008-recording-and-replay.md) | Every model and tool call is recordable; tests run from recordings | accepted |
| [0009](0009-licence-and-contribution.md) | Apache-2.0 licence, DCO sign-off, name check | accepted |

New ADRs copy [`0000-template.md`](0000-template.md). Number sequentially; never reuse a number.
