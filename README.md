# Hawser

**An open-source, self-hostable runtime for verifiable multimodal AI workflows.**

Hawser is the operational layer around models: it admits documents and images, plans and runs
the work with the models and tools you choose, validates the output, ties every extracted value
to the page and box it came from, repairs what it can, and routes the rest to a human reviewer.
A result is `accepted` only when deterministic checks say so; the model recommends, the runtime
decides.

```python
import hawser
from pydantic import BaseModel

class Invoice(BaseModel):
    invoice_number: str
    total: float

result = hawser.run("Extract the invoice.", inputs=["invoice.pdf"], output_schema=Invoice)
result.status                      # accepted | needs_review | rejected | failed
result.parse_as(Invoice)           # typed result
result.evidence_for("/total")      # page, bounding box and quoted text for that value
```

## What you get

- **Evidence for every field.** Values are anchored to page regions (native text or OCR) or to
  calculations. Evidence found on a cropped, rotated or rescaled image is mapped back to the
  original pixels.
- **Deterministic acceptance.** Schema checks, declarative rules
  (`approx(sum(line_items[:].line_total), subtotal)`), evidence requirements and policy decide
  the outcome. A bounded repair loop fixes what it can.
- **Three ways to run.** *Direct* (one extraction), *workflow* (a versioned skill with
  checkpointed steps) and *agent* (a planner builds a typed plan that is validated before it
  runs).
- **Your models.** Anthropic, OpenRouter, OpenAI, Ollama and vLLM through one gateway with
  capability- and policy-aware selection, fallback, budgets and recording/replay.
- **Documents and images.** PDFs with or without a text layer, photos and scans (deskew + OCR),
  layout and tables, and vision tools (detect, ground, caption, classify, UI parse, redact).
- **Human review.** A review workspace with field edits, optimistic concurrency and immutable
  machine revisions; approvals gate side-effecting tools.
- **Evaluations.** Datasets, field-level scoring, failure clusters, model comparisons and CI
  gates. The reference skill scores 100% field and status accuracy on its 60-pair benchmark,
  scanned pages included ([benchmark](docs/benchmarks/invoice-po.md)).
- **A server when you need one.** `hawserd`: REST API, durable worker with crash recovery,
  Postgres and S3-compatible storage, SSE progress, signed webhooks, and a web app for running,
  inspecting and reviewing.

## Install

```bash
pip install "hawser-ai[ocr]"          # SDK, CLI and local runtime (import name: hawser)
pip install hawser-server              # optional: hawserd, the API server and web app
```

Python 3.12+. The `ocr` extra bundles a local OCR engine (RapidOCR); leave it out if your
inputs always have a text layer.

## Try it in two minutes, without an API key

```bash
mkdir try-hawser && cd try-hawser
hawser init                 # hawser.toml, a local store and a bundled example
hawser quickstart           # replays a recorded model response: no key, no cost
hawser inspect run_…        # steps, fields with page/bbox evidence, validation, usage
```

Then with a real model:

```bash
export ANTHROPIC_API_KEY=…            # or OPENROUTER_API_KEY / OPENAI_API_KEY
hawser quickstart --live
hawser run -i "Extract the invoice." -f my-invoice.pdf --schema quickstart/invoice.schema.json
```

## Run the server

```bash
hawserd serve --dev         # SQLite + local files + in-process worker; prints a dev API key
# open http://127.0.0.1:8080/app

make up                     # or the full stack: Postgres, S3 (RustFS), hawserd, worker
hawser init --server http://localhost:8080 && export HAWSER_API_KEY=…
```

The SDK and CLI work the same against the server: `hawser.Client(base_url=…, api_key=…)` or
`hawser --server URL`.

## Documentation

- [Quickstart](docs/quickstart.md) · [Docs index](docs/README.md) ·
  [Operator guide](docs/operator/)
- [Skills](docs/concepts/skills.md) · [Validation](docs/concepts/validation.md) ·
  [Review](docs/concepts/review.md) · [Evals](docs/concepts/evals.md) ·
  [Models](docs/concepts/models.md) · [Tools](docs/concepts/tools.md) ·
  [Agent mode](docs/concepts/agent.md) · [Images & OCR](docs/concepts/images.md) ·
  [Server](docs/concepts/server.md) · [Web app](docs/concepts/web.md)
- [Architecture decisions](docs/adr/) · [Roadmap](docs/roadmap.md) ·
  [Changelog](CHANGELOG.md)

## Status

Version 0.1: documents and images, end to end. Audio, video and a generated-code sandbox are
next; see the [roadmap](docs/roadmap.md). APIs may change before 1.0, and every contract change
is versioned in [`api/schemas/`](api/schemas/) and [`api/openapi/`](api/openapi/).

## Repository layout

```
python/hawser/          hawser-ai: SDK, local runtime, CLI, contract models
python/hawser_media/    PDF, image, OCR and layout primitives
python/hawser_server/   hawserd: API, durable worker, migrations, admin
python/hawser_evals/    evaluation engine
python/hawser_synth/    synthetic documents and the reference dataset generator
web/                    the web app (React + TypeScript), served by hawserd at /app
skills/                 versioned skill bundles (invoice ↔ purchase-order reconciliation)
api/                    generated JSON Schemas and OpenAPI document (CI checks drift)
deploy/                 Docker image and Compose stack
docs/                   concepts, operator guide, ADRs, benchmarks
```

## Development

Requires [`uv`](https://docs.astral.sh/uv/) (Python 3.12 is fetched automatically) and, for the
web app, Node 20 with pnpm.

```bash
make setup        # venv, all packages, pre-commit hooks
make check        # lint + typecheck + schema/OpenAPI drift + tests (what CI runs)
make web-check    # typecheck + lint + unit tests for web/
```

Tests never call a model provider: they replay recorded responses. Server tests run on SQLite
and on an embedded Postgres, with no services to start. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

Apache-2.0. See [LICENSE](LICENSE).
