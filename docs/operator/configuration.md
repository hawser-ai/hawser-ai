# Configuration reference

`hawserd` reads `HAWSERD_*` for the server and `HAWSER_*` for the engine. Nothing is read from
files except an optional `.env` next to the compose file.

## Server (`HAWSERD_*`)

| Variable | Default | Meaning |
|---|---|---|
| `HAWSERD_DATABASE_URL` | `sqlite:///./.hawserd/hawserd.db` | `postgresql://user:pass@host:5432/db` in production. SQLite is single-node only. |
| `HAWSERD_ARTIFACTS_DIR` | `./.hawserd/store` | Local artifact directory; ignored when `HAWSERD_S3_BUCKET` is set. |
| `HAWSERD_S3_BUCKET` / `_S3_ENDPOINT_URL` / `_S3_REGION` / `_S3_ACCESS_KEY` / `_S3_SECRET_KEY` | unset | S3-compatible artifact store (RustFS in Compose; any S3 endpoint works). The bucket is created at start-up if missing. |
| `HAWSERD_S3_PRESIGN_TTL_S` | `900` | Lifetime of presigned URLs (max 7 days). |
| `HAWSERD_ARTIFACTS_PROXY` | `true` | Serve artifact bytes through the API. `false` returns presigned URLs from `/v1/artifacts/{id}/url`. |
| `HAWSERD_HOST` / `HAWSERD_PORT` | `127.0.0.1` / `8080` | Bind address. |
| `HAWSERD_PUBLIC_URL` | `http://127.0.0.1:8080` | How clients reach the server; used in webhook payloads and artifact URLs. |
| `HAWSERD_DEV_API_KEY` | unset | Bootstrap the local tenant with this key at start-up. Development and Compose only. |
| `HAWSERD_LEASE_SECONDS` | `60` | Job lease; a worker heartbeats every third of it. Longer than your slowest single step. |
| `HAWSERD_WORKER_CONCURRENCY` | `2` | Runs executed in parallel per worker process. |
| `HAWSERD_WORKER_MAX_ATTEMPTS` | `3` | Leases before a run is abandoned as `worker_attempts_exhausted`. |
| `HAWSERD_POLL_INTERVAL_S` | `0.5` | Worker and SSE polling interval. |
| `HAWSERD_MAX_UPLOAD_BYTES` | `52428800` | `POST /v1/assets` size limit. |
| `HAWSERD_WEBHOOK_TIMEOUT_S` / `_WEBHOOK_MAX_AGE_S` | `10` / `86400` | Delivery timeout; retry window before dead-lettering. |
| `HAWSERD_SSE_KEEPALIVE_S` | `15` | Comment keep-alive interval on event streams. |
| `HAWSERD_CORS_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` | Browser origins allowed to call the API (the web dev server). The bundled app at `/app` is same-origin and needs none. |
| `HAWSERD_WEB_DIR` | bundled | Directory with a built web app to serve at `/app` (overrides the bundle). |
| `HAWSERD_PAGE_CACHE_ENTRIES` | `64` | Rendered pages kept in memory per process for the evidence viewer. |

## Engine (`HAWSER_*`)

The same variables the local runtime uses (`hawser.config.HawserConfig`): `HAWSER_DEFAULT_MODEL`,
`HAWSER_DEFAULT_EFFORT`, `HAWSER_MAX_PAGES`, `HAWSER_MAX_PIXELS`, `HAWSER_MAX_BYTES`,
`HAWSER_RENDER_DPI`, `HAWSER_CONTEXT_FRACTION`, `HAWSER_STORE_RAW`, `HAWSER_RECORD_MODE`,
`HAWSER_CASSETTE_DIR`, `HAWSER_HOME` (plan cache, cassettes), `HAWSER_SKILL_PATHS`
(path-separator or comma separated), `HAWSER_OCR_ENGINE` (`auto|rapidocr|tesseract|azure-document-intelligence|none`), `HAWSER_OCR_DPI` (300), `HAWSER_DESKEW` (true). The Docker image bundles RapidOCR; a bare-metal install needs `pip install 'hawser-ai[ocr]'` or a `tesseract` binary, and `AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT`/`_KEY` for the hosted engine. `HAWSER_IMAGE_API_BASE`/`HAWSER_IMAGE_API_KEY` (or `OPENAI_API_KEY`) serve `image.generate|edit` when a policy enables generation. Provider credentials are the usual `ANTHROPIC_API_KEY`,
`OPENROUTER_API_KEY`, `OPENAI_API_KEY`; they live only on worker processes.

## Secrets

- API keys are stored as argon2id hashes; the plaintext is shown once by
  `hawserd admin create-key`. Revoke with `hawserd admin revoke-key`.
- Webhook secrets are stored in the database (they sign outgoing requests). Rotate by deleting
  and re-registering the webhook.
- Model provider keys are environment variables on workers only; `hawserd serve` does not need
  them.
- Never set `HAWSERD_DEV_API_KEY` on a server reachable from the internet; create tenants and
  keys with the admin commands instead.
