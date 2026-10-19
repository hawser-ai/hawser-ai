from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, File, Form, Query, Response, UploadFile

from hawser.models import Asset, MediaClass
from hawser.runtime.admission import Admitter, admit_bytes
from hawser_media import pdf as pdf_mod
from hawser_server.deps import PrincipalDep, ServicesDep, owned_asset
from hawser_server.errors import BadRequest, NotFound, PayloadTooLarge

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


@router.get("/{asset_id}/pages/{page}", response_class=Response)
def render_page(
    svc: ServicesDep,
    who: PrincipalDep,
    asset_id: str,
    page: int,
    dpi: Annotated[float, Query(ge=36, le=300)] = 110,
) -> Response:
    """A page of a PDF rendered to PNG (or an image asset's bytes). Evidence bboxes in PDF
    points map to pixels by ``dpi / 72``; the response says which dpi was used."""
    asset = owned_asset(svc, who, asset_id)
    artifacts = svc.engine.artifacts_for(who.tenant_id)
    if asset.media_class == MediaClass.image:
        if page != 1:
            raise NotFound("images have one page", code="page_out_of_range")
        data = artifacts.get_bytes(asset.original_artifact_id)
        return Response(content=data, media_type=asset.media_type, headers={"X-Hawser-Dpi": "0"})
    if asset.media_class != MediaClass.pdf:
        raise BadRequest(f"{asset.media_class.value} assets have no pages", code="no_pages")
    count = asset.manifest.pdf.page_count if asset.manifest.pdf else 0
    if page < 1 or page > count:
        raise NotFound(f"page {page} of {count}", code="page_out_of_range")
    key = (asset.id, page, round(dpi, 1))
    png = svc.page_cache.get(key)
    if png is None:
        rendered = pdf_mod.render_page(
            artifacts.get_bytes(asset.original_artifact_id), page, dpi=dpi
        )
        png = rendered.png
        svc.page_cache.put(key, png)
    return Response(
        content=png,
        media_type="image/png",
        headers={"X-Hawser-Dpi": str(dpi), "Cache-Control": "private, max-age=3600"},
    )
