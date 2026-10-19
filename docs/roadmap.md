# Roadmap

Hawser is built in phases defined in [`Hawser_Build_Plan.md`](../Hawser_Build_Plan.md); each
phase section there ends with a status note listing what shipped and what deviated from the
plan. Architecture decisions that changed the plan are recorded in [`adr/`](adr/).

## Next

| Phase | Scope |
|---|---|
| 8 — Audio | Transcription, speaker diarisation, evidence pinned to time ranges, long-audio chunking |
| 9 — Video | Scene detection, frame sampling, a searchable time index, timestamped evidence |
| 10 — Generated-code sandbox | Agent-written code under a broker with isolation adapters, policy gating and escape tests |
| 11 — Integrations and 1.0 | MCP server and client, TypeScript SDK, Helm chart, docs site, 1.0 |

A managed, multi-tenant service (organisations, RBAC, billing, SSO) is out of scope for the
open-source edition for now; the self-hosted server already isolates tenants by API key.

## Shipped (0.1)


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
[`benchmarks/invoice-po.md`](benchmarks/invoice-po.md).

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

**Phase 7 — Image intelligence.** OCR for pages without a text layer and for photos (RapidOCR
bundled, Tesseract and Azure Document Intelligence adapters), deskew and EXIF normalisation
with recorded transform chains so evidence always maps back to the original pixels, a
`reocr` repair, layout analysis (blocks, tables, key-values), and vision tools (caption, VQA,
detect, ground, classify, tag, UI parse, segment, embed, redact, annotate, policy-gated
generate/edit). The scanned subset of the reference dataset now scores like the native pairs.

Docs: [quickstart](quickstart.md) · [skills](concepts/skills.md) ·
[validation](concepts/validation.md) · [review](concepts/review.md) ·
[evals](concepts/evals.md) · [models](concepts/models.md) ·
[tools](concepts/tools.md) · [agent](concepts/agent.md) ·
[server](concepts/server.md) · [web app](concepts/web.md) ·
[images & OCR](concepts/images.md) · [operator guide](operator/). Not yet:
audio/video (Phases 8–9), the generated-code sandbox (Phase 10).
