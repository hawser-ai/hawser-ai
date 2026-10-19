"""Document index v2 (plan WP7.2): native text, OCR for pages without a text layer, and
images (photos, scans) straightened and OCR'd so their fields get real box evidence.

Every index is a checkpointed step. Images go through ``normalise`` (EXIF, size cap) and
``deskew``; the derived image is stored as an artifact and the index records the transform
chain, so anchored evidence maps back to the original pixels (``hawser.evidence.resolve``).
"""

from __future__ import annotations

import json
from typing import Any

from hawser.evidence import DocumentIndex, PageIndex, build_document_index
from hawser.evidence.index import page_from_text
from hawser.models import ArtifactKind, Asset, EventType, MediaClass, StepKind
from hawser.models.common import DpiTransform
from hawser.runtime.context import DocumentContext, RunContext
from hawser.runtime.steps import StepHandle
from hawser_media import ocr as ocr_mod
from hawser_media import ops
from hawser_media import pdf as pdf_mod
from hawser_media.errors import MediaError


def index_documents(ctx: RunContext, assets: list[Asset]) -> list[DocumentContext]:
    """Build (checkpointed) indexes for PDF and image assets; other assets pass through."""
    docs: list[DocumentContext] = []
    for asset in assets:
        if asset.media_class == MediaClass.pdf:
            fn = _index_pdf
        elif asset.media_class == MediaClass.image:
            fn = _index_image
        else:
            docs.append(DocumentContext(asset=asset))
            continue

        def _run(h: StepHandle, a: Asset = asset, f: Any = fn) -> DocumentIndex:
            index: DocumentIndex = f(ctx, h, a)
            art = ctx.artifacts.put_json(
                index,
                kind=ArtifactKind.document_index,
                parents=[a.original_artifact_id],
                run_id=ctx.run_id,
                step_id=h.step_id,
            )
            ctx.artifact_refs.append(art.ref())
            return index

        index = ctx.steps.run(
            f"index:{asset.id}",
            f"Index {asset.name or asset.id}",
            StepKind.tool,
            _run,
            output_type=DocumentIndex,
        )
        docs.append(DocumentContext(asset=asset, index=index))
    return docs


def _engine(ctx: RunContext, *, exclude: str | None = None) -> ocr_mod.OcrEngine | None:
    if ctx.config.ocr_engine == "none":
        return None
    return ocr_mod.get_engine(ctx.config.ocr_engine, exclude=exclude)


def _index_pdf(ctx: RunContext, h: StepHandle, asset: Asset) -> DocumentIndex:
    data = ctx.artifacts.get_bytes(asset.original_artifact_id)
    index = build_document_index(asset.id, pdf_mod.extract_text(data), name=asset.name)
    engine = _engine(ctx)
    if engine is None:
        return index
    for i, page in enumerate(index.pages):
        if not page.needs_ocr or page.source == "ocr":
            continue
        index.pages[i] = ocr_pdf_page(ctx, h, asset, data, page, engine, dpi=ctx.config.ocr_dpi)
    return index


def ocr_pdf_page(  # noqa: PLR0917 - internal helper
    ctx: RunContext,
    h: StepHandle,
    asset: Asset,
    data: bytes,
    page: PageIndex,
    engine: ocr_mod.OcrEngine,
    *,
    dpi: float,
) -> PageIndex:
    """Render a page without a text layer and OCR it; boxes come back in PDF points."""
    rendered = pdf_mod.render_page(data, page.page, dpi=dpi)
    art = ctx.artifacts.put(
        rendered.png,
        kind=ArtifactKind.rendered_page,
        media_type="image/png",
        parents=[asset.original_artifact_id],
        transform=DpiTransform(dpi=rendered.dpi),
        media={"page": page.page, "width": rendered.width_px, "height": rendered.height_px},
        run_id=ctx.run_id,
        step_id=h.step_id,
    )
    ctx.artifact_refs.append(art.ref())
    try:
        result = engine.recognize(rendered.png)
    except MediaError as exc:
        ctx.warn("ocr_failed", f"OCR failed on page {page.page}: {exc.message}", page=page.page)
        return page
    _store_ocr(ctx, h, asset, result, page=page.page, parent=art.id)
    pt = ocr_mod.page_text_from_ocr(result, page=page.page, scale=72.0 / rendered.dpi)
    # Keep the page's true size (OCR reports the raster size, which rounds).
    pt.width_pt, pt.height_pt = page.width_pt, page.height_pt
    new = page_from_text(pt, source="ocr", ocr_engine=result.engine)
    new.render_artifact_id = art.id
    new.render_dpi = rendered.dpi
    ctx.emitter.emit(
        EventType.tool_called,
        {
            "tool": f"ocr.{result.engine}",
            "asset_id": asset.id,
            "page": page.page,
            "dpi": rendered.dpi,
            "lines": len(result.lines),
            "words": len(result.words),
            "latency_ms": result.duration_ms,
        },
        step_id=h.step_id,
    )
    return new


