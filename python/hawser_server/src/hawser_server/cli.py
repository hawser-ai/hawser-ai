"""``hawserd``: serve, worker, migrate, admin, openapi."""

from __future__ import annotations

import json
import logging
import os
import secrets
import tempfile
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console

from hawser_server import __version__
from hawser_server.config import ServerConfig
from hawser_server.services import Services

app = typer.Typer(name="hawserd", help="Hawser control plane.", no_args_is_help=True)
admin_app = typer.Typer(help="Tenants and API keys.", no_args_is_help=True)
app.add_typer(admin_app, name="admin")
console = Console()
err = Console(stderr=True)

DEV_DIR = Path(".hawserd")


def _version(value: bool) -> None:
    if value:
        console.print(f"hawserd {__version__}")
        raise typer.Exit()


@app.callback()
def _main(
    version: Annotated[
        bool, typer.Option("--version", callback=_version, is_eager=True, help="Show the version.")
    ] = False,
) -> None:
    """Hawser control plane: API, durable worker, migrations."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")


def _dev_key() -> str:
    explicit = os.environ.get("HAWSERD_DEV_API_KEY")
    if explicit:
        return explicit
    DEV_DIR.mkdir(exist_ok=True)
    path = DEV_DIR / "dev.key"
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    key = "hk_live_" + secrets.token_urlsafe(30)[:40]
    path.write_text(key + "\n", encoding="utf-8")
    path.chmod(0o600)
    return key


def _config(*, dev: bool, **overrides: Any) -> ServerConfig:
    if dev:
        overrides.setdefault("dev_api_key", _dev_key())
    return ServerConfig.load(**overrides)


@app.command()
def serve(  # noqa: PLR0917 - CLI options
    host: Annotated[str | None, typer.Option("--host")] = None,
    port: Annotated[int | None, typer.Option("--port")] = None,
    dev: Annotated[
        bool, typer.Option("--dev", help="SQLite, local files, in-process worker, dev API key.")
    ] = False,
    with_worker: Annotated[
        bool, typer.Option("--with-worker", help="Run a worker in this process too.")
    ] = False,
    migrate: Annotated[
        bool, typer.Option("--migrate", help="Apply pending migrations before serving.")
    ] = False,
    web_dir: Annotated[
        Path | None, typer.Option("--web-dir", help="Built web app to serve at /app.")
    ] = None,
) -> None:
    """Serve the HTTP API (and the web app at /app when a build is available)."""
    import uvicorn  # noqa: PLC0415 - heavy import only when serving

    from hawser_server.app import create_app  # noqa: PLC0415
    from hawser_server.worker import Worker  # noqa: PLC0415

    cfg = _config(dev=dev, host=host, port=port, web_dir=web_dir)
    svc = Services(cfg, migrate=True if (migrate or dev) else None)
    who = svc.bootstrap()
    if dev and who is not None:
        console.print(
            f"[bold]dev mode[/]: database {cfg.database_url}, artifacts {cfg.artifacts_dir}\n"
            f"API key: [bold]{cfg.dev_api_key}[/]  (tenant {who.tenant_id})\n"
            f"  export HAWSER_SERVER_URL=http://{cfg.host}:{cfg.port} "
            f"HAWSER_API_KEY={cfg.dev_api_key}"
        )
    worker: Worker | None = None
    if with_worker or dev:
        worker = Worker(svc)
        worker.start()
    try:
        uvicorn.run(create_app(svc), host=cfg.host, port=cfg.port, log_level="info")
    finally:
        if worker is not None:
            worker.stop(wait=False)
        svc.close()


@app.command()
def worker(
    concurrency: Annotated[int | None, typer.Option("--concurrency", "-c")] = None,
    dev: Annotated[bool, typer.Option("--dev")] = False,
) -> None:
    """Run a durable worker (lease runs, execute from checkpoints, deliver webhooks)."""
    from hawser_server.worker import Worker  # noqa: PLC0415

    cfg = _config(dev=dev)
    svc = Services(cfg, migrate=True if dev else None)
    svc.bootstrap()
    w = Worker(svc, concurrency=concurrency)
    try:
        w.run_forever()
    except KeyboardInterrupt:
        w.stop()
    finally:
        svc.close()


@app.command()
def migrate(
    status: Annotated[bool, typer.Option("--status", help="Show what is applied.")] = False,
) -> None:
    """Apply pending migrations (forward-only)."""
    from hawser_server.db import open_backend  # noqa: PLC0415
    from hawser_server.migrations import ALL_MIGRATIONS  # noqa: PLC0415

    cfg = ServerConfig.load()
    backend = open_backend(cfg.database_url)
    try:
        if status:
            with backend.transaction() as tx:
                tx.execute(
                    "CREATE TABLE IF NOT EXISTS schema_migrations"
                    " (name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
                )
                done = {
                    str(r[0]): str(r[1])
                    for r in tx.execute("SELECT name, applied_at FROM schema_migrations")
                }
            for m in ALL_MIGRATIONS:
                mark = (
                    f"[green]applied {done[m.name]}[/]" if m.name in done else "[yellow]pending[/]"
                )
                console.print(f"{m.name:24} {mark}")
            return
        from hawser.storage.schema import apply_migrations  # noqa: PLC0415

        applied = apply_migrations(backend, ALL_MIGRATIONS)
        console.print(
            f"applied {len(applied)} migration(s)" + (": " + ", ".join(applied) if applied else "")
        )
    finally:
        backend.close()


@admin_app.command("create-tenant")
def create_tenant(
    name: Annotated[str, typer.Argument()],
    tenant_id: Annotated[str | None, typer.Option("--id", help="Explicit ten_... id.")] = None,
) -> None:
    """Create a tenant."""
    svc = Services(ServerConfig.load(), migrate=None)
    try:
        t = svc.auth.create_tenant(name, tenant_id=tenant_id)
        console.print(f"tenant [bold]{t.id}[/] ({t.name})")
    finally:
        svc.close()


@admin_app.command("list-tenants")
def list_tenants() -> None:
    svc = Services(ServerConfig.load(), migrate=None)
    try:
        for t in svc.auth.list_tenants():
            console.print(f"{t.id}  {t.name}  {t.created_at}")
    finally:
        svc.close()


@admin_app.command("create-key")
def create_key(
    tenant: Annotated[str, typer.Option("--tenant", help="Tenant id.")],
    name: Annotated[str, typer.Option("--name")] = "default",
) -> None:
    """Create an API key for a tenant. The key is printed once."""
    svc = Services(ServerConfig.load(), migrate=None)
    try:
        try:
            key, token = svc.auth.create_key(tenant, name)
        except KeyError:
            err.print(f"[red]unknown tenant {tenant}[/]")
            raise typer.Exit(1) from None
        console.print(f"key [bold]{key.id}[/] for {tenant}\n{token}")
    finally:
        svc.close()


@admin_app.command("revoke-key")
def revoke_key(key_id: Annotated[str, typer.Argument()]) -> None:
    svc = Services(ServerConfig.load(), migrate=None)
    try:
        if svc.auth.revoke_key(key_id):
            console.print(f"revoked {key_id}")
        else:
            err.print(f"[red]unknown key {key_id}[/]")
            raise typer.Exit(1)
    finally:
        svc.close()


def openapi_document() -> str:
    """The OpenAPI document, rendered deterministically."""
    from hawser_server.app import create_app  # noqa: PLC0415

    cfg = ServerConfig(
        database_url="sqlite:///:memory:", artifacts_dir=Path(tempfile.mkdtemp(prefix="hawserd-"))
    )
    svc = Services(cfg, migrate=True)
    try:
        doc = create_app(svc).openapi()
    finally:
        svc.close()
    return json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


@app.command()
def openapi(
    out: Annotated[Path, typer.Option("--out")] = Path("api/openapi/openapi.json"),
    check: Annotated[bool, typer.Option("--check", help="Fail if the file is stale.")] = False,
) -> None:
    """Write (or verify) the generated OpenAPI document."""
    text = openapi_document()
    if check:
        if not out.exists() or out.read_text(encoding="utf-8") != text:
            err.print(f"[red]{out} is stale; run `hawserd openapi`[/]")
            raise typer.Exit(1)
        console.print(f"{out} is up to date")
        return
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    console.print(f"wrote {out}")


__all__ = ["app", "openapi_document"]
