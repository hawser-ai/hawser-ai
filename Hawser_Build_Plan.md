# Hawser — Phased Build Plan

Companion to `Hawser_Feature_Blueprint.md` v1.0 · 18 October 2026 · Plan version 1.0

This plan turns the blueprint into an ordered sequence of phases. Each phase has a goal, explicit in/out scope, work packages with concrete tasks, the design details that must be decided inside the phase, the tests that prove it, exit criteria (a demo plus a checklist) and an effort estimate.

Section numbers in brackets like **[B§15]** refer to sections of the blueprint.

---

## 0. How to read this plan

### 0.1 Assumptions

| Assumption | Value used in this plan | If different |
|---|---|---|
| Team | 2 full-time engineers, both comfortable in Python; one comfortable in Go and TypeScript | Solo: see §16 "Solo cut". 4+: see §15 "Parallelisation". |
| Primary language of early phases | Python | — |
| First modality | Documents (PDF, images of documents) | — |
| First model provider | Anthropic (Claude), then OpenAI-compatible (covers OpenAI, vLLM, Ollama, OpenRouter) | — |
| Licence | Apache-2.0 for everything in the open-source edition | — |
| Deployment target for v1 | A developer's laptop (`pip install hawser`), then Docker Compose | — |
| Estimates | Calendar weeks for the assumed team, including tests and docs, excluding holidays | Multiply by ~1.4 for one engineer who also does everything else |

### 0.2 Two deliberate deviations from the blueprint

**Deviation 1 — Python-first, server later.** The blueprint's reference architecture is Go control plane + Python workers + gRPC + Temporal + Postgres + S3 **[B§25]**. The blueprint also demands a ten-minute quickstart **[B§2, B§30]**. These conflict if the server stack is built first. This plan builds an **in-process Python runtime** (`LocalRuntime`) in Phases 1–4 that implements the *exact* public result contract, run state machine, event stream and storage interfaces, then adds the Go control plane and Temporal in Phase 5 as a second backend behind the same SDK. The blueprint allows "a simpler local runner may support development" **[B§25]**; this plan promotes that runner to the main way developers first meet the product. The rule "only one production durability model" still holds: `LocalRuntime` is documented as development-grade; Temporal is the only production mode.

Every contract in Phases 1–4 is designed so that Phase 5 is a *re-hosting*, not a rewrite:
- All domain objects are Pydantic models exported to JSON Schema in CI, so Go types are generated, not hand-written.
- Storage is behind `ArtifactStore`, `MetadataStore`, `EventSink` protocols from day one.
- Steps are idempotent on a `step_key` from day one, so Temporal activities can wrap them unchanged.
- Model and tool calls are recorded/replayed from day one, so conformance tests run identically against both backends.

**Deviation 2 — One user-facing CLI.** The blueprint lists a Go CLI **[B§22]**. This plan keeps a single Python CLI (`hawser`) as the developer CLI throughout, because it must drive the local runtime, evals and skill tooling, which are Python. The Go binary is the server (`hawserd`) with an operator sub-command set (`hawserd migrate`, `hawserd admin ...`). If a Go CLI is still wanted later for distribution reasons, it is a thin OpenAPI client and can be generated in Phase 11.

### 0.3 Principles that apply in every phase

1. **Contract first.** No feature is "done" until its request/response/event shapes exist as Pydantic models, are exported to `api/schemas/`, and have a drift test.
2. **Record everything, replay anything.** Every model call and tool call is recordable. Every test that touches a model runs from a cassette. Live calls happen only in explicitly budgeted nightly jobs.
3. **The runtime decides, the model recommends.** Status, budgets, permissions and acceptance are computed by deterministic code. A model output never sets `status`.
4. **Evidence or `unknown`.** A field that cannot be anchored to evidence is either marked `unknown` or produces a validation issue. There is no third option.
5. **Tenant column from the first migration.** Even in the local runtime, every stored object has a `tenant_id` (default `local`). Retrofitting tenancy is the most expensive mistake available.
6. **One reference workflow, run on every commit.** Invoice ↔ PO reconciliation is the canary for every phase from Phase 2 onwards.
7. **Phase gates are demos.** A phase ends when the demo in its exit criteria runs from a clean checkout in CI.

### 0.4 Phase map

| Phase | Name | Core outcome | Est. (weeks) | Cumulative |
|---|---|---|---|---|
| 0 | Foundations | Repo, contracts, ADRs, CI, identifiers, state machine | 2 | 2 |
| 1 | Local runtime + documents + Direct mode | `hawser.run()` extracts a schema from a PDF with page/bbox evidence, recorded or live | 5 | 7 |
| 2 | Validation, repair, review, Workflow mode, skills, evals, reference workflow | Invoice↔PO reconciliation skill passes a published eval; `needs_review` works end-to-end | 6 | 13 |
| 3 | Model gateway + tool system | Multi-provider, capability-aware selection, policy-checked fallback, tool broker with permissions | 4 | 17 |
| 4 | Agent mode | Typed plans, planner, budget reservation, context manager; three-mode comparison on the eval | 5 | 22 |
| 5 | Control plane + durable execution | Go API, Postgres, S3, Temporal, Python workers, Compose; SDK works against server | 8 | 30 |
| 6 | Web application | Playground, trace inspector, evidence viewer, review workspace, eval dashboard | 6 | 36 |
| 7 | Image intelligence | Detection, grounding, segmentation, OCR adapters, coordinate chains, redaction | 4 | 40 |
| 8 | Audio intelligence | Transcription, diarisation, timed evidence, long-audio chunking | 3 | 43 |
| 9 | Video intelligence | Scene detection, sampling, temporal index, retrieval, timestamped evidence | 4 | 47 |
| 10 | Generated-code sandbox | Broker, isolation adapters, code mode gating, escape test suite | 4 | 51 |
| 11 | Integrations + distribution | MCP server/client, TypeScript SDK, Helm, docs site, 1.0 release | 3 | 54 |
| 12 | Multitenancy + managed service | Orgs/RBAC/quotas/billing/SSO/audit; Hawser Cloud | 8+ | 62+ |

Phases 7, 8 and 9 do not depend on 5 or 6 and can run in parallel with them given a third engineer (§15).

---

## Phase 0 — Foundations (2 weeks)

### Goal
A repository where the second commit of a feature is cheaper than the first: tooling, contracts, identifiers, error taxonomy, run state machine and decision records exist before any feature code.

### Scope
**In:** repo layout, toolchain, CI skeleton, core domain models (empty behaviour, full shape), JSON Schema export and drift check, ADRs for the decisions below, naming/licence checks.
**Out:** any model call, any PDF parsing.

### Work packages

#### WP0.1 — Repository and toolchain
- Create the monorepo with the blueprint's layout **[B§32]**. Create empty directories with `README.md` placeholders so the structure is visible from day one:
  ```
  api/schemas/            generated JSON Schema (committed; CI checks drift)
  api/openapi/            openapi.yaml (hand-written from Phase 5; stubs earlier)
  api/proto/              (Phase 5)
  control/                (Phase 5, Go)
  python/hawser/          the `hawser` package: SDK + local runtime + CLI
  python/hawser_workers/  (Phase 5) Temporal activities wrapping hawser.*
  python/hawser_media/    document/image/audio/video primitives
  python/hawser_evals/    evaluation engine
  sandbox/                (Phase 10)
  skills/                 versioned skill bundles
  mcp/                    (Phase 11)
  web/                    (Phase 6)
  deploy/compose/ deploy/helm/
  examples/
  docs/                   adr/, architecture/, security/, operator/
  ```
  Note: the blueprint names `python/hawser_sdk`, `python/workers`, `python/media`, `python/evals`. This plan uses importable package names (`hawser`, `hawser_workers`, `hawser_media`, `hawser_evals`). Record this in ADR-0001.
- Python: 3.12 minimum, `uv` workspace with one `pyproject.toml` per package, `ruff` (lint + format), `pyright` strict on `hawser`, `pytest` + `pytest-xdist`, `hypothesis` available.
- Go (placeholder module, Phase 5): `go.mod` at `control/`, `golangci-lint` config committed now.
- Web (placeholder, Phase 6): `pnpm-workspace.yaml`.
- Pre-commit: ruff, pyright, schema drift check, `go vet` (when Go exists), secret scan (`gitleaks`).
- CI (GitHub Actions): lint → typecheck → unit → contract tests → recorded integration tests; matrix on macOS + Linux, Python 3.12/3.13. Nightly job placeholder for live-model evals with a budget cap env var.
- `Makefile`/`justfile` targets: `setup`, `lint`, `test`, `test-live`, `schemas`, `docs`.

#### WP0.2 — Identifiers, hashing, time
- IDs: prefixed ULIDs. Prefixes: `ten_`, `prj_`, `asset_`, `art_`, `run_`, `step_`, `evt_`, `ev_` (evidence), `iss_` (issue), `rev_` (revision), `skl_`, `tool_`, `mdl_`, `ds_` (dataset), `eval_`, `rvw_`, `apr_` (approval), `whk_`, `dlv_` (delivery). Implement `hawser.ids.new_id(prefix)` and `parse_id()`; reject malformed IDs at every boundary.
- Content hash: `sha256` of bytes. Tenant-scoped hash (used for dedup keys and as the artifact key) = `sha256(tenant_id + "\x00" + sha256(bytes))`, so a cross-tenant request cannot probe whether another tenant has a given file **[B§4.2]**. Store both; expose only the tenant-scoped one.
- Time: all timestamps UTC, RFC 3339 with milliseconds. Monotonic clock for latency.

#### WP0.3 — Core domain models (shape only)
Create `hawser/models/` with Pydantic v2 models. These are the public contract; everything else is implementation.

```python
# hawser/models/run.py
class RunStatus(StrEnum):
    queued = auto(); admitting = auto(); planning = auto(); running = auto()
    validating = auto(); repairing = auto(); awaiting_review = auto()
    awaiting_approval = auto()
    accepted = auto(); needs_review = auto(); rejected = auto()
    failed = auto(); cancelled = auto()

TERMINAL = {accepted, needs_review, rejected, failed, cancelled}

class RunRequest(BaseModel):
    instruction: str | None
    inputs: list[InputRef]              # paths, bytes, asset ids, artifact ids, storage refs
    output_schema: JsonSchema | None    # already converted from Pydantic by the SDK
    skill: SkillRef | None
    mode: Literal["direct","workflow","agent"] | None
    policy: Policy
    tools: list[str] | None             # permitted tool names, overrides skill default (narrowing only)
    models: ModelPreference | None
    metadata: dict[str, str]            # caller tags
    idempotency_key: str | None

class RunResult(BaseModel):             # the response contract [B§2]
    run_id: str
    status: RunStatus
    result: Any | None                  # conforms to output_schema when present
    field_meta: dict[str, FieldMeta]    # sidecar keyed by JSON pointer: evidence + validation per field
    evidence: list[Evidence]
    validation: ValidationReport
    artifacts: list[ArtifactRef]
    usage: Usage
    trace_id: str
    revision_id: str                    # the machine revision; reviewed revisions are separate
    warnings: list[Warning]
```

Design note: evidence and validation for fields live in a **sidecar** (`field_meta`, keyed by JSON pointer like `/line_items/3/unit_price`) rather than wrapping each value. `result` therefore validates against the caller's schema unchanged, and the blueprint's `{value, evidence, validation}` example **[B§15]** is produced by a helper `result.annotated()` for display.

Other models to define now (fields only, behaviour later): `Asset`, `AssetManifest`, `Artifact`, `Evidence` (all kinds in **[B§15]** as a discriminated union), `Transform` (affine + crop + resample + time-offset), `Policy` (all limits in **[B§10.3]** plus allow-lists, privacy, fallback), `Usage`/`ModelCallUsage`/`ToolCallUsage`, `Step`, `Event` (discriminated by `type`), `ValidationIssue`, `ValidationReport`, `Revision`, `SkillManifest`, `ToolSpec`, `ModelSpec`, `PlanIR` (Phase 4 fills it; reserve the name), `HawserError` taxonomy (below).

