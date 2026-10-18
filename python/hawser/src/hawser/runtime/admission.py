"""Asset admission (blueprint §4.2): resolve inputs, sniff, limit, probe, hash, store."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from hawser.clock import now
from hawser.config import HawserConfig
from hawser.errors import AdmissionError, BudgetExceeded, ToolError
from hawser.hashing import content_hash, scoped_hash
from hawser.ids import new_id
from hawser.models import (
    ArtifactInput,
    ArtifactKind,
    ArtifactRef,
    Asset,
    AssetInput,
    AssetManifest,
    EventType,
    InlineInput,
    InputRef,
    Limits,
    MediaClass,
    PathInput,
    StorageInput,
)
from hawser.models.asset import ImageProbe, LimitsApplied, PdfProbe, TextProbe
from hawser.runtime.artifacts import ArtifactService
from hawser.runtime.context import RunContext
from hawser.storage import MetadataStore
from hawser_media import MediaKind, sniff
from hawser_media import image as image_mod
from hawser_media import pdf as pdf_mod
from hawser_media import text as text_mod
from hawser_media.errors import MediaError

_NOT_YET = {
    MediaKind.audio: "Phase 8",
    MediaKind.video: "Phase 9",
}


class AssetList(BaseModel):
    assets: list[Asset]


def _no_emit(type: EventType, payload: dict[str, Any]) -> None:
    return


@dataclass
class Admitter:
    """What admission needs. Built from a run, or standalone for uploads without a run."""

    config: HawserConfig
    tenant_id: str
    meta: MetadataStore
    artifacts: ArtifactService
    limits: Limits = field(default_factory=Limits)
    run_id: str | None = None
    emit: Callable[[EventType, dict[str, Any]], None] = _no_emit
    artifact_refs: list[ArtifactRef] = field(default_factory=list)
    object_reader: Callable[[str], bytes] | None = None

    @classmethod
    def from_run(cls, ctx: RunContext) -> Admitter:
        def emit(type: EventType, payload: dict[str, Any]) -> None:
            ctx.emitter.emit(type, payload)

        return cls(
            config=ctx.config,
            tenant_id=ctx.tenant_id,
            meta=ctx.meta,
            artifacts=ctx.artifacts,
            limits=ctx.request.policy.limits,
            run_id=ctx.run_id,
            emit=emit,
            artifact_refs=ctx.artifact_refs,
            object_reader=ctx.object_reader,
        )


def _load(ctx: Admitter, ref: InputRef) -> tuple[bytes, str | None, str | None]:
    """Return ``(bytes, name, declared_media_type)`` for a non-asset input."""
    if isinstance(ref, PathInput):
        path = Path(ref.path).expanduser()
        if not path.is_file():
            raise AdmissionError(
                f"input file not found: {path}", code="input_not_found", details={"path": str(path)}
            )
        return path.read_bytes(), ref.name or path.name, None
    if isinstance(ref, InlineInput):
        if ref.json_value is not None:
            return (
                json.dumps(ref.json_value).encode("utf-8"),
                ref.name or "inline.json",
                "application/json",
            )
        return (ref.text or "").encode("utf-8"), ref.name or "inline.txt", "text/plain"
    if isinstance(ref, ArtifactInput):
        artifact = ctx.meta.get_artifact(ref.artifact_id)
        if artifact is None:
            raise AdmissionError(
                f"artifact not found: {ref.artifact_id}", code="artifact_not_found"
            )
        return (
            ctx.artifacts.get_bytes(artifact.id),
            ref.name or artifact.id,
            artifact.meta.media_type,
        )
    if isinstance(ref, StorageInput):
        if ctx.object_reader is None:
            raise AdmissionError(
                "object-storage references are not supported by this runtime",
                code="storage_refs_unsupported",
                details={"uri": ref.uri},
            )
        try:
            data = ctx.object_reader(ref.uri)
        except Exception as exc:
            raise AdmissionError(
                f"could not read {ref.uri}: {exc}", code="storage_read_failed"
            ) from exc
        return data, ref.name or ref.uri.rsplit("/", 1)[-1], None
    raise AdmissionError(f"unsupported input kind {ref.kind}", code="input_kind_unsupported")


def admit_inputs(run_ctx: RunContext, inputs: list[InputRef]) -> AssetList:
    if not inputs:
        return AssetList(assets=[])
    ctx = Admitter.from_run(run_ctx)
    assets: list[Asset] = []
    for ref in inputs:
        if isinstance(ref, AssetInput):
            asset = ctx.meta.get_asset(ref.asset_id)
            if asset is None or asset.tenant_id != ctx.tenant_id:
                raise AdmissionError(f"asset not found: {ref.asset_id}", code="asset_not_found")
            assets.append(asset)
            continue
        data, name, declared = _load(ctx, ref)
        assets.append(admit_bytes(ctx, data, name=name, declared_media_type=declared))
    return AssetList(assets=assets)


def admit_bytes(
    ctx: Admitter, data: bytes, *, name: str | None, declared_media_type: str | None = None
) -> Asset:
    cfg = ctx.config
    limits = LimitsApplied(
        max_bytes=cfg.max_bytes, max_pages=cfg.max_pages, max_pixels=cfg.max_pixels
    )
    if len(data) == 0:
        raise AdmissionError(f"input {name!r} is empty", code="input_empty")
    if len(data) > cfg.max_bytes:
        raise AdmissionError(
            f"input {name!r} is {len(data)} bytes; limit is {cfg.max_bytes}",
            code="input_too_large",
            details={"size": len(data), "max_bytes": cfg.max_bytes},
        )
    sniffed = sniff(data, filename=name)
    if sniffed is None:
        raise AdmissionError(
            f"input {name!r} is not a supported media type", code="unsupported_media_type"
        )
    # Declared types are advisory for text-ish inputs; a hard mismatch on binary is an error.
    if (
        declared_media_type
        and declared_media_type != sniffed.media_type
        and sniffed.kind not in (MediaKind.text, MediaKind.json)
    ):
        raise AdmissionError(
            f"declared type {declared_media_type} does not match {sniffed.media_type}",
            code="media_type_mismatch",
        )
    if sniffed.kind == MediaKind.office:
        from hawser.tools.convert import office_to_pdf  # noqa: PLC0415 - tools import the runtime

        try:
            converted = office_to_pdf(data, sniffed.media_type)
        except ToolError as exc:
            raise AdmissionError(f"{name!r}: {exc.message}", code=exc.code) from exc
        ctx.emit(
            EventType.tool_called,
            {
                "tool": "convert.office_to_pdf",
                "name": name,
                "bytes_in": len(data),
                "bytes_out": len(converted),
            },
        )
        pdf_name = (name or "document").rsplit(".", 1)[0] + ".pdf"
        return admit_bytes(ctx, converted, name=pdf_name, declared_media_type="application/pdf")
    if sniffed.kind in _NOT_YET:
        raise AdmissionError(
            f"{sniffed.kind.value} inputs are not supported yet ({_NOT_YET[sniffed.kind]})",
            code=f"{sniffed.kind.value}_not_supported",
        )

    ch = content_hash(data)
    sh = scoped_hash(ctx.tenant_id, ch)
    existing = ctx.meta.find_asset(ctx.tenant_id, sh)
    if existing is not None:
        ctx.emit(EventType.asset_admitted, {"asset_id": existing.id, "name": name, "dedup": True})
        return existing

    manifest = AssetManifest(
        media_class=MediaClass(sniffed.kind.value),
        media_type=sniffed.media_type,
        declared_media_type=declared_media_type,
        size_bytes=len(data),
        limits=limits,
    )
    try:
        _probe(manifest, sniffed.kind, data, cfg.max_pages, cfg.max_pixels)
    except MediaError as exc:
        ctx.emit(EventType.asset_rejected, {"name": name, "code": exc.code, "message": exc.message})
        raise AdmissionError(f"{name!r}: {exc.message}", code=exc.code) from exc

    lim = ctx.limits
    if (
        manifest.pdf is not None
        and lim.max_pages is not None
        and manifest.pdf.page_count > lim.max_pages
    ):
        raise BudgetExceeded(
            f"{name!r} has {manifest.pdf.page_count} pages; policy allows {lim.max_pages}",
            code="max_pages",
            details={"pages": manifest.pdf.page_count, "limit": lim.max_pages},
        )
    if (
        manifest.image is not None
        and lim.max_pixels is not None
        and manifest.image.width * manifest.image.height > lim.max_pixels
    ):
        raise BudgetExceeded(
            f"{name!r} exceeds the policy pixel limit {lim.max_pixels}", code="max_pixels"
        )

    original = ctx.artifacts.put(
        data, kind=ArtifactKind.original, media_type=sniffed.media_type, run_id=ctx.run_id
    )
    asset = Asset(
        id=new_id("asset"),
        tenant_id=ctx.tenant_id,
        name=name,
        media_class=manifest.media_class,
        media_type=sniffed.media_type,
        size_bytes=len(data),
        content_hash=ch,
        scoped_hash=sh,
        original_artifact_id=original.id,
        manifest=manifest,
        created_at=now(),
    )
    ctx.meta.put_asset(asset)
    ctx.artifact_refs.append(original.ref())
    ctx.emit(
        EventType.asset_admitted,
        {
            "asset_id": asset.id,
            "name": name,
            "media_type": asset.media_type,
            "size_bytes": asset.size_bytes,
            "execution_classes": manifest.execution_classes,
        },
    )
    return asset


def _probe(
    manifest: AssetManifest, kind: MediaKind, data: bytes, max_pages: int, max_pixels: int
) -> None:
    if kind == MediaKind.pdf:
        info = pdf_mod.probe(data)
        if info.page_count > max_pages:
            raise MediaError(
                "pdf_too_many_pages", f"{info.page_count} pages exceeds the limit of {max_pages}"
            )
        if info.page_count == 0:
            raise MediaError("pdf_empty", "the PDF has no pages")
        manifest.pdf = PdfProbe(
            page_count=info.page_count,
            encrypted=info.encrypted,
            has_forms=info.has_forms,
            chars_per_page=info.chars_per_page,
            page_sizes_pt=info.page_sizes_pt,
        )
        needs = [p for p in range(1, info.page_count + 1) if info.needs_ocr(p)]
        classes: list[str] = []
        if len(needs) < info.page_count:
            classes.append("document.native_text")
        if needs:
            classes.append("document.needs_ocr")
            manifest.warnings.append(
                f"{len(needs)} page(s) have no text layer; extraction is best-effort until OCR"
            )
        manifest.execution_classes = classes
    elif kind == MediaKind.image:
        info = image_mod.probe(data, max_pixels=max_pixels)
        if info.frames > 1:
            raise MediaError("image_animated", "animated images are not accepted")
        manifest.image = ImageProbe(
            width=info.width,
            height=info.height,
            mode=info.mode,
            exif_orientation=info.exif_orientation,
            has_icc=info.has_icc,
            frames=info.frames,
        )
        manifest.execution_classes = ["image.vision"]
    elif kind in (MediaKind.text, MediaKind.json):
        info = text_mod.probe(data, expect_json=kind == MediaKind.json)
        manifest.text = TextProbe(chars=info.chars, valid_json=info.valid_json)
        manifest.execution_classes = ["text"]


__all__ = ["Admitter", "AssetList", "admit_bytes", "admit_inputs"]
