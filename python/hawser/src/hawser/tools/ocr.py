"""OCR and layout tools (plan WP7.2): run an engine on an image or page, analyse layout."""

from __future__ import annotations

import json
from typing import Any

from hawser.errors import ToolError
from hawser.models import ArtifactKind, ProducerRef, SideEffect
from hawser.runtime.context import RunContext
from hawser.tools.image import load_image
from hawser.tools.registry import tool
from hawser_media import layout as layout_mod
from hawser_media import ocr as ocr_mod
from hawser_media import pdf as pdf_mod
from hawser_media.errors import MediaError


@tool(
    "ocr.recognize",
    description="OCR an image (asset or artifact): lines and words with pixel boxes and "
    "confidence. engine: auto | rapidocr | tesseract | azure-document-intelligence.",
    modalities=["image", "document"],
    side_effect=SideEffect.local_artifact,
)
def recognize(ctx: RunContext, ref: str, engine: str = "auto") -> dict[str, Any]:
    src = load_image(ctx, ref)
    eng = ocr_mod.get_engine(engine if engine != "auto" else ctx.config.ocr_engine)
    if eng is None:
        raise ToolError(
            f"no OCR engine available for {engine!r} (install hawser-ai[ocr] or tesseract)",
            code="ocr_unavailable",
            retryable=False,
        )
    try:
        result = eng.recognize(src.data)
    except MediaError as exc:
        raise ToolError(exc.message, code=exc.code) from exc
    art = ctx.artifacts.put_json(
        result,
        kind=ArtifactKind.ocr,
        parents=[src.artifact_id],
        producer=ProducerRef(kind="tool", name=f"ocr.{result.engine}", version="1"),
        media={"engine": result.engine, "transform_chain": json.dumps(src.chain)},
        run_id=ctx.run_id,
    )
    ctx.artifact_refs.append(art.ref())
    return {
        "artifact_id": art.id,
        "engine": result.engine,
        "width": result.width,
        "height": result.height,
        "text": result.text,
        "lines": [json.loads(ln.model_dump_json()) for ln in result.lines],
        "duration_ms": result.duration_ms,
    }


@tool(
    "ocr.engines",
    description="Which OCR engines this deployment can use.",
    modalities=["image", "document"],
)
def engines(ctx: RunContext) -> dict[str, Any]:
    return {"available": ocr_mod.available_engines(), "configured": ctx.config.ocr_engine}


@tool(
    "document.layout",
    description="Layout analysis of a PDF page or an image: blocks (text/table/header/footer) in "
    "reading order, table cell grids, key-value pairs, all with boxes.",
    modalities=["document", "image"],
    produces_evidence=True,
)
def layout(ctx: RunContext, ref: str, page: int = 1) -> dict[str, Any]:
    if ref.startswith("asset_"):
        asset = ctx.meta.get_asset(ref)
        if asset is None or asset.tenant_id != ctx.tenant_id:
            raise ToolError(f"unknown asset {ref}", code="asset_not_found", retryable=False)
        if asset.media_class.value == "pdf":
            data = ctx.artifacts.get_bytes(asset.original_artifact_id)
            pages = pdf_mod.extract_text(data, pages=[page])
            if not pages:
                raise ToolError(f"page {page} not found", code="page_out_of_range", retryable=False)
            pt = pages[0]
            if pt.visible_char_count < pdf_mod.NEEDS_OCR_CHAR_THRESHOLD:
                eng = (
                    ocr_mod.get_engine(ctx.config.ocr_engine)
                    if ctx.config.ocr_engine != "none"
                    else None
                )
                if eng is None:
                    raise ToolError(
                        "page has no text layer and no OCR engine is available",
                        code="ocr_unavailable",
                        retryable=False,
                    )
                rendered = pdf_mod.render_page(data, page, dpi=ctx.config.ocr_dpi)
                pt = ocr_mod.page_text_from_ocr(
                    eng.recognize(rendered.png), page=page, scale=72.0 / rendered.dpi
                )
            result = layout_mod.analyse(pt)
            return {"coordinate_system": "pdf_points_tl", **json.loads(result.model_dump_json())}
    src = load_image(ctx, ref)
    eng = ocr_mod.get_engine(ctx.config.ocr_engine) if ctx.config.ocr_engine != "none" else None
    if eng is None:
        raise ToolError(
            "no OCR engine available for image layout", code="ocr_unavailable", retryable=False
        )
    pt = ocr_mod.page_text_from_ocr(eng.recognize(src.data), page=1, scale=1.0)
    result = layout_mod.analyse(pt)
    return {
        "coordinate_system": "pixels",
        "artifact_id": src.artifact_id,
        "transform_chain": src.chain,
        **json.loads(result.model_dump_json()),
    }


__all__ = ["engines", "layout", "recognize"]