- Export: `python -m hawser.models.export → api/schemas/*.schema.json`. CI fails if the committed files differ.
- Schema versioning: every exported schema carries `x-hawser-contract-version` (semver, starts `0.1.0`). Breaking changes bump major and require an ADR.

#### WP0.4 — Error taxonomy
`hawser.errors`:

| Class | Retryable | Maps to run status | Example |
|---|---|---|---|
| `AdmissionError` | no | `failed` (never enters runtime) | oversized PDF, encrypted file |
| `PolicyViolation` | no | `failed` | model not in allow-list |
| `BudgetExceeded` | no | `failed` or `needs_review` per policy | cost cap hit mid-run |
| `PlanInvalid` | no | `failed` | unknown tool in plan |
| `ProviderError` | yes/no flag | retried then `failed` | 429, 5xx, timeout |
| `ToolError` | per ToolSpec | retried then `failed` | OCR engine crash |
| `SandboxDenied` | no | `failed` | code mode requested but no sandbox |
| `ReviewRequired` | n/a (signal) | `needs_review` | not an exception; a status outcome |
| `Cancelled` | n/a | `cancelled` | — |

Every error has `code` (stable string), `message`, `details` (dict), `retryable`, `run_id`, `step_id`.

#### WP0.5 — Run state machine
Implement `hawser.runtime.state.transition(from, to)` with the allowed-edge table, and a test that enumerates every edge. Edges as implemented:

```
queued → admitting → planning → running → validating
validating → accepted | repairing | needs_review | rejected
repairing → running | needs_review              (gives up to review when attempts/budget are exhausted)
running → awaiting_approval → running | rejected
needs_review → accepted | rejected | cancelled   (reviewer decision; creates a reviewed revision)
any active/paused state → failed | cancelled
```
`needs_review` is a *resting* state, not a terminal one: machine work is finished and only a reviewer decision moves it on. It cannot `fail` (nothing is running) but can be cancelled. The server (Phase 5) models it as a durable wait on a `review_decision` signal. See `docs/architecture/run-lifecycle.md`.

#### WP0.6 — Decision records
Write ADRs (short, template in `docs/adr/0000-template.md`):
- 0001 Repo layout and package names
- 0002 Python-first local runtime; Temporal is the only production durability model
- 0003 Single Python CLI; Go server binary
- 0004 Pydantic models are the source of truth; JSON Schema generated; Go types generated
- 0005 Evidence sidecar (`field_meta`) rather than wrapped values
- 0006 Identifier scheme and tenant-scoped hashing
- 0007 PDF engine: `pypdfium2` (Apache/BSD) not PyMuPDF (AGPL)
- 0008 Recording/replay is mandatory for all tests touching models or tools
- 0009 Licence: Apache-2.0; CLA/DCO choice; trademark check on "Hawser" done

### Tests
- Schema drift test; ID round-trip property tests; state-machine edge enumeration; error serialisation round-trip.

### Exit criteria
- `uv run pytest` green on a clean clone in CI on both OSes.
- `api/schemas/` contains every core model.
- ADRs 0001–0009 merged.

### Risks
- Over-designing models before use. Mitigation: mark fields `Phase: n` in docstrings; it is acceptable to change them until contract 1.0.

---

## Phase 1 — Local runtime, document ingestion, Direct mode (5 weeks)

### Goal
`hawser.run(instruction=..., inputs=["invoice.pdf"], output_schema=Invoice)` returns a `RunResult` whose fields carry page + bounding-box evidence, works with a recorded cassette and no provider key, and works live with an Anthropic key. The quickstart takes under ten minutes **[B§30]**.

### Scope
**In:** asset admission for PDF/PNG/JPEG/text/JSON; local artifact store; document text extraction and page rendering; model gateway v0 (Anthropic + recorded); structured output; evidence anchoring v1; trace/events; `hawser init|run|inspect`; recorded example.
**Out:** validation rules beyond schema conformance, repair, review, workflows, skills, agent planning, OCR for scanned PDFs (Phase 7 — but the admission layer must *detect* scanned pages now and report `best-effort`), audio/video.

### Work packages

#### WP1.1 — Asset admission and inspection [B§4.2]
Module `hawser_media/admission/`.
- Type sniffing by magic bytes (`python-magic` or `filetype`), never by extension alone; mismatch between declared and sniffed type → `AdmissionError(code="media_type_mismatch")`.
- Limits (configurable in `Policy.limits` and global config): `max_bytes` (default 50 MB), `max_pages` (200), `max_pixels` per image (40 MP), `max_total_pixels` per run. Pillow `MAX_IMAGE_PIXELS` set explicitly; decompression-bomb check before decode.
- Probes:
  - PDF: `pypdfium2` — page count, per-page size in points, encrypted?, has-text-layer? (character count per page), estimated render pixels at the configured DPI, XFA/forms presence. Corrupt → `AdmissionError(code="pdf_corrupt")`. Encrypted → `pdf_encrypted` (no password support in Phase 1).
  - Image: dimensions, mode, EXIF orientation, ICC presence, animation frames (reject animated beyond 1 frame).
  - Text/JSON/Markdown: size, UTF-8 validity, JSON parse.
- `AssetManifest` records every probe result plus the two hashes, the limits applied, and `execution_classes` feasible (e.g. `["document.native_text"]` vs `["document.needs_ocr"]`) with a rough cost estimate.
- Idempotent admission: same bytes + tenant → same `asset_id` (keyed by tenant-scoped hash).

#### WP1.2 — Artifact store [B§19]
`hawser/storage/artifacts.py`:
```python
class ArtifactStore(Protocol):
    def put(self, tenant: str, data: bytes | BinaryIO, *, media_type: str, meta: ArtifactMeta) -> ArtifactRef
    def get(self, ref: ArtifactRef) -> BinaryIO
    def stat(self, ref: ArtifactRef) -> ArtifactMeta
    def url(self, ref: ArtifactRef, ttl_s: int) -> str     # file:// for local; signed in Phase 5
    def delete(self, ref: ArtifactRef) -> None             # honours retention policy
```
- `LocalArtifactStore`: content-addressed under `~/.hawser/store/<tenant>/<hash[:2]>/<hash>`; metadata in SQLite (`~/.hawser/hawser.db`) via the `MetadataStore` protocol.
- Lineage: `ArtifactMeta.parents: list[ArtifactRef]`, `producer: ProducerRef`, `transform: Transform | None`.
- `MetadataStore` protocol (SQLite now, Postgres in Phase 5): assets, artifacts, runs, steps, events, results. Keep SQL simple and portable (no SQLite-only features) so the Phase 5 port is mechanical.

