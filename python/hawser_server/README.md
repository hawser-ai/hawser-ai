# hawser-server

`hawserd`: the Hawser control plane (ADR-0010). A FastAPI application, a leased durable worker,
forward-only migrations and admin commands, over Postgres and S3-compatible storage.

```bash
hawserd migrate
hawserd admin create-tenant acme
hawserd admin create-key --tenant ten_... --name ci
hawserd serve                # API
hawserd worker               # durable worker (any number of processes)
hawserd serve --dev          # SQLite + local files + in-process worker, for evaluation
```

See `docs/operator/` for configuration, deployment and backup.
