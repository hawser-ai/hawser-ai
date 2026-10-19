# Hawser

An open-source, self-hostable runtime for building **verifiable** multimodal AI workflows.

> Build verifiable multimodal AI workflows using your choice of models, tools and infrastructure.

Hawser is not a foundation model. It is the operational layer around models: planning, context,
tools, execution, validation, evidence, repair, review, policy and observability.

- Product definition: [`Hawser_Feature_Blueprint.md`](Hawser_Feature_Blueprint.md)
- Build plan: [`Hawser_Build_Plan.md`](Hawser_Build_Plan.md)
- Decisions: [`docs/adr/`](docs/adr/)

## Status

**Phase 1 — Local runtime, documents, Direct mode.** `hawser.run()` extracts a schema from a
PDF (or image / text / JSON) with page-and-bounding-box evidence for every field, validates the
result, and decides `accepted` / `needs_review` / `rejected` / `failed` deterministically.
Recording and replay make the quickstart work with no API key. Try it:

```bash
uv sync --all-packages
mkdir /tmp/try && cd /tmp/try
uv run --project <path-to-this-repo> hawser init
uv run --project <path-to-this-repo> hawser quickstart        # recorded, free
uv run --project <path-to-this-repo> hawser quickstart --live # needs ANTHROPIC_API_KEY
# or through OpenRouter (OPENROUTER_API_KEY), whose real recording is also bundled:
uv run --project <path-to-this-repo> hawser quickstart --model openrouter/anthropic/claude-opus-5.5
```

Providers: Anthropic (first-party), and any OpenAI-compatible endpoint (OpenRouter, OpenAI,
Ollama, vLLM) through one adapter.

**Phase 2 — Validation, repair, workflows, skills, review, evals.** Declarative validation
rules, a bounded repair loop, Workflow mode with checkpointed steps, skill bundles with a
lockfile, human review with immutable machine revisions, and an evaluation engine with CI gates.
The reference skill — invoice ↔ purchase-order reconciliation — ships with a generated
60-pair dataset and recorded model responses; see
[`docs/benchmarks/invoice-po.md`](docs/benchmarks/invoice-po.md).

```bash
uv run --project <repo> hawser run --skill invoice-po-reconciliation -f invoice.pdf -f purchase_order.pdf
uv run --project <repo> hawser review list
uv run --project <repo> hawser eval run --dataset datasets/invoice-po/v1 --tag smoke
```

**Phase 3 — Model gateway and tool system.** Capability- and policy-aware model selection with
strategies and policy-safe fallback, pre-call budget enforcement with usage reconciliation, an
adapter compatibility kit (`hawser models check`), and the permission-enforcing tool broker —
schema validation in and out, side-effect approvals that pause the run and resume from
checkpoints (`hawser approve`), wall-time limits. The same reference skill was evaluated
unchanged on Claude, Gemini and GPT models.

**Phase 4 — Agent mode.** A typed plan IR with a static validator, a planner with feedback
rounds and a plan cache, an executor with checkpointed nodes, budget reservation for parallel
branches and bounded loops, and a retrieval-based context manager. `hawser run --mode agent -i
"<goal>" -f a.pdf -f b.pdf`; `hawser inspect run_… --plan`. The three-mode comparison on the
reference skill is published in the benchmark page.

**Phase 5 — Control plane and durable execution.** `hawserd` (the `hawser-server` package):
a REST API generated into `api/openapi/openapi.json`, API keys and tenancy, Postgres and
S3-compatible storage behind the same store protocols, a leased durable worker that resumes crashed
runs from step checkpoints, SSE event streams, signed webhooks with retries and a dead-letter
list, Docker Compose, and an SDK/CLI HTTP backend. A conformance suite runs the same scenarios
against the local runtime and the server. Python end to end — see ADR-0010 for why Go and
Temporal were dropped.

```bash
make up                                            # postgres + rustfs (s3) + hawserd + worker
hawser init --server http://localhost:8080 && export HAWSER_API_KEY=<printed key>
hawser run -i "Extract the invoice." -f invoice.pdf --schema quickstart/invoice.schema.json
hawserd serve --dev                                # or: SQLite + in-process worker, no Docker
```

**Phase 6 — Web application.** Served by `hawserd` at `/app`: playground with drag-drop
admission and live SSE progress, runs list, run inspector (annotated result, plan graph, trace,
validation, artifacts, revisions), evidence viewer with highlighted regions and keyboard
navigation, review workspace with conflict handling, evaluation dashboard and comparison,
registry, usage, policy editor, webhooks. React + TypeScript (`web/`), types generated from the
OpenAPI document, Playwright end to end against a real `hawserd`.

```bash
make web && hawserd serve --dev            # then open http://127.0.0.1:8080/app
```

Docs: [quickstart](docs/quickstart.md) · [skills](docs/concepts/skills.md) ·
[validation](docs/concepts/validation.md) · [review](docs/concepts/review.md) ·
[evals](docs/concepts/evals.md) · [models](docs/concepts/models.md) ·
[tools](docs/concepts/tools.md) · [agent](docs/concepts/agent.md) ·
[server](docs/concepts/server.md) · [web app](docs/concepts/web.md) ·
[operator guide](docs/operator/). Not yet: OCR for scanned pages (Phase 7), audio/video
(Phases 8–9), the generated-code sandbox (Phase 10).

## Repository layout

```
api/schemas/            generated JSON Schema for every contract model (CI checks drift)
api/openapi/            generated OpenAPI document for hawserd (CI checks drift)
api/proto/              reserved for internal RPC contracts (unused; ADR-0010)
python/hawser/          the `hawser` package: SDK, local runtime, CLI, contract models
python/hawser_server/   `hawserd`: API, durable worker, migrations, admin (Phase 5)
python/hawser_media/    document, image, audio, video primitives (Phase 1+)
python/hawser_evals/    evaluation engine (Phase 2)
python/hawser_synth/    synthetic documents and datasets for tests and evals
sandbox/                generated-code sandbox broker and adapters (Phase 10)
skills/                 versioned skill bundles (Phase 2)
mcp/                    MCP server and client adapters (Phase 11)
web/                    the web app: playground, inspector, evidence, review, evals (Phase 6)
deploy/                 docker image, compose (Phase 5); helm (Phase 11)
examples/               reference workflows and the quickstart
docs/                   ADRs, architecture, security, operator guides
```

## Development

Requires [`uv`](https://docs.astral.sh/uv/). Python 3.12 is fetched automatically.

```bash
make setup      # venv, all packages, pre-commit hooks
make check      # lint + typecheck + schema/OpenAPI drift + tests (what CI runs)
make schemas    # regenerate api/schemas after changing hawser.models
make openapi    # regenerate api/openapi/openapi.json after changing hawserd routes
make web-check  # typecheck + lint + unit tests for web/ (Node 20, pnpm)
make web        # build web/ and bundle it into hawser-server
```

Tests never call a model provider; everything runs from recorded cassettes
(`HAWSER_RECORD_MODE=replay`). Server tests run on SQLite and on an embedded Postgres
(`pgserver`, no service needed); set `HAWSER_TEST_DATABASE_URL` to use your own or
`HAWSER_SKIP_PG=1` to skip. Live runs are nightly and budget-capped.

## Licence

Apache-2.0. See [`LICENSE`](LICENSE).