#### WP1.3 — Document primitives v1 [B§5]
`hawser_media/document/`:
- `extract_text(asset) -> DocumentTextIndex`: per page, ordered text with **character-level boxes** from pypdfium2 (`get_charbox`), grouped into words and lines with a simple reading-order heuristic (top-to-bottom, left-to-right, column detection deferred). Coordinates in PDF points, origin top-left (normalise pdfium's bottom-left origin here and record `coordinate_system="pdf_points_tl"`).
- `render_page(asset, page, dpi) -> ArtifactRef` (PNG) with the `Transform` from points→pixels recorded so pixel evidence maps back to points.
- `page_index(asset) -> PageIndex`: per-page character count, has-text, dimensions, render artifact ids (lazy).
- `DocumentTextIndex` is itself stored as an artifact (JSON) so it is reusable and replayable.
- Scanned-page detection: a page with `< N` characters but non-trivial raster content is flagged `needs_ocr`. In Phase 1 such pages are passed to the model as images and the result is marked best-effort; evidence anchoring falls back to page-level evidence (`bbox=None`, `anchor_confidence=0`).

#### WP1.4 — Model gateway v0 [B§9]
`hawser/gateway/`:
```python
class ModelAdapter(Protocol):
    spec: ModelSpec
    def complete(self, req: ModelRequest) -> ModelResponse
    def stream(self, req: ModelRequest) -> Iterator[ModelEvent]   # optional in v0
```
- `ModelRequest`: `system`, `messages[]` where a message is a list of parts (`text`, `image(artifact_ref, media_type)`, `document(artifact_ref)` for providers that accept PDFs natively), `output_schema` (JSON Schema), `tools[]` (Phase 3), `max_output_tokens`, `temperature`, `metadata`.
- `ModelResponse`: `parsed` (when schema given), `text`, `usage` (`input_tokens`, `output_tokens`, `cache_read_tokens`, `cost_usd` computed from `ModelSpec.pricing`), `latency_ms`, `provider_request_id`, `stop_reason`, `raw` (redactable).
- Adapters in Phase 1: `AnthropicAdapter` (structured output via a single forced tool call whose input schema is the output schema; images and PDFs as parts), `RecordedAdapter` (below). OpenAI-compatible adapter is Phase 3 but keep the request shape provider-neutral now.
- `ModelSpec` registry file `hawser/gateway/registry.yaml`: id, provider, modalities, context window, max output, structured-output support, tool support, pricing per million tokens, regions, data-handling flags. Load at import; validated against the `ModelSpec` schema.
- Retries: exponential backoff with jitter on 429/5xx/timeout, max 3, honour `Retry-After`; every attempt emitted as an event.
- Redaction: `raw` provider payloads stored only when `config.trace.store_raw=True`; by default store request metadata + parsed output.

#### WP1.5 — Recording and replay [B§20, P0.3]
`hawser/recording/`:
- `Cassette` = directory of JSON files. Key = `sha256(canonical_json({"kind":"model","provider","model","request_without_volatile_fields"}))`; same for tools (`kind="tool"`). Images/documents inside requests are referenced by artifact hash, not bytes, so cassettes are small.
- Modes via `HAWSER_RECORD_MODE`: `replay` (default in tests; miss → error), `record` (always call live and overwrite), `auto` (replay hit, record miss), `live` (never touch cassettes).
- The recorded example shipped in `examples/quickstart/` includes its cassette, so `hawser run` works with no key **[B§2]**.
- Replay is marked in `Usage` (`replayed=True`, `cost_usd=0`) and in the trace, so nobody mistakes replay for a live result **[B§20]**.

#### WP1.6 — Structured output [B§14]
`hawser/structured/`:
- Accept Pydantic classes or JSON Schema. Normalise to JSON Schema draft 2020-12; reject features the provider cannot enforce (report in `warnings`).
- **Extraction envelope**: the model is not asked for the user's schema directly. The runtime wraps it: for every leaf field `f` the model must return `{"value": ..., "source_text": str | null, "page": int | null, "status": "found" | "unknown" | "not_applicable"}`. The runtime then unwraps `value` into `result` (so `result` conforms to the user schema), and uses `source_text`/`page` for anchoring. Nullable/optional fields map to `unknown`. This is the mechanism behind "explicit unknown values" and "field-level evidence" **[B§14]**.
- Schema size guard: if the envelope exceeds provider limits, fall back to per-section extraction (split top-level object keys into groups) — simple, deterministic, logged.
- Partial results: on parse failure, retain the parsable subset, mark the rest `unknown`, and produce a `schema_parse` validation issue (Phase 2 turns that into repair).

#### WP1.7 — Evidence anchoring v1 [B§15]
`hawser/evidence/anchor.py`:
1. Normalise `source_text` (collapse whitespace, unify quotes/dashes, NFKC).
2. Search the page's normalised text for an exact match; if `page` is null, search all pages (prefer the first hit; record ambiguity).
3. On miss, `rapidfuzz.fuzz.partial_ratio_alignment` over the page text; accept ≥ 88, record `anchor_confidence = score/100`.
4. Map the matched character range back to character boxes → union bbox (and per-line bboxes for multi-line matches).
5. Emit `Evidence(kind="document_region", asset_id, page, bbox, coordinate_system="pdf_points_tl", text=matched_text, producer={tool:"evidence.anchor", version}, confidence)`.
6. Miss → no evidence; `field_meta[ptr].issues += ["evidence_missing"]`. Phase 2 decides what that means for acceptance.
- Numeric values: when `source_text` is a number that appears many times, prefer the occurrence nearest to a label match (the field name tokens) — a simple proximity heuristic; log when it is used.

#### WP1.8 — Runtime core and Direct mode [B§3]
`hawser/runtime/`:
- `Runtime` protocol: `submit(RunRequest) -> RunHandle`, `get(run_id) -> RunResult`, `events(run_id) -> Iterator[Event]`, `cancel(run_id)`.
- `LocalRuntime`: synchronous execution in-process (thread for `submit_async`). Step execution through `StepExecutor.execute(step_key, fn, *args)` which: checks for an existing checkpointed output for `(run_id, step_key)` → returns it; otherwise runs, stores output as artifact/metadata, emits `step.started/step.completed/step.failed`.
- Direct mode pipeline: admit → build document index → build prompt (instruction + envelope schema + page texts/images under a context budget) → model call → unwrap → anchor → assemble `RunResult` with `status=accepted` if schema-valid, else `failed` (Phase 2 replaces this crude rule with validation + policy).
- Context budget v1: include native text for text pages; include rendered page images only for pages flagged `needs_ocr` or when the instruction mentions layout/visual terms; cap total tokens at 60% of the model's context, dropping lowest-priority pages and recording `context.selection` events **[B§13]**.
- Events (append-only JSONL per run in local mode): `run.created`, `run.status_changed`, `asset.admitted`, `step.*`, `model.called`, `context.selected`, `evidence.anchored`, `result.assembled`.
- `Usage` totals assembled from events, not from counters, so they reconcile **[B§30]**.

#### WP1.9 — SDK surface and CLI [B§2, B§21]
- `hawser.run(...)` convenience; `hawser.Client(runtime=LocalRuntime())` for explicit use; `Client.runs.get/list/events/cancel`.
- CLI (`typer`): `hawser init` (creates `hawser.toml`, `.hawser/`, sample schema, sample policy, copies the quickstart example), `hawser run --instruction ... --input ... --schema schema.json|module:Class --policy policy.yaml --mode direct` (prints status, result, usage; `--json`), `hawser inspect <run_id>` (timeline of events, steps, model calls with tokens/cost, evidence per field; `--field /invoice_number` drills down), `hawser models list`.
- Config precedence: CLI flags > env (`HAWSER_*`) > `hawser.toml` > defaults. Provider keys from env only.

#### WP1.10 — Quickstart and docs
- `docs/quickstart.md`: install, `hawser init`, run recorded example (no key), run live with key, read `inspect` output. Time it with a stopwatch on a fresh machine; record the time in the PR.
- `examples/quickstart/`: one invoice PDF (synthetic, generated by a script in `examples/_gen/` so there are no licensing questions), `Invoice` schema, cassette.

### Tests
- Admission: fixture corpus of malformed/oversized/encrypted/mismatched files; property test that admission never raises anything but `AdmissionError`.
- Document: golden tests for text index and char boxes on 5 synthetic PDFs; coordinate round-trip (points→pixels→points) within 0.5pt.
- Gateway: adapter contract test (request → response shape) run against `RecordedAdapter`; retry/backoff with a fake clock.
- Anchoring: table-driven cases (exact, whitespace drift, OCR-like typos, number near label, miss).
- End-to-end: quickstart runs in replay mode in CI; live run in the nightly job with `HAWSER_LIVE_BUDGET_USD=1`.

### Exit criteria (demo)
From a clean clone: `pip install -e python/hawser && hawser init && hawser run` returns `accepted` with evidence for ≥ 90% of fields on the recorded example without a key; the same with `ANTHROPIC_API_KEY` set runs live; `hawser inspect` shows per-field page/bbox. Measured quickstart time ≤ 10 minutes.

### Estimate: 5 weeks.

### Risks
- pypdfium2 reading order on multi-column pages is poor → accepted for Phase 1; column detection lands in Phase 7 with layout analysis.
- Provider structured-output quirks (nested enums, `additionalProperties`) → schema normaliser with explicit `warnings`.

### Status (2026-10-18): implemented
All work packages landed. Notes on what differs from the text above:
- **Validation core arrived early.** `hawser.validation` ships the `Validator` protocol, four core rules (`core.schema`, `core.unknown_required`, `core.evidence_missing`, `core.evidence_consistent`) and the deterministic `decide()` from WP2.2, so Phase 1 already returns `needs_review`/`rejected` instead of the crude "failed on schema error" rule. Repair strategies are still Phase 2; when `decide()` says `repairing` the runtime routes per policy and records a `repair_unavailable` warning.
- **Structured output** uses Anthropic's native `output_config.format` (JSON Schema), not a forced tool call — forced `tool_choice` is rejected on current models.
- **A fifth workspace package, `hawser_synth`**, holds the synthetic invoice generator (reportlab) with exact ground truth and printed `source_texts`; it feeds tests, the quickstart and (Phase 2) the eval dataset.
- **The quickstart bundle ships two cassettes.** The entry for the default model (`anthropic/claude-opus-5-5`) is `source: synthetic` — built by running the real pipeline with a static adapter returning the generator's ground truth, because no first-party key was available. The entry for `openrouter/anthropic/claude-opus-5.5` is `source: live`: a real Claude Opus 5.5 run through OpenRouter (4,269 in / 1,566 out tokens, $0.048, 18 s) that matched the ground truth exactly with every field anchored. The builder preserves live entries across rebuilds.
- **OpenAI-compatible adapter pulled forward from Phase 3** (`hawser.gateway.openai_compat`): one class with OpenRouter / OpenAI / Ollama / vLLM profiles, provider-reported cost (OpenRouter) overriding registry pricing, and `metadata.raw` upstream error extraction.
- **Envelope redesigned after contact with the real API.** Anthropic's structured-output compiler caps union-typed parameters at 16 and rejects large grammars outright; the original four-key envelope (`value|null`, `source_text|null`, `page|null`, `status` enum) failed on a 15-field invoice. The envelope is now three keys with **no unions**: `value` (leaf type, nullability stripped), `source_text` (`""` = none), `found` (bool). Not-found leaves carry a typed placeholder the runtime discards. Page hints were dropped; the anchorer searches all pages. The gateway also falls back to prompt-guided JSON (parsed from text, flagged `structured_output_fallback`) if a provider still rejects a grammar.
- **Anchoring refinement:** exact matches prefer word boundaries, and short values (quantities) are placed by row proximity to already-anchored sibling fields of the same array element.
- **Test layout:** pytest runs in `--import-mode=importlib` so each package keeps its own `tests/` directory without package-name collisions.
- Measured quickstart: `hawser init` + `hawser quickstart` complete in ~2 s on a laptop (excluding `uv sync`).

---

## Phase 2 — Validation, repair, review, Workflow mode, skills, evals, reference workflow (6 weeks)

### Goal
The invoice ↔ purchase-order reconciliation skill **[B§29]** runs as a deterministic workflow, validates arithmetic and cross-document rules, repairs bounded failures, routes residual failures to `needs_review`, and publishes eval metrics from `hawser eval`. "Validation failures cannot be silently labelled accepted" **[B§30]** becomes enforced.

### Scope
**In:** validator framework and built-ins, issue model, acceptance policy, repair loop with no-progress detection, Workflow mode and step DSL, skill bundle format + loader + lockfile, review states + revisions + minimal CLI review, eval datasets/scorers/CLI/CI gate, the reference workflow and its dataset.
**Out:** agent planning, multi-provider selection (Phase 3), review UI (Phase 6).

### Work packages

#### WP2.1 — Validation framework [B§16]
`hawser/validation/`:
```python
class Validator(Protocol):
    id: str            # e.g. "core.required_fields", "skill.invoice_po.totals_match"
    version: str
    def validate(self, ctx: ValidationContext) -> list[Issue]

class Issue(BaseModel):
    id: str; rule_id: str; severity: Literal["error","warning","info"]
    field: str | None            # JSON pointer
    message: str
    evidence: list[str]          # evidence ids supporting the finding
    repair: Literal["retrieve","reocr","remodel","ask_field","recalculate","reexecute","review","none"]
    details: dict
```
- `ValidationContext`: `result`, `field_meta`, `evidence`, `assets`, `document indexes`, `run policy`, `skill config`, helper accessors.
- Built-ins: `schema` (JSON Schema, with precise pointers), `required_fields`, `type_format` (dates, currencies, IBAN, email, regex), `arithmetic` (declarative: `sum(line_items[].total) == subtotal ± tolerance`, `subtotal + tax == total`), `cross_field` (expression mini-language using `simpleeval`-style safe evaluation over the result), `cross_document` (join two results on keys; declarative comparisons), `evidence_present` (configurable per field: required/preferred/optional), `evidence_consistent` (anchored text must contain the value or a normalised form of it), `unknown_budget` (max fraction of unknown fields), `policy_safety` (output must not contain disallowed categories — pluggable).
- Model-based judge validator exists as a plugin point (`judge.llm`) but is **off by default** and requires an explicit policy flag **[B§16]**.
- `ValidationReport`: issues, counts by severity, `rules_run[]` with versions, `duration_ms`.

#### WP2.2 — Acceptance policy
Deterministic function `decide(report, policy, attempts) -> Decision`:
1. No `error` issues → `accepted`.
2. `error` issues exist and all are `repair != "none"|"review"` and `attempts < policy.repair_attempts` → `repairing`.
3. Otherwise `policy.review_on_failure` → `awaiting_review` (reported `needs_review`); else `rejected`.
4. `warning` issues never block; they are reported.
Policy can escalate specific rules (`policy.validation.escalate: {"evidence_missing": "error"}`) or downgrade them.

#### WP2.3 — Repair loop [B§16]
`hawser/runtime/repair.py`:
- Strategy per `Issue.repair`:
  - `retrieve`: re-run extraction for only the failing fields with a narrower context window (the page/region where a label match was found, or the page the model named).
  - `reocr`: Phase 7 (stub records "not available").
  - `remodel`: Phase 3 (needs selection); in Phase 2 re-run with the same model, temperature 0, and the issue messages appended as corrections.
  - `ask_field`: ask only for the listed fields using the envelope, with the original values and the issue text, instruct to return `unknown` if not present.
  - `recalculate`: re-run deterministic steps (totals) — never calls a model.
  - `reexecute`: re-run a named workflow step.
- Bounds: `policy.repair_attempts`, `policy.repair_max_cost_usd`, `policy.repair_max_seconds`.
- No-progress detection: fingerprint = hash of (sorted issue rule_ids+fields, sorted evidence ids, result hash). If a repair attempt produces the same fingerprint as any previous attempt, stop and record `repair.no_progress` **[B§16]**.
- All repairs are steps with their own events; `hawser inspect` shows the repair tree.

#### WP2.4 — Workflow mode and step DSL [B§3, B§10.1]
`hawser/workflow/`:
```python
@skill.workflow
def reconcile(ctx: WorkflowContext, invoice: Document, purchase_order: Document) -> ReconciliationResult:
    inv = ctx.step("extract_invoice", document.extract, invoice, schema=InvoiceRecord)
    po  = ctx.step("extract_po", document.extract, purchase_order, schema=PurchaseOrderRecord)
    matched = ctx.step("align", compute.align_line_items, inv.line_items, po.line_items)
    if matched.unmatched_count > 0:
        ctx.flag("unmatched_items")                # becomes a validation finding
    return ctx.step("assemble", compute.reconcile, inv, po, matched)
```
- `ctx.step(key, fn, ...)` is the idempotent checkpoint boundary (WP1.8). `key` must be unique per run; loops append an index.
- Static analysis: the workflow is executed once in **dry-run mode** at skill load with stub inputs to enumerate step keys, tools used and branches; mismatches with the manifest's declared `tools` → `PlanInvalid` at install time, not at run time.
- Branching and bounded loops are plain Python with `ctx.loop(key, max_iterations)` guards; parallel fan-out via `ctx.parallel([...])` (thread pool locally; Temporal child activities in Phase 5).
- Deterministic calculation steps are tagged `pure=True` and never call models.

#### WP2.5 — Skills [B§12]
Bundle layout:
```
skills/invoice-po-reconciliation/
  skill.yaml          manifest (schema in api/schemas/skill_manifest.schema.json)
  workflow.py         optional; mode: workflow
  prompts/            system.md, extract_invoice.md ...
  schemas/            output and intermediate schemas (JSON Schema or .py)
  validators.py       skill-local validators
  examples/           input files + expected outputs (also used by evals)
  evals/dataset.yaml  references to datasets
  CHANGELOG.md
```
- `skill.yaml` fields per blueprint: name, version, description, inputs (typed), output_schema, mode, models (allowed/preferred), tools (required permissions), validators, evidence requirements, policy defaults, evals, compatibility (`hawser>=0.2,<0.3`), examples.
- Loader: resolves bundle → `Skill` object; computes a bundle hash; writes `hawser.lock` (skill name, version, hash, resolved tool and model versions) so runs are reproducible.
- Permissions diff: `hawser skill install|upgrade` prints the delta in tools/side-effects/models vs. the locked version and requires `--accept-permissions` when it widens **[B§12]**.
- Commands: `hawser skill new|validate|install|list|test|export` (`export` → tarball with hash).

#### WP2.6 — Review and revisions [B§17]
- `Revision`: `revision_id`, `run_id`, `kind: machine|reviewed`, `result`, `field_meta`, `parent_revision_id`, `author` (`system` or reviewer id), `created_at`, `comment`. The machine revision is immutable; a review creates a new revision.
- Review record: `review_id`, `run_id`, `status: open|approved|rejected`, `assigned_to`, `decisions[]` (field pointer, old, new, comment), `etag` for concurrent-edit protection (optimistic concurrency; mismatch → 409).
- Local review path for Phase 2 (UI comes in Phase 6): `hawser review list`, `hawser review show <run>` (prints fields with issues and evidence text), `hawser review edit <run> --set /total=123.45 --comment "..."`, `hawser review approve|reject <run>`. These operate on the same `MetadataStore` the server will use.
- Export: `hawser runs export <run> --revision machine|reviewed --format json`.

#### WP2.7 — Evaluation system [B§21]
`hawser_evals/`:
- Dataset format: `dataset.yaml` (`id`, `version`, `description`, `cases[]`), `cases/<case_id>/inputs/*`, `expected.json` (conforms to skill output schema; may include `expected_evidence` with page+bbox per field and `expected_status`), `tags[]`.
- Scorers, chosen per field type from the schema with overrides in `dataset.yaml`: `exact`, `normalized_string` (case/whitespace/punct), `numeric(tolerance abs|rel)`, `date` (parse both), `enum`, `set` (order-insensitive), `list_align` (Hungarian matching on a key or on fuzzy similarity; reports precision/recall per item field), `evidence_iou` (bbox IoU ≥ threshold on the same page), `evidence_presence`, `status_match`.
- Metrics per eval run: field accuracy (micro/macro), document pass rate (all required fields correct), abstention rate (correct `unknown` vs. wrong `unknown`), review rate, validation issue rate by rule, cost per case (p50/p95), latency (p50/p95), tokens, repairs per case, model/provider breakdown.
- Failure clustering v1: group failures by `(field, scorer, rule_id)`; print top clusters with example case ids.
- CLI: `hawser eval run --skill X --dataset Y [--mode direct|workflow|agent] [--model ...] --budget 5.00` → `eval_<id>` with `report.json` + `report.md`; `hawser compare eval_a eval_b` → diff table with regressions highlighted; `hawser eval gate --thresholds evals.yaml` → non-zero exit on regression (used by CI).
- Config snapshot: every eval run stores skill hash, model registry hash, policy, cassette mode, git SHA **[B§21]**.
- CI: reference workflow eval runs in replay mode on every PR; nightly live run with budget cap posts a summary artifact.

#### WP2.8 — Reference workflow: invoice ↔ PO reconciliation [B§29]
- Schemas: `InvoiceRecord`, `PurchaseOrderRecord` (header fields + `line_items[]` with `sku`, `description`, `qty`, `unit_price`, `line_total`, currency), `ReconciliationResult` (matched items with both sides + deltas, unmatched invoice items, unmatched PO items, header discrepancies, totals check, `summary_status: match|discrepancies|unreadable`).
- Steps: `classify` (which input is the invoice and which the PO, by a small model call with page-1 text; or by skill input names when given), `extract_invoice`, `extract_po`, `align` (deterministic: SKU exact → normalised description fuzzy ≥ 0.9 → qty+unit_price proximity; emit `match_method` per pair), `arithmetic` (recompute line totals, subtotal, tax, total; tolerance config), `reconcile` (discrepancy list with evidence on both sides), `assemble`.
- Validators: required fields, totals match, evidence present for header amounts and every matched line, cross-document currency equality, `unmatched_items` → `warning` or `error` per policy.
- Dataset: `datasets/invoice-po/v1` — ≥ 60 synthetic pairs generated by `examples/_gen/invoice_po.py` with controlled variation (layouts × 4, currencies × 3, injected discrepancies: qty, price, missing item, extra item, rounding; 20% include a scanned-style rasterised page), plus ≥ 10 hand-labelled public-domain samples if available. Expected outputs are produced by the generator, so they are exact.
- Published metrics target for Phase 2 exit (native-text pairs only): field accuracy ≥ 0.95, document pass ≥ 0.85, review rate ≤ 0.2, cost per pair reported.

### Tests
- Validators: unit tests per rule; property tests on arithmetic tolerance; pointer precision.
- Acceptance decision table test (every combination of issue severity × repairability × attempts × policy).
- Repair: no-progress detection; attempt caps; cost caps with a fake gateway that charges.
- Workflow: step idempotency (run twice, second run executes zero steps), dry-run enumeration, loop bound.
- Skills: manifest schema; permission widening requires flag; lockfile reproducibility.
- Review: optimistic concurrency conflict; machine revision immutability.
- Evals: scorer tests; `compare` detects a planted regression.
- End-to-end: reference workflow on the dataset in replay mode in CI, gate thresholds enforced.

### Exit criteria (demo)
`hawser eval run --skill invoice-po-reconciliation --dataset invoice-po/v1 --mode workflow` prints the metrics table; one case with an injected discrepancy ends `needs_review`; `hawser review edit/approve` creates a reviewed revision; `hawser compare` against a run with repairs disabled shows the delta. All in replay mode in CI.

### Estimate: 6 weeks.

### Risks
- Synthetic data over-fits layouts. Mitigation: the generator varies templates; keep a "held-out layouts" split; add real samples as they are obtained.
- Envelope prompting costs tokens. Measure; Phase 4 context manager reduces it.

### Status (2026-10-18): implemented
All eight work packages landed; the exit demo runs (`hawser eval run … --mode workflow`, a discrepancy case ends `needs_review`, `hawser review edit/approve` creates a reviewed revision, `hawser compare` diffs two reports). Published results: `docs/benchmarks/invoice-po.md` — Haiku 5.5 over 60 pairs: 100% field accuracy, 100% status routing, 97% evidence coverage on native PDFs, $0.0023/pair; Opus 5.5 smoke identical quality at $0.08/pair. Notes on what differs from the text above:
- **Shared extraction core.** `hawser.runtime.extract.extract_structured` is used by direct mode, by `WorkflowContext.extract` and by repair, each call being two checkpointed steps (`<prefix>:model`, `<prefix>:anchor`).
- **Repair (WP2.3)** implements `ask_field` (sub-schema + merge for top-level fields) and full re-extraction with corrections for the other re-extractable kinds; `reocr` waits for Phase 7; no-progress fingerprinting and cost/time bounds work as specified. Workflow-level repair (re-running named steps) is deferred to the agent/plan work in Phase 4; workflow failures route to review.
- **Validators** are configured in the manifest as `id` + `params`: `rules.expressions` (a whitelisted-AST expression language — `approx(sum(line_items[:].line_total), subtotal)`), `rules.formats`, `rules.evidence_required`, `core.unknown_budget`, plus skill-local objects from `validators.py`. The separate `cross_document` validator was unnecessary: workflow results embed both documents, so cross-field expressions cover it.
- **Workflow DSL** is `WorkflowContext` (`extract`, `step`, `attach`, `derive`, `flag`, `parallel`, `loop`). Dry-run static enumeration of step keys at install time is not implemented; permissions are enforced at call time against the manifest's `tools` and the request's narrowing `tools`.
- **Tool system** shipped as a registry + `@tool` decorator with `ToolSpec`s (`document.extract`, `compute.align_line_items`, `compute.check_arithmetic`, `compute.reconcile`); the permission-enforcing broker with limits/approvals remains Phase 3.
- **Review**: `ReviewService` (open/edit/approve/reject, optimistic `version`), `Review` added to the contracts, machine revision written for every run, `LocalRuntime.get` overlays the latest reviewed revision.
- **Evals**: `hawser_evals` (dataset format, aligned field scoring, metrics, failure clusters, `report.json/md`, `compare`, `gate`) and `hawser eval run|gate`, `hawser compare`. Datasets are generated, not committed; recordings under `datasets/**/cassettes/` are committed and survive rebuilds (a rebuild once deleted $0.80 of recordings before that guard existed).
- **Dataset expectations**: scanned pairs expect `needs_review` until OCR exists, and `expected_status` derives from the computed reconciliation summary (a 1-cent rounding difference is within tolerance and correctly a match).
- CI: `.github/workflows/eval.yml` rebuilds the dataset and replays the native smoke subset through the Haiku recording, gated by `evals/thresholds.yaml`.

---

## Phase 3 — Model gateway and tool system (4 weeks)

### Goal
The same skill runs against at least two providers **[B§30]**; model selection is capability- and policy-aware; fallback never violates policy; every privileged operation goes through a tool broker that enforces permissions, limits and side-effect classification.

### Scope
**In:** OpenAI-compatible adapter (OpenAI, vLLM, Ollama, OpenRouter), Google Gemini adapter, model registry completion, selection policy, pinning, fallback, rate limits, cost accounting reconciliation, adapter compatibility test kit, ToolSpec/registry/broker, permission model, initial tool families.
**Out:** agent planning (Phase 4), image/audio/video tools beyond what documents need.

### Work packages

#### WP3.1 — Adapters
- `OpenAICompatibleAdapter` with base URL/profile (`openai`, `vllm`, `ollama`, `openrouter`), structured output via `response_format=json_schema` when supported else forced tool call; image parts; PDF via pre-rendered pages.
- `GeminiAdapter` (native PDF/image/audio/video parts become useful in Phases 8–9).
- Local OCR is not a model adapter; it is a tool (Phase 7).
- Capability probing on startup is **not** done (non-deterministic); the registry is the truth and a `hawser models check <id>` command verifies it with one cheap live call.

#### WP3.2 — Selection, pinning, fallback [B§9]
- `ModelPreference` in the request: `pinned: str | None`, `candidates: list[str] | None`, `strategy: cheapest|fastest|best_quality|balanced`.
- Selector: filter registry by required capabilities (modalities present in the inputs, structured output, tool calling, context size ≥ estimated need) → filter by policy (allowed providers/models/regions, `privacy.no_hosted`, data-handling flags) → rank by strategy using registry pricing and a per-model quality prior (from skill eval history when available, else registry default) → pick.
- Fallback: on retryable exhaustion or capability error, move to the next candidate **only if it also passes the policy filter**; emit `model.fallback` event with reason **[B§9]**. Fallback across privacy classes is impossible by construction because the policy filter runs first.
- Config snapshot: `RunResult.usage.models_used[]` includes exact model ids and registry version.

#### WP3.3 — Cost accounting [B§30]
- Every `ModelCallUsage` carries `cost_usd` computed from registry pricing at call time, plus `pricing_version`. Tool calls carry `cost_usd` from `ToolSpec.cost_estimate` or actual (cloud OCR).
- Reconciliation check in tests: sum of step usage == run usage == sum of events; a mismatch fails the run with `usage_mismatch` (bug detector, not a user error).
- Budget enforcement moves here from the crude Phase 1 version: pre-call estimate (tokens via provider tokenizer when available, else chars/4 heuristic × price) must fit the remaining budget; exceed → `BudgetExceeded`.

#### WP3.4 — Adapter compatibility kit [B§9]
`hawser/gateway/testing/`: a pytest plugin that third parties run against their adapter: request shapes, structured output round-trip, image part, error mapping (429 → retryable `ProviderError`), usage fields present, streaming contract (if declared). Documented in `docs/extending/model-adapters.md`.

#### WP3.5 — Tool system [B§11]
```python
class ToolSpec(BaseModel):
    name: str; version: str; description: str
    input_schema: JsonSchema; output_schema: JsonSchema
    modalities: list[str]
    permissions: list[str]                 # e.g. "document.read", "network.egress:api.example.com"
    side_effect: Literal["none","local_artifact","external_read","external_write"]
    limits: ToolLimits                     # cpu_s, memory_mb, wall_s, max_input_bytes
    cost_estimate: CostEstimate | None
    retry: Literal["never","safe","idempotent_with_key"]
    idempotency: Literal["pure","keyed","none"]
    location: Literal["inprocess","worker","remote","sandbox"]

class Tool(Protocol):
    spec: ToolSpec
    def invoke(self, ctx: ToolContext, input: dict) -> ToolResult   # typed output + artifacts + evidence + usage
```
- Registry: in-code registration + `tools.yaml` for remote tools; versions resolvable (`document.extract@1.2`).
- **Broker** (`hawser/tools/broker.py`) — the only call path: validate input against schema → check the run's effective permission set (skill-declared ∩ policy-allowed ∩ request-narrowed) → check side-effect approval requirement (`external_write` requires an approval record unless policy pre-approves a specific action pattern) → reserve budget → enforce limits (subprocess/time guard for in-process tools) → invoke → validate output → settle usage → emit events. Tools receive a `ToolContext` with scoped artifact access only (no raw credentials; provider credentials never reach tools) **[B§11]**.
- Prompt-injection posture **[B§24]**: tool outputs are typed data; the broker strips any field not in the output schema; nothing in a tool output can alter permissions or policy because the policy object is immutable after admission.
- Initial families: `document.inspect|extract_text|render_page|extract` (schema-guided extraction becomes a tool that wraps the gateway), `compute.arithmetic|align_line_items|reconcile|expression`, `review.request` (creates a review record and pauses), `convert.office_to_pdf` (LibreOffice in a subprocess with a timeout; `location=worker`), `search.text_index` (local BM25 over document indexes).

### Tests
- Selector decision tables (capabilities × policy × strategy); fallback never selects a filtered model (property test over random registries and policies).
- Cost reconciliation invariant across the whole reference eval.
- Broker: permission denial, approval requirement for `external_write`, limit enforcement (a tool that sleeps), output-schema stripping.
- Compatibility kit runs against all first-party adapters in replay.

### Exit criteria (demo)
`hawser eval run ... --model anthropic/claude-* ` and `--model openai/gpt-*` (and `ollama/<local>`) produce comparable reports from the same skill; a run with `policy.privacy.no_hosted=true` and only hosted candidates fails fast with `PolicyViolation`; a tool call requiring `external_write` pauses for approval.

### Estimate: 4 weeks.

### Status (2026-10-18): implemented
Exit demo done: the reference skill was evaluated unchanged on four models across three vendors (Claude Haiku/Opus 5.5, Gemini 2.5 Flash, GPT-4.1 mini — `docs/benchmarks/invoice-po.md`), `no_hosted` with only hosted candidates fails fast with `no_model_available`, and an `external_write` tool pauses the run on `awaiting_approval`; `hawser approve` resumes it from checkpoints without repeating model calls. Notes on what differs from the text above:
- **Adapters (WP3.1):** the OpenAI-compatible adapter (profiles: OpenRouter, OpenAI, Ollama, vLLM) is the second adapter; a native Gemini adapter was not written — Google models are reachable through OpenRouter today and no Google key exists to test a native one. `hawser models check <id>` runs the live compatibility kit (text, structured output, image part); all three routed providers pass 3/3.
- **Selection (WP3.2):** `hawser.gateway.select` — requirements (modalities, structured output, min context) computed from the indexed inputs, policy filter, capability filter, strategies `cheapest|fastest|best_quality|balanced` (a `speed_prior` was added to `ModelSpec`), pinned/explicit lists, and `Gateway.complete_any` for fallback restricted to the filtered list. Property-tested: no candidate ever violates policy.
- **Budgets (WP3.3):** pre-call estimates gate `max_cost_usd`, `max_model_calls`, `max_input_tokens`; `max_pages`/`max_pixels` at admission; `reconcile()` fails a run with `usage_mismatch` if step and run usage disagree. Provider-reported cost (OpenRouter) overrides registry pricing.
- **Compatibility kit (WP3.4):** `hawser.gateway.testing.compatibility_checks` — used by `models check` and by tests against the OpenAI-compatible adapter with a mock transport (429/5xx/400/upstream-200/content-filter/empty mapping).
- **Broker (WP3.5):** `hawser.tools.Broker` — permission = skill ∩ request ∩ policy (globs), input/output JSON-schema validation with stripping, approvals for `external_write` with pre-approval patterns, wall-time limits via a worker thread, `ToolCallUsage` and `tool.called` events; tools receive `ctx` only when declared. `WorkflowContext.step` routes registered tools through it; `ctx.tool(name, ...)` calls by name. The `awaiting_approval` pause is real: `LocalRuntime.resume()` re-executes with `resuming=True` so already-passed state transitions are skipped and completed steps replay. CPU/memory limits are not enforced (documented); that needs the sandbox adapters of Phase 10.
- **Tool families:** `document.inspect|extract_text|render_page|extract`, `compute.*`, `search.text_index` (BM25), `convert.office_to_pdf` (LibreOffice, wired into admission so `.docx/.xlsx/.pptx` are accepted when `soffice` exists), `http.post` (external_write, `Idempotency-Key` = action id), `review.request`.
- **Contracts:** `Approval` added (`api/schemas/Approval.schema.json`); `ModelResponse.fallback_from`.

---

## Phase 4 — Agent mode (5 weeks)

### Goal
Agent mode compiles a goal into a **typed, validated plan** and executes it within policy, with budget reservation, bounded loops and repair paths, and the eval shows direct vs. workflow vs. agent on the same dataset **[B§3, B§10]**.

### Scope
**In:** Plan IR, plan validator, planner (model-generated plan → IR), executor, budget reservation/settlement, context manager v2, re-planning on failure (bounded), three-mode comparison.
**Out:** generated-code sandbox (Phase 10), durable execution (Phase 5).

### Work packages

#### WP4.1 — Plan IR [B§10.1]
JSON document validated by `api/schemas/plan.schema.json`:
```
Plan { id, goal, inputs[], budget_estimate, nodes[], edges[], exits{success, failure} }
Node = CallTool{tool, version, input_bindings} | CallModel{purpose, schema_ref, context_spec}
     | Compute{expression|tool} | Branch{condition, then, else} | Loop{body, max_iterations, until}
     | Parallel{branches[], max_parallel, aggregate} | Validate{validators[]} | Review{reason}
     | Repair{strategies[], max_attempts} | Artifact{producer} | Exit{status}
Bindings reference prior node outputs by `$node.<id>.<path>` and inputs by `$input.<name>`.
```
- Plan validator: all tools exist at the given version and are permitted; all bindings resolve to declared output schemas (type-checked against JSON Schema); loops have bounds; parallel width ≤ policy; estimated cost (sum of tool/model estimates × loop bounds) ≤ budget; no `external_write` without a preceding `Review`/approval node unless pre-approved; exactly one success exit **[B§10.1]**.

#### WP4.2 — Planner
- Prompt the model with: goal, input manifests (not content), the catalogue of permitted tools (name, description, input/output schemas), policy limits, and a few canonical plan examples. Ask for a plan in the IR (structured output).
- Validate; on failure, feed the validator's errors back once (bounded re-plan, `policy.max_replans` default 2). Still invalid → `PlanInvalid`.
- **Deterministic shortcut rule** **[B§3]**: if the skill is `mode: workflow`, agent mode is refused unless the request explicitly overrides; if a goal matches a registered skill's description with high confidence (embedding or keyword match, logged), the planner is told to prefer calling the skill as a single tool.
- Plan caching: plans are cached by `(goal hash, tool catalogue hash, policy hash, input modality set)`; a cache hit skips the planner call (and is marked as such).

#### WP4.3 — Executor
- Topological execution with the existing `StepExecutor` (each node is a step; `step_key = plan_id/node_id[/iteration]`).
- Budget reservation **[B§10.3]**: before dispatching a `Parallel`, reserve `sum(branch estimates)` from the remaining budget; each branch settles actual usage on completion; unused reservation is released. Reservation failure → the node is skipped with `budget_exhausted` and the plan takes the failure exit, which usually leads to `needs_review`.
- Loop guards: `max_iterations`, and the same no-progress fingerprint as repair.
- Cancellation checks between nodes; a cancelled run records partial results.

#### WP4.4 — Context manager v2 [B§13]
- Retrieval: BM25 (and optional embedding) index over document text per page/region, built once per asset; `CallModel.context_spec` requests "pages matching X", "region around label Y", "previous step summaries", etc.
- Budgeting: per-call token budget from policy; selection order: tool schemas → instruction → validation failures → requested evidence windows → summaries; drop lowest priority first; emit `context.selected` with what was included and why.
- Step summaries: after each node, a compact (≤ 200 tokens) summary is produced *deterministically* where possible (schema-shaped: step, status, key values), falling back to a model summary only for free-text outputs.

#### WP4.5 — Three-mode comparison [B§3, B§21]
- `hawser eval run --mode direct|workflow|agent` on the reference dataset; `hawser compare` shows accuracy, review rate, cost and latency per mode. Document the result in `docs/benchmarks/invoice-po.md`.

### Tests
- Plan validator fixtures (one per rule) and a fuzz test that random mutations of a valid plan are rejected for the right reason.
- Reservation/settlement arithmetic under concurrent branches (property test).
- Planner in replay mode produces a valid plan for 5 canned goals; invalid-plan feedback path exercised.
- Executor: cancellation mid-parallel, loop bound hit, failure exit routes to review.

### Exit criteria (demo)
A free-form goal ("Find every discrepancy between these two documents and summarise the risk") on a new document pair produces a plan visible in `hawser inspect --plan`, executes under a $0.25 budget, ends `accepted` or `needs_review` with evidence, and the three-mode comparison report is published.

### Estimate: 5 weeks.

### Status (2026-10-18): implemented
Exit demo done: `hawser run --mode agent` plans and executes; `hawser inspect --plan` shows the nodes; the three-mode comparison is in `docs/benchmarks/invoice-po.md` (direct 99.3% fields but 4/7 status; workflow and agent 100%/7/7; agent $0.0032/pair). Notes on what differs from the text above:
- **Plan IR (WP4.1)** is a *flat* `Node` model (one class, optional fields per kind) plus a `Plan`; containers reference node ids. Exported as `api/schemas/Plan.schema.json`. Plans are produced as prompt-guided JSON and validated by `hawser.plan.validate` — not provider-enforced, by design (the union-heavy schema would hit the grammar limits, and the runtime must validate anyway). Static checks: ids, bindings, inputs, tools and permissions, **tool input keys against the (now signature-inferred) input schemas**, external writes without a preceding `review`, loop/parallel/step limits, expression syntax, schema inlining, estimated cost, exactly one success exit with a result.
- **Planner (WP4.2)**: catalogue of permitted tools + installed skills (`skill.<name>`), limits, example plan; up to `policy.planning.max_replans` feedback rounds; plan cache keyed by goal, catalogue, policy and input shape (`~/.hawser/plans`). The deterministic shortcut is a keyword match that tells the planner to prefer a matching skill; `--mode agent` on a workflow skill lets the planner choose the steps under the skill's contract.
- **Executor (WP4.3)**: every node a checkpointed step (`plan/<id>[/iter]`); `parallel` reserves estimated cost against `max_cost_usd` (`budget.reserved/settled/released`); loops stop on repeated outputs; `review`/failure exits route to review; `repair` nodes run the Phase 2 loop on a `call_model` target; skills run inline via `execute_skill` with prefixed step keys. Cancellation mid-plan is still not applicable to the synchronous local runtime.
- **Context manager v2 (WP4.4)**: `pages: all | matching | [n…]` with BM25 for `matching`, per-call token budget, deterministic JSON summaries of prior outputs (no model-written summaries yet).
- **Evidence through bindings**: the success exit's result template decides where each extraction's `field_meta`/evidence lands (`{"invoice": "$node.a"}` → `/invoice/…`).
- A live planner attempt invented a tool argument name; that exposed that tools without explicit input schemas advertised `input: object`. `@tool` now infers input schemas from signatures and the validator rejects unknown/missing keys, so the planner gets precise feedback.

---

## Phase 5 — Control plane and durable execution (Python; revised by ADR-0010)

> **Revision (2026-10-18).** The original Phase 5 specified a Go control plane with Temporal.
> ADR-0010 replaces it with a Python control plane (`hawser-server`, `hawserd`) and a
> Postgres-leased durable worker. The goal, the public contract (OpenAPI, SSE, webhooks,
> idempotency, API keys, tenancy columns) and the exit demo are unchanged; the implementation
> stack is not. The original text is preserved in git history.

### Goal
The same SDK and CLI work against a server. Runs are durable (crash recovery, timeouts,
cancellation, idempotency, review/approval pauses), accessible via REST/OpenAPI, SSE and signed
webhooks, with Postgres metadata and S3-compatible artifacts, deployable with Docker Compose
**[B§10.2, B§22, B§25, B§26]**.

### Scope
**In:** `hawser-server` package (`hawserd serve | worker | migrate | admin | openapi`), Postgres
metadata store + forward-only migrations, S3 artifact store + signed URLs (MinIO in Compose),
leased durable worker with heartbeats and crash recovery, API keys + tenancy columns
(single-tenant semantics), SSE with resume, signed webhooks with retries and a dead-letter list,
Compose, SDK HTTP backend (`hawser.Client(base_url=..., api_key=...)`), `hawser --server`,
conformance suite run against both backends, operator docs.
**Out:** RBAC/orgs/billing (Phase 12), web UI (Phase 6), Go, Temporal.

### Work packages

#### WP5.1 — Engine extraction and OpenAPI contract [B§22]
- Factor `LocalRuntime` into an `Engine` (submit/execute/resume/get/list over any
  `MetadataStore` + `ArtifactStore` + `Gateway` + `SkillRegistry`) so the worker and the local
  runtime share one execution path. `Runtime.wait(run_id, timeout)` joins the protocol.
- `api/openapi/openapi.json` is **generated** by `hawserd openapi` from the FastAPI app, whose
  bodies are the contract models; CI checks drift. Resources: `/v1/assets`, `/v1/runs`,
  `/v1/runs/{id}` (summary), `/v1/runs/{id}/result`, `/v1/runs/{id}/events` (JSON or SSE by
  `Accept`), `/v1/runs/{id}/steps`, `/v1/runs/{id}/revisions`, `/v1/runs/{id}/cancel`,
  `/v1/reviews/...`, `/v1/approvals/...`, `/v1/skills`, `/v1/models`, `/v1/tools`,
  `/v1/artifacts/{id}` (+`/content`, `/url`), `/v1/webhooks` (+ deliveries), `/v1/usage`,
  `/healthz`, `/readyz`.
- Conventions: `Idempotency-Key` on `POST /v1/runs`; `If-Match` (review version) on review
  edits/decisions; problem+json errors carrying the error-taxonomy `code`;
  `X-Hawser-Request-Id` on every response.

#### WP5.2 — Server, auth, tenancy
- FastAPI app; dependency-injected `Services` (stores, queue, gateway config, skills).
- API keys `hk_live_<id>_<secret>`: argon2id hash at rest, prefix index, `revoked_at`,
  `last_used_at`; `Authorization: Bearer`. `hawserd admin create-tenant`, `create-key`,
  `revoke-key`. `HAWSER_DEV_API_KEY` bootstraps a tenant + key for Compose/dev only.
- Every table carries `tenant_id`; every read of a tenant-owned object checks ownership; a test
  asserts cross-tenant reads return 404; a lint test asserts every list query in the Postgres
  store filters on `tenant_id`.

#### WP5.3 — Postgres schema and migrations
- `hawser_server/migrations/NNNN_*.sql`, applied by `hawserd migrate` (forward-only, recorded in
  `schema_migrations`). Tables mirror the local store (assets, artifacts, runs, steps, events,
  results, revisions, reviews, approvals) plus `tenants`, `api_keys`, `jobs`, `webhooks`,
  `webhook_deliveries`. `events` has `(run_id, seq)` as primary key; per-run sequence assigned
  under an advisory lock.
- The same contract tests run against `SQLiteMetadataStore` and `PostgresMetadataStore`
  (embedded Postgres in tests via `pgserver`; a service container in CI).

#### WP5.4 — Artifact service [B§19]
- `S3ArtifactStore` (boto3; MinIO in Compose) with the local key scheme; presigned GET URLs
  (TTL ≤ 15 min default) or proxied streaming (`GET /v1/artifacts/{id}/content`).
- Uploads: `POST /v1/assets` (multipart, size-limited by config) admits immediately and returns
  the `Asset`; runs reference assets by id. Presigned multipart upload for very large inputs is
  deferred to Phase 7/8 when media sizes demand it.

#### WP5.5 — Durable execution (leased worker) [B§10.2]
- `jobs(run_id PK, tenant_id, state pending|leased|done, attempts, max_attempts, available_at,
  lease_owner, lease_expires_at, resume bool)`. `hawserd worker --concurrency N` leases with
  `FOR UPDATE SKIP LOCKED`, heartbeats every `lease/3`, executes via the `Engine` with
  `resuming=True` on any attempt > 1, marks done.
- Crash recovery: an expired lease is re-leased by any worker; finished steps replay from
  checkpoints. After `max_attempts` the run fails with `worker_attempts_exhausted`.
- Signals: `runs.cancel_requested_at` (API) is checked by the engine before every step →
  `Cancelled`. Approval decisions re-enqueue the run (`resume=true`). Review decisions need no
  worker. `policy.limits.max_wall_time_s` is checked at step boundaries.
- Idempotency: `POST /v1/runs` with `Idempotency-Key` returns the original run for the tenant.
  External side effects keep the Phase 3 `action_id` reconciliation.
- Kill test: a worker is killed mid-step; the run completes on another worker with no repeated
  model call.

#### WP5.6 — Events, SSE, webhooks [B§22]
- SSE: `id: <seq>`, `event: <type>`, `data: <Event JSON>`, keep-alive comments, resume from
  `Last-Event-ID`; the stream closes when the run reaches a terminal, resting or paused state.
- Webhooks: per-tenant endpoints with a secret shown once; events `run.completed`,
  `run.needs_review`, `run.awaiting_approval`, `review.decided`; `Hawser-Signature:
  t=<ts>,v1=<hmac-sha256(secret, "<ts>.<body>")>`; retries with exponential backoff (capped,
  up to 24 h); `delivery_id` for consumer dedup; `GET /v1/webhooks/{id}/deliveries` lists the
  dead letters.

#### WP5.7 — Deployment [B§26]
- `deploy/compose/docker-compose.yaml`: `postgres`, `minio`, `hawserd`, `hawser-worker`.
  `make up` brings it up and prints the dev API key; `hawser init --server http://localhost:8080`
  configures the CLI.
- `deploy/docker/Dockerfile`: multi-stage `uv` build, non-root, pinned base image.
- Operator docs: config reference (env vars), secrets, backup/restore, upgrade (`hawserd
  migrate`), health endpoints.

#### WP5.8 — SDK HTTP backend and conformance suite
- `RemoteRuntime` (httpx) implements `Runtime`; `hawser.Client(base_url=..., api_key=...)`;
  `hawser --server URL` or `[server]` in `hawser.toml`; `hawser run` streams SSE progress.
  `hawser review ...` and `hawser approve ...` work against the server through the same facades.
- `python/hawser/src/hawser/conformance/`: scenarios parameterised over a `Runtime` (recorded
  extraction → accepted; idempotent resubmit; budget exceeded → needs_review; review approve →
  reviewed revision; event ordering; cancel). CI runs it against `LocalRuntime` and against
  `hawserd` (SQLite dev mode and Postgres).

### Tests
- Store contract tests on SQLite and Postgres; tenancy lint; cross-tenant 404 test.
- Queue tests: lease, heartbeat, expiry, SKIP LOCKED under concurrency, poison runs.
- Worker kill test (subprocess killed mid-step; recovery with no repeated model call).
- Webhook signature verification, retry schedule, dead-letter listing.
- Conformance suite on both backends.

### Exit criteria (demo)
`make up`, `hawser init --server`, `hawser run` on the reference pair → live progress via SSE
in the terminal → `needs_review` → `hawser review approve` → webhook received by a sample
listener with a valid signature. Killing the worker during extraction does not lose the run.

### Risks
- Bespoke lease logic. Mitigation: small table, property-style concurrency tests, kill test in
  CI.
- Polling SSE under many concurrent streams. Mitigation: short-poll with backoff now;
  `LISTEN/NOTIFY` later without API change.

### Status (2026-10-18): implemented
All eight work packages shipped in Python (`python/hawser_server`, ADR-0010). Deviations and
notes:
- The `Engine` extraction (WP5.1) made `LocalRuntime` a thin wrapper; the worker and the local
  runtime share one execution path, including cancel/deadline checks at step boundaries.
- The metadata store became one SQL implementation with a backend per driver (SQLite via
  `sqlite3`, Postgres via `pg8000` — BSD, chosen over LGPL `psycopg` for the licence gate);
  schema is a migration list applied by both.
- Uploads go through the API (`POST /v1/assets`, multipart, size-limited); presigned multipart
  upload for very large media is deferred to the media phases. `s3://` inputs work when S3 is
  configured.
- The worker kill test is an in-process simulation (a blocked adapter, forced lease expiry, a
  second worker taking over, the first discovering it lost the lease and discarding its work)
  rather than `docker kill`; the semantics are the same and it runs in every CI job.
- Reviewed overlays downgrade the machine's error-severity issues to `info` (marked
  `resolved_by`) so an accepted-after-review result still satisfies the result contract.
