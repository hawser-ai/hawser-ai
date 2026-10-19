# The server: `hawserd`

`hawserd` is the Hawser control plane (ADR-0010): an HTTP API, a durable worker, migrations
and admin commands, in Python, over Postgres and S3-compatible storage. It runs the *same*
engine as the local runtime; what changes is durability and who can reach it.

```bash
make up                                    # Postgres + RustFS (S3) + hawserd + worker (docker compose)
hawser init --server http://localhost:8080
export HAWSER_API_KEY=hk_live_...          # printed by `make up`, or `hawserd admin create-key`
hawser run -i "Extract the invoice." -f invoice.pdf --schema invoice.schema.json
```

Without Docker: `hawserd serve --dev` runs on SQLite and local files with an in-process worker
and a development API key (printed at start-up, kept in `.hawserd/dev.key`). That mode is for
evaluation on one machine; production is Postgres + one or more `hawserd worker` processes.

## Shape of the API

Every body and response is a contract model from `api/schemas/`; the OpenAPI document is
generated from the application and committed at `api/openapi/openapi.json` (`make openapi`,
checked in CI). Errors are `application/problem+json` with the error-taxonomy `code`, the
error class in `error_type`, and the `X-Hawser-Request-Id` of the request.

| Resource | What it does |
|---|---|
| `POST /v1/assets` (multipart `file`) | Admit a file: sniff, limit, probe, hash, store. Returns the `Asset`. |
| `POST /v1/runs` | Create and queue a run. `Idempotency-Key` returns the original run on a repeat. Inputs are asset ids, inline text/JSON, artifacts or `s3://` references; local paths are uploaded by the SDK first. |
| `GET /v1/runs/{id}` · `/result` · `/steps` · `/revisions` | Summary, the `RunResult` (404 `result_not_ready` until the run rests), checkpoints, revisions. |
| `GET /v1/runs/{id}/events` | The event log as JSON, or SSE with `Accept: text/event-stream` (`id` = seq, `Last-Event-ID` resumes, `stream.end` closes it when the run rests). |
| `POST /v1/runs/{id}/cancel` | Immediately when nothing is executing; otherwise at the next step boundary. |
| `POST /v1/runs/{id}/resume` | Re-queue an `awaiting_approval` run (approval decisions do this for you). |
| `/v1/reviews/{run_id}/open` · `/edit` · `/approve` · `/reject` · `/current` | Human review with `If-Match` on the review version (`ETag` on responses). |
| `/v1/approvals` · `/v1/approvals/{id}/decide` | Side-effect approvals; the run resumes once nothing is pending. |
| `/v1/artifacts/{id}` · `/content` · `/url` | Metadata, bytes (proxied), or a presigned URL when S3 is configured with `HAWSERD_ARTIFACTS_PROXY=false`. |
| `/v1/webhooks` · `/{id}/deliveries` | Signed webhooks and their delivery log (`state=failed` is the dead-letter list). |
| `/v1/skills` · `/v1/models` · `/v1/tools` · `/v1/usage` | What this deployment can run, and what it has spent. |
| `/healthz` · `/readyz` | Liveness; readiness with a database ping and queue counts. |

Authentication is `Authorization: Bearer hk_live_...`. Keys belong to a tenant; every object is
tenant-owned and a key never sees another tenant's objects (404, not 403).

## Durable execution

A run is a row in `jobs`. Workers lease a row for `HAWSERD_LEASE_SECONDS`, heartbeat it, and
execute the run with the engine. Every step is checkpointed, so when a worker dies its lease
expires, another worker leases the run and re-executes it: finished steps replay from their
outputs (`step.skipped` events), unfinished ones run again. A worker that discovers its lease
was taken over stops at the next step boundary and discards its work rather than overwrite
the new owner's. After `HAWSERD_WORKER_MAX_ATTEMPTS` leases a run fails with
`worker_attempts_exhausted`.

Signals are rows too: `POST /cancel` on an executing run sets a flag the engine checks before
every step (`Cancelled`, status `cancelled`; the step that was in flight is still
checkpointed). `policy.limits.max_wall_time_s` is checked at the same points. Approval and
review decisions are ordinary writes; an approval decision re-queues the run.

## Webhooks

Register a URL and the events you want (`run.completed`, `run.needs_review`,
`run.awaiting_approval`, `review.decided`). The response shows the secret once. Each delivery
carries:

```
Hawser-Delivery-Id: dlv_...
Hawser-Event: run.completed
Hawser-Signature: t=1760000000,v1=<hex hmac-sha256(secret, "<t>.<body>")>
```

Verify with `hawser_server.webhooks.verify(secret, body, header)` (or the same ten lines in
any language). Non-2xx responses are retried with exponential backoff (10 s doubling, capped at
an hour) for up to `HAWSERD_WEBHOOK_MAX_AGE_S`; then the delivery is `failed` and listed under
`GET /v1/webhooks/{id}/deliveries?state=failed`. Deduplicate on the delivery id.

## The SDK and CLI against a server

```python
from hawser import Client
client = Client(base_url="http://localhost:8080", api_key="hk_live_...")
result = client.run("Extract the invoice.", inputs=["invoice.pdf"], output_schema=Invoice)
```

`Client()` with no arguments reads `HAWSER_SERVER_URL` / `HAWSER_API_KEY` or the `[server]`
table of `hawser.toml`, and falls back to the local runtime. The CLI accepts `--server` and
`--api-key` on every command; `hawser run` streams progress over SSE, and `hawser review`,
`hawser approve`, `hawser inspect`, `hawser runs` work unchanged.

## Conformance

`python -m hawser.conformance` runs the same scenarios (accepted extraction, idempotent
resubmit, budget stop → review, review flow, event ordering, checkpoints, listing, cancel)
against the local runtime, and with `--server URL --api-key KEY` against any deployment that
replays the bundled quickstart recording (`HAWSER_RECORD_MODE=replay`,
`HAWSER_CASSETTE_DIR=<site-packages>/hawser/_quickstart/cassette`). CI runs it on both.