def _index_image(ctx: RunContext, h: StepHandle, asset: Asset) -> DocumentIndex:
    data = ctx.artifacts.get_bytes(asset.original_artifact_id)
    chain: list[dict[str, Any]] = []
    try:
        derived = ops.normalise(data)
        chain += derived.chain
        if ctx.config.deskew:
            straight = ops.deskew(derived.png)
            chain += straight.chain
            derived = straight
    except MediaError as exc:
        ctx.warn("image_normalise_failed", exc.message, asset_id=asset.id)
        return DocumentIndex(asset_id=asset.id, name=asset.name, pages=[])
    artifact_id: str | None = None
    if chain:
        art = ctx.artifacts.put(
            derived.png,
            kind=ArtifactKind.normalised,
            media_type="image/png",
            parents=[asset.original_artifact_id],
            media={
                "width": derived.width,
                "height": derived.height,
                "transform_chain": json.dumps(chain),
                "asset_id": asset.id,
            },
            run_id=ctx.run_id,
            step_id=h.step_id,
        )
        ctx.artifact_refs.append(art.ref())
        artifact_id = art.id
    engine = _engine(ctx)
    page = PageIndex(
        page=1,
        width_pt=float(derived.width),
        height_pt=float(derived.height),
        text="",
        needs_ocr=True,
        coordinate_system="pixels",
        artifact_id=artifact_id,
        transform_chain=chain,
        render_artifact_id=artifact_id,
    )
    if engine is not None:
        try:
            result = engine.recognize(derived.png)
        except MediaError as exc:
            ctx.warn("ocr_failed", f"OCR failed: {exc.message}", asset_id=asset.id)
            result = None
        if result is not None:
            _store_ocr(
                ctx, h, asset, result, page=1, parent=artifact_id or asset.original_artifact_id
            )
            pt = ocr_mod.page_text_from_ocr(result, page=1, scale=1.0)
            page = page_from_text(
                pt, source="ocr", ocr_engine=result.engine, coordinate_system="pixels"
            )
            page.artifact_id = artifact_id
            page.render_artifact_id = artifact_id
            page.transform_chain = chain
            ctx.emitter.emit(
                EventType.tool_called,
                {
                    "tool": f"ocr.{result.engine}",
                    "asset_id": asset.id,
                    "page": 1,
                    "deskew_degrees": next(
                        (t.get("degrees") for t in chain if t.get("kind") == "rotate"), 0.0
                    ),
                    "lines": len(result.lines),
                    "words": len(result.words),
                    "latency_ms": result.duration_ms,
                },
                step_id=h.step_id,
            )
    return DocumentIndex(asset_id=asset.id, name=asset.name, pages=[page])


def _store_ocr(
    ctx: RunContext,
    h: StepHandle,
    asset: Asset,
    result: ocr_mod.OcrResult,
    *,
    page: int,
    parent: str,
) -> None:
    art = ctx.artifacts.put_json(
        result,
        kind=ArtifactKind.ocr,
        parents=[parent],
        media={"page": page, "engine": result.engine, "asset_id": asset.id},
        run_id=ctx.run_id,
        step_id=h.step_id,
    )
    ctx.artifact_refs.append(art.ref())


def reocr_documents(
    ctx: RunContext, docs: list[DocumentContext], *, attempt: int
) -> list[DocumentContext] | None:
    """The ``reocr`` repair: OCR again at a higher DPI, with a different engine when one is
    available. Returns new contexts, or ``None`` when nothing could be re-OCR'd."""
    current = next(
        (p.ocr_engine for d in docs if d.index for p in d.index.pages if p.source == "ocr"), None
    )
    engine = _engine(ctx, exclude=current) or _engine(ctx)
    if engine is None:
        return None
    dpi = ctx.config.ocr_dpi * (1.0 + 0.34 * attempt)
    changed = False
    out: list[DocumentContext] = []
    for d in docs:
        if d.index is None or not any(p.source == "ocr" for p in d.index.pages):
            out.append(d)
            continue

        def _run(h: StepHandle, d: DocumentContext = d, dpi: float = dpi) -> DocumentIndex:
            assert d.index is not None
            if d.asset.media_class == MediaClass.pdf:
                data = ctx.artifacts.get_bytes(d.asset.original_artifact_id)
                pages = [
                    ocr_pdf_page(ctx, h, d.asset, data, p, engine, dpi=dpi)
                    if p.source == "ocr"
                    else p
                    for p in d.index.pages
                ]
                return DocumentIndex(asset_id=d.asset.id, name=d.asset.name, pages=pages)
            page = d.index.pages[0]
            source = ctx.artifacts.get_bytes(page.artifact_id or d.asset.original_artifact_id)
            result = engine.recognize(source)
            pt = ocr_mod.page_text_from_ocr(result, page=1, scale=1.0)
            new = page_from_text(
                pt, source="ocr", ocr_engine=result.engine, coordinate_system="pixels"
            )
            new.artifact_id, new.render_artifact_id = page.artifact_id, page.artifact_id
            new.transform_chain = page.transform_chain
            return DocumentIndex(asset_id=d.asset.id, name=d.asset.name, pages=[new])

        index = ctx.steps.run(
            f"reocr{attempt}:{d.asset.id}",
            f"Re-OCR {d.asset.name or d.asset.id} ({engine.name}, {dpi:.0f} dpi)",
            StepKind.tool,
            _run,
            output_type=DocumentIndex,
        )
        out.append(DocumentContext(asset=d.asset, index=index))
        changed = True
    return out if changed else None


__all__ = ["index_documents", "ocr_pdf_page", "reocr_documents"]