- Compose has four services (Postgres, MinIO, `hawserd`, worker). The image was not built in
  this environment (no Docker available); the Dockerfile and compose file are untested.
- Tests: 48 server tests (store contract on both backends via embedded Postgres, queue
  concurrency, auth, API end to end, SSE, webhooks, worker takeover, SDK + CLI against a live
  uvicorn, S3 via moto, OpenAPI drift) plus the conformance suite on both backends.

---

## Phase 6 — Web application (6 weeks)

### Goal
A UI that exposes runtime behaviour: playground, run/trace inspector, evidence viewer with highlighted regions, review workspace, eval comparison, registries, usage and policy **[B§23]**.

### Scope
**In:** all screens in **[B§23]** except administration for managed deployments (Phase 12). Document and image evidence viewers; audio/video viewers come with Phases 8–9.
**Out:** multi-user auth beyond API key login (Phase 12).

### Work packages

#### WP6.1 — Foundation
- `web/`: Vite + React 18 + TypeScript strict, TanStack Router + Query, Zustand for local state, Tailwind + Radix primitives, `openapi-typescript` for API types, MSW for mocked API in dev and tests, Playwright for e2e, Storybook for the evidence components.
- Auth: API key entered once, stored in session; served by `hawserd` at `/app` (embedded build) and as a separate container in Compose.
- Design: load the `frontend-design` and `dataviz` skills at implementation time; the UI must show runtime structure (plan graph, steps, evidence), not a chat window **[B§23]**.

