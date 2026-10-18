"""FastAPI dependencies: services, the authenticated principal, tenant-owned objects."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, Request

from hawser.models import Artifact, Asset
from hawser.storage import RunRecord
from hawser_server.auth import Principal
from hawser_server.errors import NotFound, Unauthorized
from hawser_server.services import Services


def services(request: Request) -> Services:
    return request.app.state.services  # type: ignore[no-any-return]


ServicesDep = Annotated[Services, Depends(services)]


def principal(
    svc: ServicesDep,
    authorization: Annotated[str | None, Header()] = None,
) -> Principal:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise Unauthorized("missing bearer token", code="unauthorized")
    token = authorization.split(" ", 1)[1].strip()
    p = svc.auth.authenticate(token)
    if p is None:
        raise Unauthorized("invalid or revoked API key", code="unauthorized")
    return p


PrincipalDep = Annotated[Principal, Depends(principal)]


def owned_run(svc: Services, who: Principal, run_id: str) -> RunRecord:
    run = svc.meta.get_run(run_id)
    if run is None or run.tenant_id != who.tenant_id:
        raise NotFound(f"unknown run {run_id}", code="run_unknown")
    return run


def owned_asset(svc: Services, who: Principal, asset_id: str) -> Asset:
    asset = svc.meta.get_asset(asset_id)
    if asset is None or asset.tenant_id != who.tenant_id:
        raise NotFound(f"unknown asset {asset_id}", code="asset_not_found")
    return asset


def owned_artifact(svc: Services, who: Principal, artifact_id: str) -> Artifact:
    artifact = svc.meta.get_artifact(artifact_id)
    if artifact is None or artifact.tenant_id != who.tenant_id:
        raise NotFound(f"unknown artifact {artifact_id}", code="artifact_not_found")
    return artifact


__all__ = [
    "PrincipalDep",
    "ServicesDep",
    "owned_artifact",
    "owned_asset",
    "owned_run",
    "principal",
    "services",
]
