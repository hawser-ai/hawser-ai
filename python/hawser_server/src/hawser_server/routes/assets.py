from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, File, Form, Response, UploadFile

from hawser.models import Asset
from hawser.runtime.admission import Admitter, admit_bytes
from hawser_server.deps import PrincipalDep, ServicesDep, owned_asset
from hawser_server.errors import PayloadTooLarge

router = APIRouter(prefix="/v1/assets", tags=["assets"])


@router.post("", response_model=Asset, status_code=201)
def upload_asset(
    svc: ServicesDep,
    who: PrincipalDep,
    response: Response,
    file: Annotated[UploadFile, File()],
    name: Annotated[str | None, Form()] = None,
) -> Asset:
    """Admit a file (sniff, limit, probe, hash, store). Runs then reference it by id."""
    limit = svc.config.max_upload_bytes
    chunks: list[bytes] = []
    size = 0
    while True:
        chunk = file.file.read(1024 * 1024)
        if not chunk:
            break
        size += len(chunk)
        if size > limit:
            raise PayloadTooLarge(
                f"upload exceeds {limit} bytes", code="payload_too_large", details={"limit": limit}
            )
        chunks.append(chunk)
    data = b"".join(chunks)
    adm = Admitter(
        config=svc.config.runtime,
        tenant_id=who.tenant_id,
        meta=svc.meta,
        artifacts=svc.engine.artifacts_for(who.tenant_id),
        object_reader=svc.engine.object_reader,
    )
    asset = admit_bytes(adm, data, name=name or file.filename, declared_media_type=None)
    response.headers["Location"] = f"/v1/assets/{asset.id}"
    return asset


@router.get("/{asset_id}", response_model=Asset)
def get_asset(svc: ServicesDep, who: PrincipalDep, asset_id: str) -> Asset:
    return owned_asset(svc, who, asset_id)