#### WP6.2 — Screens
1. **Playground**: inputs (drag-drop upload → asset admission results shown inline, including `needs_ocr` flags and limits), instruction, schema editor (Monaco with JSON Schema validation; "from example JSON" helper), skill picker, mode selector, policy form (budget, repair attempts, review on failure, model pinning), run button, live progress panel (SSE).
2. **Runs list**: filters by status/skill/mode/date; cost and latency columns; search by metadata.
3. **Run detail**: header (status, usage, versions pinned); tabs: *Result* (annotated JSON tree; click a field → evidence highlights), *Plan* (React Flow graph of workflow steps or agent plan with node status), *Steps/Trace* (timeline with events, model calls with tokens/cost/latency, context selections, repair tree), *Validation* (issues grouped by severity with links to fields), *Artifacts* (lineage graph), *Revisions*.
4. **Evidence viewer**: server-rendered page PNG (`document.render_page` artifact) with absolutely positioned bbox overlays scaled by the stored `Transform`; page navigation; hover shows evidence text, producer, confidence; image evidence uses the same component with pixel coordinates; multi-evidence per field.
5. **Review workspace**: side-by-side source (evidence viewer) and result form (generated from schema with issues inline); editable fields with change tracking; reviewer comments; approve/reject; `If-Match` etag conflict handling with a merge prompt; export original/reviewed **[B§17]**.
6. **Evaluations**: list of eval runs; detail with metric cards and per-case table; compare view (baseline vs. candidate; regressions highlighted; failure clusters); link each case to its run.
7. **Registry**: models (capabilities, pricing, regions, enabled flag), tools (spec, permissions, side-effects), skills (manifest, versions, permission diff between versions).
8. **Usage**: cost/tokens by day, skill, model, status; budget consumption per run.
9. **Policy**: project default policy editor with validation against the Policy schema.

