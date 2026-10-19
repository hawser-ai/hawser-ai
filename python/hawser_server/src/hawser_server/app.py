"""The FastAPI application. ``create_app(services)`` wires routes, errors and request ids."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from importlib import resources
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse

from hawser.models import CONTRACT_VERSION
from hawser_server import __version__
from hawser_server.errors import install_error_handlers
from hawser_server.routes import (
    approvals,
    artifacts,
    assets,
    catalog,
    evals,
    examples,
    reviews,
    runs,
    settings,
    system,
    webhooks,
)
from hawser_server.services import Services

DESCRIPTION = (
    "Hawser control plane. Bodies and responses are the contract models published under "
    "`api/schemas/`; errors are `application/problem+json` with the error-taxonomy `code`."
)


def create_app(services: Services) -> FastAPI:
    app = FastAPI(
        title="Hawser API",
        version=f"{CONTRACT_VERSION}+hawserd.{__version__}",
        description=DESCRIPTION,
        docs_url="/docs",
        openapi_url="/openapi.json",
    )
    app.state.services = services
    install_error_handlers(app)

    @app.middleware("http")
    async def _request_id(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        rid = request.headers.get("X-Hawser-Request-Id") or f"req_{uuid.uuid4().hex[:20]}"
        request.state.request_id = rid
        response = await call_next(request)
        response.headers["X-Hawser-Request-Id"] = rid
        return response

    if services.config.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=services.config.cors_origins,
            allow_methods=["*"],
            allow_headers=["*"],
            expose_headers=["ETag", "Location", "X-Hawser-Request-Id", "X-Hawser-Dpi"],
        )

    for r in (
        system,
        assets,
        runs,
        reviews,
        approvals,
        catalog,
        artifacts,
        webhooks,
        evals,
        settings,
        examples,
    ):
        app.include_router(r.router)
    _mount_web(app, services.config.web_dir)
    return app


def web_dir(configured: Path | None) -> Path | None:
    """The built web app: an explicit directory, else the package's ``static`` folder."""
    if configured is not None:
        return configured if (configured / "index.html").exists() else None
    with resources.as_file(resources.files("hawser_server") / "static") as path:
        bundled = Path(path)
    return bundled if (bundled / "index.html").exists() else None


def _mount_web(app: FastAPI, configured: Path | None) -> None:
    root = web_dir(configured)
    if root is None:
        return
    index = root / "index.html"

    @app.get("/", include_in_schema=False)
    async def _root() -> RedirectResponse:
        return RedirectResponse("/app/")

    @app.get("/app", include_in_schema=False)
    @app.get("/app/{path:path}", include_in_schema=False)
    async def _app(path: str = "") -> FileResponse:
        # Single-page app: real files are served as-is, every other path gets index.html.
        candidate = (root / path).resolve() if path else index
        if path and candidate.is_file() and root.resolve() in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(index)


__all__ = ["create_app"]
