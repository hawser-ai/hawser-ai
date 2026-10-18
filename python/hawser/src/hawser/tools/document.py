"""Document tools: inspect, text index, page rendering. ``document.extract`` is the extraction
core exposed under a tool name so manifests and permissions can refer to it."""

from __future__ import annotations

from typing import Any

from hawser.errors import ToolError
from hawser.evidence import build_document_index
from hawser.models import ArtifactKind, SideEffect
from hawser.models.common import DpiTransform
from hawser.runtime.context import RunContext
from hawser.tools.registry import tool
from hawser_media import pdf as pdf_mod


def _asset(ctx: RunContext, asset_id: str) -> Any:
    asset = ctx.meta.get_asset(asset_id)
    if asset is None or asset.tenant_id != ctx.tenant_id:
        raise ToolError(f"unknown asset {asset_id}", code="asset_unknown", retryable=False)
    return asset


@tool(
    "document.inspect",
    description="Probe a document asset: pages, text layer, sizes, execution classes.",
    modalities=["document"],
    input_schema={
        "type": "object",
        "properties": {"asset_id": {"type": "string"}},
        "required": ["asset_id"],
    },
)
def inspect(ctx: RunContext, asset_id: str) -> dict[str, Any]:
    asset = _asset(ctx, asset_id)
    return {
        "asset_id": asset.id,
        "media_type": asset.media_type,
        "size_bytes": asset.size_bytes,
        "manifest": asset.manifest.model_dump(mode="json"),
    }


@tool(
    "document.extract_text",
    description="Native text with character boxes for every page of a PDF asset.",
    modalities=["document"],
    input_schema={
        "type": "object",
        "properties": {"asset_id": {"type": "string"}},
        "required": ["asset_id"],
    },
    produces_evidence=True,
)
def extract_text(ctx: RunContext, asset_id: str) -> dict[str, Any]:
    asset = _asset(ctx, asset_id)
    if asset.media_class.value != "pdf":
        raise ToolError(
            "document.extract_text needs a PDF asset", code="unsupported_media", retryable=False
        )
    data = ctx.artifacts.get_bytes(asset.original_artifact_id)
    index = build_document_index(asset.id, pdf_mod.extract_text(data), name=asset.name)
    return index.model_dump(mode="json")


@tool(
    "document.render_page",
    description="Render one page of a PDF asset to a PNG artifact.",
    modalities=["document"],
    side_effect=SideEffect.local_artifact,
    input_schema={
        "type": "object",
        "properties": {
            "asset_id": {"type": "string"},
            "page": {"type": "integer", "minimum": 1},
            "dpi": {"type": "number"},
        },
        "required": ["asset_id", "page"],
    },
)
def render_page(
    ctx: RunContext, asset_id: str, page: int, dpi: float | None = None
) -> dict[str, Any]:
    asset = _asset(ctx, asset_id)
    data = ctx.artifacts.get_bytes(asset.original_artifact_id)
    rendered = pdf_mod.render_page(data, page, dpi=dpi or ctx.config.render_dpi)
    art = ctx.artifacts.put(
        rendered.png,
        kind=ArtifactKind.rendered_page,
        media_type="image/png",
        parents=[asset.original_artifact_id],
        transform=DpiTransform(dpi=rendered.dpi),
        media={"page": page, "width": rendered.width_px, "height": rendered.height_px},
        run_id=ctx.run_id,
    )
    ctx.artifact_refs.append(art.ref())
    return {
        "artifact_id": art.id,
        "page": page,
        "width": rendered.width_px,
        "height": rendered.height_px,
        "dpi": rendered.dpi,
    }


@tool(
    "document.extract",
    description="Schema-guided extraction from documents with page/region evidence.",
    side_effect=SideEffect.external_read,
    modalities=["document", "image", "text"],
    produces_evidence=True,
)
def extract_spec() -> None:  # pragma: no cover - spec holder; WorkflowContext.extract does the work
    raise NotImplementedError("use WorkflowContext.extract")


__all__ = ["extract_spec", "extract_text", "inspect", "render_page"]