#### WP6.3 — Shared components
- `EvidenceOverlay`, `AnnotatedJson`, `PlanGraph`, `EventTimeline`, `IssueList`, `UsageBadge`. Storybook stories from fixture data exported by the conformance suite so UI fixtures never drift from the contract.

### Tests
- Component tests with fixtures; Playwright e2e for: run from playground (mocked), open evidence, complete a review with a conflict.
- Accessibility pass (load `design:accessibility-review` skill) on review workspace and evidence viewer: keyboard navigation across evidence boxes is required.

### Exit criteria (demo)
Upload invoice + PO in the playground, watch steps complete, click `total` and see it highlighted on page 1, open the review workspace on a `needs_review` run, correct a value, approve, and see the reviewed revision in the API.

### Estimate: 6 weeks.

---

## Phase 7 — Image intelligence (4 weeks)

### Goal
Image and scanned-document capabilities in **[B§6]** as tools with coordinate-transform chains so evidence always maps back to the original **[B§6, B§15]**, plus OCR and layout analysis that lift documents from best-effort to supported for scanned inputs **[B§5]**.

### Work packages

#### WP7.1 — Coordinate and transform system
- `Transform` chain algebra: `crop(x,y,w,h)`, `scale(sx,sy)`, `rotate(deg, about)`, `exif_orient(n)`, `pad`, `dpi(points→pixels)`; `compose`, `invert`, `apply_to_bbox/point/mask`. Every image-producing tool records the transform from its output to its input artifact; evidence stores the chain and the runtime resolves it to original coordinates when assembling `RunResult`.
- Property tests: apply then invert returns the original box within 0.5 px for random chains.

