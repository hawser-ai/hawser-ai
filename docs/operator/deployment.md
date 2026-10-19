# Deployment

## Docker Compose (reference)

```bash
make up            # builds deploy/docker/Dockerfile, starts postgres, s3 (RustFS), hawserd, worker
make logs
make down          # keeps the volumes
```

`deploy/compose/docker-compose.yaml` wires `HAWSERD_*` for you. Put real values in a `.env`
file next to it before exposing the stack: `HAWSER_DEV_API_KEY` (or unset it and create keys
with the admin commands), `POSTGRES_PASSWORD`, `S3_ACCESS_KEY`/`S3_SECRET_KEY`, and your provider
keys. The object store is [RustFS](https://github.com/rustfs/rustfs) (Apache-2.0, S3-compatible,
MinIO-style configuration; MinIO's public images are no longer pullable). Any S3-compatible
service works: point `HAWSERD_S3_*` at it.
Scale workers with `docker compose up -d --scale worker=4`.

The image is a multi-stage build (Node 20 for the web app, `uv` for Python) on
`python:3.12-slim`, non-root, with a `/healthz` health check. It contains `hawserd`, the
`hawser` CLI and the web app at `/app`; it does not contain LibreOffice, so Office inputs are
rejected unless you add it to the image.

## Bare metal / your own orchestrator

1. `pip install hawser-server` (or `uv sync --all-packages` in the repo).
2. Postgres 14+ and an S3-compatible bucket. Export `HAWSERD_DATABASE_URL`, the `HAWSERD_S3_*`
   variables and `HAWSERD_PUBLIC_URL`.
3. `hawserd migrate`, then `hawserd admin create-tenant <name>` and
   `hawserd admin create-key --tenant <id> --name <label>`.
4. Run `hawserd serve` behind your TLS terminator (it speaks plain HTTP; keep SSE responses
   unbuffered — the server sets `X-Accel-Buffering: no`). Run any number of `hawserd worker`
   processes with the provider keys in their environment.
5. Point clients at it: `hawser init --server https://hawser.example.com`,
   `export HAWSER_API_KEY=...`.

Readiness: `GET /readyz` returns 503 until the database answers. Liveness: `GET /healthz`.

## Sizing

- The API process is I/O bound; one process per core is plenty. SSE streams poll the database
  every `HAWSERD_POLL_INTERVAL_S`; hundreds of concurrent streams are fine, thousands want a
  longer interval.
- Workers are bound by model latency, not CPU, except PDF rendering for scanned pages. Start
  with concurrency 2–4 per process and scale processes.
- `HAWSERD_LEASE_SECONDS` must exceed your slowest single step (one model call on the largest
  document you accept), or a healthy worker will look dead and its run will be re-leased.

## Development mode

`hawserd serve --dev` needs nothing: SQLite at `.hawserd/hawserd.db`, artifacts in
`.hawserd/store`, an in-process worker, and a dev key in `.hawserd/dev.key`. It is the same
code path as production except for the database and the process model. Do not expose it.
