"""The FastAPI application. ``create_app(services)`` wires routes, errors and request ids."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response

from hawser.models import CONTRACT_VERSION
from hawser_server import __version__
from hawser_server.errors import install_error_handlers
from hawser_server.routes import (
    approvals,
    artifacts,
    assets,
    catalog,
    reviews,
    runs,
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

    for r in (system, assets, runs, reviews, approvals, catalog, artifacts, webhooks):
        app.include_router(r.router)
    return app


__all__ = ["create_app"]