#### WP7.2 — OCR and layout
- Tools: `ocr.tesseract` (local, default), `ocr.paddle` (optional extra), `ocr.hosted.*` (Google Document AI / Azure Document Intelligence / AWS Textract adapters, each `external_read`, region-tagged). Common output: words with boxes + confidence, lines, blocks, reading order.
- `document.layout`: block segmentation (text/table/figure/header/footer) via a local model (e.g. a LayoutParser/DocLayNet-trained detector, or the OCR engine's layout where sufficient); table extraction to cell grids with cell boxes; key-value pair detection heuristic (label proximity) with evidence.
- Document index v2 merges native text and OCR per page, marking `source: native|ocr` and confidence per word; evidence anchoring consumes both; `needs_ocr` pages now get real bbox evidence.
- Repair strategy `reocr` (WP2.3) implemented: re-OCR a region at higher DPI with a different engine if available.

#### WP7.3 — Vision tools
- `image.caption`, `image.vqa` (via gateway, any VLM).
- `image.detect` (adapters: VLM-native boxes where the model supports it — normalised coordinate parsing with validation; local detectors like OWLv2/Grounding DINO as optional extras), `image.ground` (phrase → boxes/points), `image.segment` (SAM-family adapter, masks stored as PNG artifacts + RLE), `image.points`.
- `image.classify|tag`, `image.embed` (SigLIP/CLIP local or hosted), `image.similarity`.
- `image.ops`: crop, rotate, deskew (Hough/projection profile), resize, normalise; each records transforms.
- `image.redact` (boxes → filled regions; also for document pages), `image.annotate` (draw evidence overlays into a new artifact for reports).
- `image.generate|edit` adapters behind an explicit policy flag (`generation.enabled`), `external_read` with cost; outputs are artifacts with producer metadata and no evidence claims.
- UI/screenshot parsing: `image.ui_parse` using a VLM with a fixed element schema (type, text, bbox); evidence is the element box.

### Tests
- Transform property tests; OCR golden tests on rasterised synthetic pages (word recall ≥ 0.98 at 300 dpi); detection adapter contract tests in replay; the reference dataset's scanned subset now meets the same thresholds as native pages (within 3 points).

### Exit criteria (demo)
Scanned invoice → OCR + layout → extraction with bbox evidence; a photo of a receipt rotated 12° → deskew → extraction, with evidence drawn correctly on the *original* photo in the web viewer.

### Estimate: 4 weeks.

---

## Phase 8 — Audio intelligence (3 weeks)

### Goal
Audio inputs with time-aligned evidence **[B§7]**.

### Work packages
- Admission: `ffprobe` (codec, container, duration, channels, sample rate); limits on duration; transcoding to 16 kHz mono WAV/FLAC as a derived artifact with a `time_offset` transform (always identity here, but the chain exists for clips).
- Tools: `audio.transcribe` (adapters: `faster-whisper` local; OpenAI, Deepgram, AssemblyAI hosted), output = segments with start/end ms, words with timestamps when available, language, confidence; `audio.diarize` (`pyannote` local, or provider-native) merged into segments; `audio.language_id`; `audio.classify` (optional); `audio.clip` (ffmpeg, records time offset).
- Long audio: chunk at silence boundaries (VAD) into ≤ 10-minute pieces with 5-second overlaps; each chunk is a checkpointed sub-step; stitch with overlap de-duplication; per-chunk cost accounting **[B§7]**.
- Transcript index: artifact (JSON) with segment ids; BM25 retrieval over segments for the context manager; `audio.qa` tool retrieves relevant segments and asks the model with instructions to cite segment ids → evidence `audio_span{start_ms,end_ms,segment_id,text}`.
- Evidence anchoring for transcripts: quote → segment match (same fuzzy approach as documents), producing time spans.
- Web: waveform player (`wavesurfer.js`) with evidence markers; click evidence → seek.
- Eval: a small synthetic TTS-generated dataset (scripted dialogues with known content) for transcript QA accuracy and timestamp tolerance (±1 s).

### Exit criteria (demo)
A 40-minute meeting recording → schema `{decisions[], action_items[{owner, due}], risks[]}` with each item linked to a playable timestamp; run cost and chunk count visible in the trace.

### Estimate: 3 weeks.

---

## Phase 9 — Video intelligence (4 weeks)

### Goal
Video inputs without placing whole videos in context: a compact temporal index, retrieval of relevant scenes/frames/transcript, timestamped evidence **[B§8]**.

### Work packages
- Admission: ffprobe (codec, resolution, fps, duration, audio tracks); limits on duration and decoded pixels (`fps × duration × w × h`); transcode to a mezzanine (H.264, capped resolution) if needed.
- Tools: `video.extract_audio` → Phase 8 pipeline; `video.scenes` (PySceneDetect content/adaptive detectors) → scene list with start/end; `video.sample_frames` strategies: `uniform(interval)`, `per_scene(n)`, `on_change(threshold)`, `at(timestamps)`; frames are image artifacts with `{t_ms, frame_index}` and a transform to the mezzanine resolution; `video.caption_scenes` (VLM per scene with k frames; optional provider-native video input for Gemini); `video.ocr_frames`; `video.track` (optional adapters, e.g. ByteTrack over detections); `video.clip`, `video.annotate`, `video.redact` (blur boxes over time ranges).
- Temporal index artifact: scenes × (caption, transcript segments overlapping, sampled frame ids, OCR text, detected objects). Retrieval over scene captions + transcript for the context manager; `video.qa` selects top-k scenes, then the frames for those scenes, under a frame budget from policy.
- Evidence: `video_span{t_start_ms,t_end_ms,scene_id,frame_ids[],track_id?}`; frame-level `image_region` evidence inherits the chain back to the frame and then to the timestamp.
- Repair: `retrieve` strategy re-samples denser frames inside the cited scene.
- Web: video player with scene strip and evidence markers; frame evidence shows the frame with overlays.
- Generation adapters (`video.generate|edit`) behind the same `generation.enabled` policy flag as images.
- Eval: small synthetic dataset (screen recordings / rendered slideshows with known content) for scene QA accuracy and timestamp tolerance (±2 s).

### Exit criteria (demo)
A 20-minute screen-recorded product demo → `{features_shown[{name, first_seen_at, screenshot_evidence}], issues_mentioned[]}` with each entry seekable; the trace shows that only ~2% of frames were sent to a model.

### Estimate: 4 weeks.

---

## Phase 10 — Generated-code sandbox (4 weeks)

### Goal
Optional code mode in which an agent writes a program using a read-only Hawser SDK, executed only in an isolation adapter that passes a security self-test **[B§18]**.

### Work packages
- **Sandbox broker** (`sandbox/broker/`): receives `{program, sdk_version, limits, egress_allowlist, artifact grants}`, picks the configured adapter, runs, collects stdout/stderr/exit/artifacts/action log, and exposes a tool-broker proxy inside the sandbox (the program calls `hawser_sdk.tools.call(...)` which is forwarded over a local socket to the broker, which applies all normal permission checks). Generated code therefore cannot bypass the tool broker, policy, artifact service or approvals **[B§18]**.
- **Adapters**: `docker+gvisor` (runsc) as the portable default; `firecracker` (microVM) as the hardened target via a small agent; `remote` adapter interface for hosted sandboxes (e.g. E2B/Modal-style) for operators who prefer them. Each adapter declares a capability report (isolation class, network control, resource limits).
- **Requirements enforcement** **[B§18]**: no host credentials (the sandbox gets a one-time broker token scoped to the run), no writable host mounts, network denied by default with explicit egress allowlists enforced at the adapter (network namespace + proxy), CPU/memory/pids/time/output-size limits, read-only SDK wheel, ephemeral filesystem, full capture, artifact/action audit.
- **Security self-test** (`sandbox/escape_tests/`): a suite the operator must pass before `code_mode.enabled=true` is honoured: outbound network to a denied host, read of `/proc/1/environ`, mount enumeration, fork bomb, memory bomb, output flood, attempts to reach the broker socket with a forged token, attempts to read another run's artifacts. Failing suite → `SandboxDenied` for every code-mode request, with the failing test named.
- **Code mode in the planner**: a `Code` node type in the Plan IR; permitted only when policy enables it and the sandbox passes; program length and runtime limits from policy; the SDK surface inside the sandbox is a strict subset (tools, artifacts read via grants, no gateway access except through tools).
- Docs: `docs/security/sandbox.md` threat model and operator checklist.

### Exit criteria (demo)
Agent mode is asked to "compute the per-category spend across these 12 invoices and produce a chart"; it writes a program using the SDK, which runs in gVisor, calls `document.extract` via the broker for each invoice (visible in the trace), produces a PNG artifact; the same request with `code_mode.enabled=false` falls back to a plan without a `Code` node; the escape suite passes in CI on Linux.

### Estimate: 4 weeks.

---

## Phase 11 — Integrations, distribution and 1.0 (3 weeks)

### Goal
Hawser is consumable from MCP clients and TypeScript, deployable on Kubernetes, documented, and tagged 1.0 with contract stability promises **[B§22, B§26]**.

### Work packages
- **MCP server** (`mcp/server/`, FastMCP): exposes admitted capabilities as MCP tools: `hawser_run_skill`, `hawser_get_run`, `hawser_list_skills`, per-skill tools generated from manifests (input schema from the skill), resources for artifacts/evidence (signed URLs), prompts for review summaries. Auth via API key; everything goes through the normal API, so policy applies unchanged.
- **MCP client**: `tools.mcp.*` family — an operator registers an MCP server; its tools are imported as `ToolSpec`s with `side_effect` declared by the operator (default `external_write` → requires approval), permissions mapped, outputs schema-validated and stripped by the broker **[B§22]**.
- **TypeScript SDK** (`sdks/typescript/`): generated models + thin client, SSE helper, webhook signature verifier; published to npm.
- **Python SDK** polish: async client, typed results generic over the output schema, published to PyPI with `hawser[anthropic,openai,ocr,audio,video]` extras.
- **Helm chart** (`deploy/helm/`): `hawserd`, workers (separate deployments and HPAs for models/media), migrations job, optional Temporal/Postgres/MinIO sub-charts or external references, secrets via existing `Secret`s, PodSecurity defaults, network policies.
- **Docs site** (`docs/` → MkDocs Material or Docusaurus): quickstart, concepts (runs, evidence, validation, skills, policy), guides per modality, extending (adapters, tools, validators, storage, sandbox), operator guide, security, API reference (from OpenAPI), benchmarks page with the reference metrics **[B§30]**.
- **1.0 release**: contract version 1.0.0; compatibility policy document (what is stable: REST, schemas, skill manifest, ToolSpec, Python SDK public surface); deprecation policy; changelog; signed release artifacts; SBOMs published.

### Exit criteria
Claude Desktop (or any MCP client) runs the reference skill via the MCP server; a TypeScript example submits a run and verifies a webhook; `helm install` on kind passes the conformance suite; docs build has no broken links; `v1.0.0` tagged.

### Estimate: 3 weeks.

---

## Phase 12 — Multitenancy and managed service (8+ weeks)

### Goal
Hawser Cloud: organisations, projects, environments, users/service accounts, RBAC, quotas, reservations/settlement, billing, team review queues, managed credentials, enterprise identity and audit exports, operational tooling **[B§27, B§28]**. The managed service runs the open runtime unchanged **[B§27]**.

### Work packages
- **Identity**: users, orgs, memberships; OIDC login (Google/GitHub/Microsoft) and SAML for enterprise; service accounts; API keys scoped to org/project/environment; session management; MFA.
- **RBAC**: roles `owner`, `admin`, `developer`, `reviewer`, `viewer`, plus custom roles over a permission catalogue (`runs:create`, `reviews:decide`, `skills:publish`, `policy:edit`, `billing:manage`, …); enforced in the Go API middleware; tests enumerate every endpoint × role.
- **Projects and environments**: `dev/staging/prod` with separate policies, keys, quotas and retention.
- **Quotas and rate limits**: per org/project: concurrent runs, runs/min, monthly spend cap; enforced via the `usage_ledger` reservations and a token-bucket limiter in the API.
- **Usage reservation and settlement**: reservations at run start (policy `max_cost`), settlement on completion, releases on cancel; month-end aggregation; invoice lines by skill/model/modality.
- **Billing**: Stripe (subscriptions + metered usage), webhooks for payment state, grace periods, hard stop on non-payment per policy; `billing:manage` role.
- **Managed provider credentials**: org-level secrets in KMS (envelope encryption); the gateway resolves credentials per run; bring-your-own remains default; managed keys are an opt-in with transparent per-token pricing.
- **Team review queues**: assignment rules (by skill, by issue rule), SLAs, reassign, bulk actions, reviewer metrics.
- **Retention administration**: per project retention classes; legal hold; deletion verification reports.
- **Dedicated/private deployments**: Helm values profiles; per-tenant namespaces; private model gateways; region pinning.
- **Operations**: dashboards (run success/latency/cost per tenant, queue depth, worker saturation), alerting, incident runbooks, status page, on-call rotation docs; capacity model for worker pools; autoscaling on queue depth.
- **Audit**: immutable audit events for every admin and review action; export to S3/SIEM; enterprise audit API.
- **Security programme**: third-party penetration test, dependency policy, vulnerability disclosure process, SOC 2 readiness checklist.

### Exit criteria
A new org signs up, invites a reviewer, sets a monthly cap, runs the reference skill with managed credentials, is billed metered usage, and a reviewer with only the `reviewer` role can approve but not change policy. Audit export contains every action.

### Estimate: 8 weeks for the first billable version; ongoing thereafter.

---

## 13. Cross-cutting workstreams (all phases)

### 13.1 Testing strategy
| Layer | What | Where it runs |
|---|---|---|
| Unit | Pure functions, validators, transforms, selectors | every push |
| Contract | Schema drift, OpenAPI ↔ models, Go ↔ Python schema hash | every push |
| Adapter compatibility | Model/tool/storage/sandbox adapters via kits | every push (replay), nightly (live) |
| Recorded integration | Reference workflow, agent plans, repairs | every push |
| Conformance | Runtime behaviour matrix on Local and Server backends | every push (local), PR to main (compose) |
| Chaos | Worker kill, DB restart, Temporal restart | nightly |
| Security | Sandbox escape suite, dependency scan, secret scan, SBOM | nightly + release |
| Eval regression | Reference dataset thresholds | every PR (replay); nightly (live, budget-capped) |
| Live smoke | One real run per provider | nightly, `HAWSER_LIVE_BUDGET_USD` |

Fixture policy: cassettes are committed; re-recording requires a PR label `re-record` and the diff of usage/cost is posted by CI.

### 13.2 Observability
OpenTelemetry from Phase 1 (local exporter to a file in dev); spans per run/step/model call/tool call with `run_id`, `step_key`, `tenant_id` attributes; metrics: runs by status, step latency, model tokens/cost, validator issue counts, repair counts, review time; logs structured and redacted by default (no document text, no raw provider payloads unless `trace.store_raw`).

### 13.3 Documentation
Each phase updates: quickstart (if affected), concept docs, API reference, operator docs, CHANGELOG. ADR for every reversible-with-pain decision. Benchmarks page updated whenever the reference eval thresholds change.

### 13.4 Versioning and release
- Contract version (schemas/OpenAPI) and package versions are separate; a package may bump without a contract change.
- Pre-1.0: minor versions may break with ADR + changelog note. Post-1.0: semver, 6-month deprecation window.
- Release checklist: conformance on both backends, eval gate, security scans, docs build, signed artifacts, images with digests.

### 13.5 Definition of done (per work package)
Code + tests + schema export + docs + an entry in the phase demo script + no `TODO` without an issue link.

---

## 14. Sequencing and critical path

```
P0 → P1 → P2 → P3 → P4 → P5 → P6 → P11 → P12
                 └────────────→ P7 → P8 → P9        (needs P3 tool system; independent of P5/P6)
                                      P10 (needs P4 planner + P5 server for broker token; can start after P4 with LocalRuntime)
```
Critical path to a self-hostable 1.0 with documents: P0–P6 + P11 ≈ 39 weeks for two engineers. Audio/video/sandbox extend it to ≈ 54 weeks sequentially, or ≈ 42 weeks with a third engineer taking P7–P10 in parallel from week 17.

---

## 15. Parallelisation with 3–4 engineers

| Engineer | Weeks 1–7 | 8–17 | 18–30 | 31–40 | 41–54 |
|---|---|---|---|---|---|
| A (runtime) | P0, P1 | P2 | P4 | P10 | P11 |
| B (platform/Go) | P0 tooling, CI | P3 gateway | P5 | P5 tail, P11 Helm | P12 |
| C (media/ML) | — | P2 dataset + evals | P7 | P8, P9 | P12 ops |
| D (web) | — | — | P6 (from week 22 once API stabilises) | P6, P8/P9 viewers | P12 admin UI |

Interfaces that must be frozen for parallel work: `RunResult`/`Evidence`/`Event` (end of P1), `ToolSpec`/broker (end of P3), OpenAPI v1 (week 24).

---

## 16. Solo cut (one engineer, first six months)

Build P0, P1, P2, P3 only, and ship **Hawser as a Python library** (`LocalRuntime`, CLI, skills, evals, reference workflow, two providers). Skip the server, UI, agent mode and non-document modalities. This is already differentiated (evidence + validation + repair + evals + model choice) and validates demand before the expensive platform work. Keep every protocol boundary so P5 remains a re-hosting.

Order of later additions for a solo builder: P4 (agent) only if users ask for free-form goals; P5+P6 only when someone wants to run it as a service; P7 (OCR/layout) early if scanned documents dominate real usage — it may deserve to move ahead of P3.

---

## 17. Risk register

| Risk | Impact | Mitigation | Owner phase |
|---|---|---|---|
| Scope creep from the feature-first blueprint | Nothing ships | Phase gates are demos; features not in a phase's scope list are rejected in review | all |
| Evidence anchoring fails on messy OCR | Core promise weakens | Fuzzy anchoring with confidence; `unknown` over guessing; Phase 7 OCR confidence feeds anchoring | P1, P7 |
| Provider structured-output drift | Flaky extraction | Envelope + normaliser + compatibility kit in nightly live tests | P1, P3 |
| Temporal non-determinism bugs | Stuck runs | Thin Go workflows; replay tests; all logic in activities | P5 |
| Cost accounting mismatch | Billing disputes | Usage computed from events; reconciliation invariant fails the run in tests | P3, P12 |
| Sandbox escape | Catastrophic | Self-test suite gates enablement; gVisor minimum; microVM target; third-party test before Cloud | P10, P12 |
| Synthetic eval data misleads | False confidence | Held-out layouts; add real samples continuously; publish methodology | P2+ |
| Dual-language contract drift | Subtle bugs | Generated types only; schema hash check in CI | P5+ |
| Licence contamination (AGPL/GPL deps) | Distribution blocked | Dependency licence check in CI (`pip-licenses`, `go-licenses`); ADR-0007 | P0+ |
| Name/trademark conflict | Rename later | Search before public release | P0 |

---

## 18. First two weeks, concretely

Day 1–2: WP0.1 repo, toolchain, CI skeleton, ADR template, ADR-0001/0002/0003.
Day 3–5: WP0.2 ids/hashes; WP0.3 models (`RunRequest`, `RunResult`, `Evidence`, `Policy`, `Usage`, `Event`, `Step`, `Asset`, `Artifact`, `ValidationIssue`); schema export + drift test.
Day 6–7: WP0.4 errors; WP0.5 state machine + edge test.
Day 8–10: remaining ADRs; `examples/_gen` skeleton for synthetic invoices (needed by Phase 1 day 1); quickstart doc outline; Phase 1 tickets written against this plan.

Phase 1 starts on day 11 with WP1.1 admission and WP1.3 document primitives in parallel.
