# Contributing to Hawser

Thanks for helping. Bug reports, skills, model and OCR adapters, tools, docs and benchmarks are
all welcome.

## Before you start

- **Bugs:** open an issue with the run id, the `hawser inspect --json` output (redact anything
  private), and how to reproduce it. A failing test is the best bug report.
- **Features:** open an issue first for anything that changes a contract (`api/schemas/`,
  `api/openapi/`), the run state machine, or the policy model, so we can agree on the shape
  before you write code. Larger decisions get an ADR in `docs/adr/`.
- **Security issues:** do not open a public issue; see [SECURITY.md](SECURITY.md).

## Setup

You need [`uv`](https://docs.astral.sh/uv/) (it fetches Python 3.12). For the web app: Node 20
and pnpm.

```bash
make setup          # venv, all packages, pre-commit hooks
make check          # ruff, strict pyright, schema + OpenAPI drift, tests (what CI runs)
make web-check      # web/: typecheck, lint, unit tests
cd web && pnpm e2e  # web/: Playwright against a real hawserd (run `pnpm build` first)
```

No provider keys are needed: tests replay recorded model responses (cassettes) and server tests
use SQLite plus an embedded Postgres.

## Rules of the codebase

1. **Contracts first.** Request, response and event shapes are Pydantic models exported to
   `api/schemas/` (`make schemas`); the OpenAPI document is generated (`make openapi`). CI fails
   on drift. Regenerate the web types with `cd web && pnpm gen:api`.
2. **The runtime decides, the model recommends.** Status, budgets, permissions and acceptance
   come from deterministic code. A model output never sets a status.
3. **Evidence or `unknown`.** A value that cannot be tied to evidence is `unknown` or raises a
   validation issue, never guessed.
4. **Record everything, replay anything.** Every model call goes through the gateway so it can
   be recorded. New tests that touch a model use a cassette or `hawser.testing.StaticAdapter`;
   live calls belong in the nightly job behind a budget.
5. **Every step is idempotent** on its step key, so a resumed or re-leased run never repeats
   paid work.
6. **Tenant scoping everywhere.** Every stored object carries a tenant id; every list query
   filters on it (a test enforces this for the SQL store).

## Code style

- Python: ruff (lint + format) and pyright in strict mode, line length 100. Public functions
  have type hints and a docstring when the name is not enough.
- TypeScript: strict mode, ESLint, generated API types only (no hand-written response types).
- Keep changes focused; one logical change per pull request, with tests.

## Recording a cassette

```bash
HAWSER_RECORD_MODE=auto HAWSER_CASSETTE_DIR=path/to/cassettes ANTHROPIC_API_KEY=… \
  uv run pytest path/to/test.py -k your_test
```

Commit the cassette with the test. Cassettes store requests by hash and responses verbatim;
check that no personal data is in the input documents before committing.

## Adding a skill

A skill is a directory under `skills/` with `skill.yaml`, a schema, optional `workflow.py` and
validators, prompts, and examples. See [docs/concepts/skills.md](docs/concepts/skills.md) and
the reference skill `skills/invoice-po-reconciliation/`. Skills that claim a document class
should ship an evaluation dataset and a published result (`hawser eval run`).

## Pull requests

- `make check` passes (and `make web-check` if you touched `web/`).
- New behaviour has tests; contract changes have regenerated schemas.
- User-facing changes have a line in `CHANGELOG.md` under "Unreleased".
- By contributing you agree that your contribution is licensed under Apache-2.0.

## Code of conduct

Participation is governed by the [Code of Conduct](CODE_OF_CONDUCT.md).
