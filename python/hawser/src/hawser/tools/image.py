"""Image tools (blueprint §6, plan WP7.3).

Every tool takes an image *reference*: an image asset id (``asset_…``) or a PNG/JPEG artifact
id (``art_…``) produced by another image tool. Tools that move pixels (``image.crop``,
``image.rotate``, ``image.resize``, ``image.deskew``, ``image.normalise``) record the transform
chain on the artifact they produce; tools that locate things (``image.detect``,
``image.ground``, ``image.ui_parse``) return pixel boxes on the input image *and* evidence
whose ``transform_chain`` reaches back to the original asset, so the runtime can draw it there.

Vision tools call any VLM through the gateway with a strict schema; boxes are requested on a
0-1000 grid (what current VLMs produce most reliably) and validated before conversion.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from typing import Any

import httpx

from hawser.errors import PolicyViolation, ProviderError, ToolError
from hawser.gateway import ImagePart, Message, ModelRequest, ModelResponse, TextPart
from hawser.gateway.select import Requirements, candidates
from hawser.ids import new_id
from hawser.models import (
    ArtifactKind,
    BBox,
    EventType,
    ImageRegionEvidence,
    Modality,
    ModelCallUsage,
    Point,
    ProducerRef,
    SideEffect,
    StepKind,
)
from hawser.models.common import JsonSchema, Transform
from hawser.models.tool import CostEstimate
from hawser.recording import canonical_json
from hawser.runtime.budget import check_before_model_call, estimate_call
from hawser.runtime.context import RunContext
from hawser.runtime.steps import StepHandle
from hawser.tools.registry import registry, tool
from hawser_media import ops
from hawser_media.errors import MediaError

GRID = 1000.0
VISION_SYSTEM = (
    "You are a careful vision analyst. Answer only from what is visible in the image. "
    "Boxes use a 0-1000 grid on both axes: [x0, y0, x1, y1] with the origin at the top-left, "
    "x0 < x1 and y0 < y1. Never invent objects that are not clearly visible."
)


# -- image references ----------------------------------------------------------------------


class ImageRef:
    def __init__(
        self,
        data: bytes,
        media_type: str,
        *,
        asset_id: str,
        artifact_id: str,
        chain: list[dict[str, Any]],
        width: int,
        height: int,
    ) -> None:
        self.data = data
        self.media_type = media_type
        self.asset_id = asset_id
        self.artifact_id = artifact_id
        self.chain = chain
        self.width = width
        self.height = height


def _size(data: bytes) -> tuple[int, int]:
    from hawser_media.image import probe  # noqa: PLC0415

    info = probe(data)
    return info.width, info.height


def load_image(ctx: RunContext, ref: str) -> ImageRef:
    """Resolve an asset or artifact id to bytes plus its lineage chain back to the asset."""
    if ref.startswith("asset_"):
        asset = ctx.meta.get_asset(ref)
        if asset is None or asset.tenant_id != ctx.tenant_id:
            raise ToolError(f"unknown asset {ref}", code="asset_not_found", retryable=False)
        if asset.media_class.value != "image":
            raise ToolError(
                f"{ref} is a {asset.media_class.value}; render a page first",
                code="not_an_image",
                retryable=False,
            )
        data = ctx.artifacts.get_bytes(asset.original_artifact_id)
        w, h = _size(data)
        return ImageRef(
            data,
            asset.media_type,
            asset_id=asset.id,
            artifact_id=asset.original_artifact_id,
            chain=[],
            width=w,
            height=h,
        )
    artifact = ctx.meta.get_artifact(ref)
    if artifact is None or artifact.tenant_id != ctx.tenant_id:
        raise ToolError(f"unknown artifact {ref}", code="artifact_not_found", retryable=False)
    if not artifact.meta.media_type.startswith("image/"):
        raise ToolError(f"{ref} is not an image", code="not_an_image", retryable=False)
    # Walk the lineage: concatenate each derived artifact's recorded chain.
    chain: list[dict[str, Any]] = []
    asset_id: str | None = None
    cur = artifact
    hops = 0
    while cur is not None and hops < 32:
        raw = cur.meta.media.get("transform_chain")
        if isinstance(raw, str):
            # Derived artifacts store their full chain from the original asset.
            chain = json.loads(raw) + chain
            aid = cur.meta.media.get("asset_id")
            if isinstance(aid, str):
                asset_id = aid
            break
        if cur.meta.transform is not None and cur.meta.kind == ArtifactKind.rendered_page:
            chain = [json.loads(cur.meta.transform.model_dump_json()), *chain]
        aid = cur.meta.media.get("asset_id")
        if isinstance(aid, str):
            asset_id = aid
        if cur.meta.kind == ArtifactKind.original or not cur.meta.parents:
            break
        cur = ctx.meta.get_artifact(cur.meta.parents[0])
        hops += 1
    data = ctx.artifacts.get_bytes(artifact.id)
    w, h = _size(data)
    return ImageRef(
        data,
        artifact.meta.media_type,
        asset_id=asset_id or ctx.request.inputs[0].__dict__.get("asset_id", "")  # type: ignore[arg-type]
        if ctx.request.inputs
        else "",
        artifact_id=artifact.id,
        chain=chain,
        width=w,
        height=h,
    )


def _store_derived(
    ctx: RunContext,
    src: ImageRef,
    result: ops.OpResult,
    *,
    kind: ArtifactKind,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    chain = src.chain + result.chain
    media: dict[str, int | float | str | bool] = {
        "width": result.width,
        "height": result.height,
        "transform_chain": json.dumps(chain),
    }
    if src.asset_id:
        media["asset_id"] = src.asset_id
    for k, v in (extra or {}).items():
        if isinstance(v, int | float | str | bool):
            media[k] = v
    art = ctx.artifacts.put(
        result.png,
        kind=kind,
        media_type="image/png",
        parents=[src.artifact_id],
        producer=ProducerRef(kind="tool", name="image.ops", version="1"),
        transform=_as_transform(result.chain[-1]) if result.chain else None,
        media=media,
        run_id=ctx.run_id,
    )
    ctx.artifact_refs.append(art.ref())
    return {
        "artifact_id": art.id,
        "width": result.width,
        "height": result.height,
        "transform_chain": chain,
        **{k: v for k, v in result.info.items()},
    }


def _as_transform(t: dict[str, Any]) -> Transform:
    from pydantic import TypeAdapter  # noqa: PLC0415

    return TypeAdapter(Transform).validate_python(t)


# -- ops ----------------------------------------------------------------------------------


@tool(
    "image.normalise",
    description="Upright (EXIF) and size-cap an image; records the transform chain.",
    modalities=["image"],
    side_effect=SideEffect.local_artifact,
)
def normalise(ctx: RunContext, ref: str, max_side: int = 2000) -> dict[str, Any]:
    src = load_image(ctx, ref)
    return _store_derived(
        ctx, src, ops.normalise(src.data, max_side=max_side), kind=ArtifactKind.normalised
    )


@tool(
    "image.deskew",
    description="Straighten skewed text (projection profile); records rotate + pad.",
    modalities=["image"],
    side_effect=SideEffect.local_artifact,
)
def deskew(ctx: RunContext, ref: str, max_degrees: float = 15.0) -> dict[str, Any]:
    src = load_image(ctx, ref)
    return _store_derived(
        ctx, src, ops.deskew(src.data, max_degrees=max_degrees), kind=ArtifactKind.normalised
    )


@tool(
    "image.rotate",
    description="Rotate counter-clockwise by degrees with an expanded canvas.",
    modalities=["image"],
    side_effect=SideEffect.local_artifact,
)
def rotate(ctx: RunContext, ref: str, degrees: float) -> dict[str, Any]:
    src = load_image(ctx, ref)
    return _store_derived(ctx, src, ops.rotate(src.data, degrees), kind=ArtifactKind.normalised)


@tool(
    "image.crop",
    description="Crop a region (pixels of the input image).",
    modalities=["image"],
    side_effect=SideEffect.local_artifact,
)
def crop(  # noqa: PLR0917 - a box is four numbers
    ctx: RunContext, ref: str, x: float, y: float, w: float, h: float
) -> dict[str, Any]:
    src = load_image(ctx, ref)
    try:
        return _store_derived(ctx, src, ops.crop(src.data, x, y, w, h), kind=ArtifactKind.crop)
    except MediaError as exc:
        raise ToolError(exc.message, code=exc.code, retryable=False) from exc


@tool(
    "image.resize",
    description="Resize by scale or to a width/height; records the scale.",
    modalities=["image"],
    side_effect=SideEffect.local_artifact,
)
def resize(
    ctx: RunContext,
    ref: str,
    width: int | None = None,
    height: int | None = None,
    scale: float | None = None,
) -> dict[str, Any]:
    src = load_image(ctx, ref)
    try:
        return _store_derived(
            ctx,
            src,
            ops.resize(src.data, width=width, height=height, scale=scale),
            kind=ArtifactKind.normalised,
        )
    except MediaError as exc:
        raise ToolError(exc.message, code=exc.code, retryable=False) from exc


@tool(
    "image.redact",
    description="Fill regions (pixel boxes [x0,y0,x1,y1]) on the image.",
    modalities=["image"],
    side_effect=SideEffect.local_artifact,
)
def redact(ctx: RunContext, ref: str, boxes: list[list[float]]) -> dict[str, Any]:
    src = load_image(ctx, ref)
    parsed = [(float(b[0]), float(b[1]), float(b[2]), float(b[3])) for b in boxes if len(b) == 4]
    return _store_derived(
        ctx,
        src,
        ops.redact(src.data, parsed),
        kind=ArtifactKind.redacted,
        extra={"regions": len(parsed)},
    )


@tool(
    "image.annotate",
    description="Draw labelled boxes (evidence overlays) into a new image.",
    modalities=["image"],
    side_effect=SideEffect.local_artifact,
)
def annotate(ctx: RunContext, ref: str, boxes: list[dict[str, Any]]) -> dict[str, Any]:
    src = load_image(ctx, ref)
    parsed: list[tuple[float, float, float, float, str | None]] = []
    for b in boxes:
        box = b.get("box") or b.get("bbox")
        if isinstance(box, list) and len(box) == 4:
            parsed.append(
                (
                    float(box[0]),
                    float(box[1]),
                    float(box[2]),
                    float(box[3]),
                    str(b["label"]) if b.get("label") else None,
                )
            )
    return _store_derived(
        ctx,
        src,
        ops.annotate(src.data, parsed),
        kind=ArtifactKind.annotation,
        extra={"regions": len(parsed)},
    )


# -- VLM calls ------------------------------------------------------------------------------


def _vision_model(ctx: RunContext) -> tuple[str, list[str]]:
    sel = candidates(
        ctx.gateway.registry,
        Requirements(modalities={Modality.text, Modality.image}, structured_output=True),
        ctx.request.policy,
        ctx.request.models,
        default_model=ctx.config.default_model,
    )
    return sel.candidates[0], sel.candidates


def vlm(
    ctx: RunContext, name: str, src: ImageRef, instruction: str, schema: JsonSchema
) -> dict[str, Any]:
    """One checkpointed, budgeted, recorded structured VLM call."""
    model, order = _vision_model(ctx)
    spec = ctx.gateway.registry.get(model)
    request = ModelRequest(
        model=spec.id,
        system=VISION_SYSTEM,
        messages=[
            Message(
                role="user",
                parts=[
                    ImagePart(media_type=src.media_type, data=src.data, label="image"),
                    TextPart(text=instruction),
                ],
            )
        ],
        output_schema=schema,
        max_output_tokens=4000,
        effort=ctx.config.default_effort,
    )
    key = hashlib.sha256(
        canonical_json(
            {"tool": name, "artifact": src.artifact_id, "instruction": instruction}
        ).encode()
    ).hexdigest()[:12]

    def _call(h: StepHandle) -> ModelResponse:
        check_before_model_call(
            ctx.usage,
            ctx.request.policy,
            estimate_call(request, spec),
            repair_attempts=ctx.repair_attempts,
        )
        resp = ctx.gateway.complete_any(request, order, policy=ctx.request.policy)
        h.record_model_call(resp.usage)
        ctx.emitter.emit(
            EventType.model_called,
            {
                "prefix": name,
                "model": f"{resp.usage.provider}/{resp.usage.model}",
                "replayed": resp.usage.replayed,
                "input_tokens": resp.usage.input_tokens,
                "output_tokens": resp.usage.output_tokens,
                "cost_usd": resp.usage.cost_usd,
                "latency_ms": resp.usage.latency_ms,
            },
            step_id=h.step_id,
        )
        return resp

    resp = ctx.steps.run(
        f"{name}:{key}", f"{name}", StepKind.model, _call, output_type=ModelResponse
    )
    if not isinstance(resp.parsed, dict):
        raise ToolError(
            f"{name}: the model returned no JSON ({resp.parse_error or 'empty'})",
            code="vlm_output_invalid",
        )
    return resp.parsed


def _grid_box(raw: Any, src: ImageRef) -> BBox | None:
    """Validate a 0-1000 grid box and convert it to pixels of ``src``; None when malformed."""
    if not isinstance(raw, list) or len(raw) != 4:
        return None
    try:
        x0, y0, x1, y1 = (float(v) for v in raw)
    except (TypeError, ValueError):
        return None
    if not (0 <= x0 < x1 <= GRID and 0 <= y0 < y1 <= GRID):
        return None
    return BBox(
        x0=round(x0 / GRID * src.width, 1),
        y0=round(y0 / GRID * src.height, 1),
        x1=round(x1 / GRID * src.width, 1),
        y1=round(y1 / GRID * src.height, 1),
    )


def _region_evidence(
    ctx: RunContext,
    src: ImageRef,
    bbox: BBox,
    *,
    label: str,
    tool_name: str,
    confidence: float | None,
) -> ImageRegionEvidence:
    return ImageRegionEvidence(
        id=new_id("ev"),
        asset_id=src.asset_id,
        artifact_id=src.artifact_id,
        producer=ProducerRef(kind="tool", name=tool_name, version="1"),
        confidence=confidence,
        transform_chain=[_as_transform(t) for t in src.chain],
        bbox=bbox,
        label=label,
    )


def _strict(props: dict[str, Any]) -> JsonSchema:
    return {
        "type": "object",
        "properties": props,
        "required": list(props),
        "additionalProperties": False,
    }


_BOX = {"type": "array", "items": {"type": "number"}, "minItems": 4, "maxItems": 4}


@tool(
    "image.caption",
    description="A one-paragraph description of the image (VLM).",
    modalities=["image"],
)
def caption(ctx: RunContext, ref: str) -> dict[str, Any]:
    src = load_image(ctx, ref)
    out = vlm(
        ctx,
        "image.caption",
        src,
        "Describe this image in one paragraph.",
        _strict({"caption": {"type": "string"}}),
    )
    return {"caption": str(out.get("caption", ""))}


@tool("image.vqa", description="Answer a question about the image (VLM).", modalities=["image"])
def vqa(ctx: RunContext, ref: str, question: str) -> dict[str, Any]:
    src = load_image(ctx, ref)
    out = vlm(
        ctx,
        "image.vqa",
        src,
        f"Question: {question}\nAnswer briefly and say how confident you are (0-1).",
        _strict({"answer": {"type": "string"}, "confidence": {"type": "number"}}),
    )
    return {
        "answer": str(out.get("answer", "")),
        "confidence": max(0.0, min(1.0, float(out.get("confidence", 0.0) or 0.0))),
    }


@tool(
    "image.detect",
    description="Detect objects (optionally only the given labels); boxes in pixels with evidence.",
    modalities=["image"],
    produces_evidence=True,
)
def detect(
    ctx: RunContext, ref: str, labels: list[str] | None = None, max_items: int = 50
) -> dict[str, Any]:
    src = load_image(ctx, ref)
    ask = (
        "Detect every clearly visible object"
        + (f" of these kinds: {', '.join(labels)}" if labels else "")
        + f". Return at most {max_items} items."
    )
    schema = _strict(
        {
            "detections": {
                "type": "array",
                "items": _strict(
                    {"label": {"type": "string"}, "box": _BOX, "confidence": {"type": "number"}}
                ),
            }
        }
    )
    out = vlm(ctx, "image.detect", src, ask, schema)
    items: list[dict[str, Any]] = []
    evidence: list[ImageRegionEvidence] = []
    dropped = 0
    for d in list(out.get("detections", []))[:max_items]:
        if not isinstance(d, dict):
            continue
        box = _grid_box(d.get("box"), src)
        if box is None:
            dropped += 1
            continue
        conf = max(0.0, min(1.0, float(d.get("confidence", 0.0) or 0.0)))
        ev = _region_evidence(
            ctx, src, box, label=str(d.get("label", "")), tool_name="image.detect", confidence=conf
        )
        evidence.append(ev)
        items.append(
            {
                "label": str(d.get("label", "")),
                "box": [box.x0, box.y0, box.x1, box.y1],
                "confidence": conf,
                "evidence_id": ev.id,
            }
        )
    return {
        "detections": items,
        "dropped": dropped,
        "evidence": [json.loads(e.model_dump_json()) for e in evidence],
    }


@tool(
    "image.ground",
    description="Locate a phrase: boxes and centre points in pixels, with evidence.",
    modalities=["image"],
    produces_evidence=True,
)
def ground(ctx: RunContext, ref: str, phrase: str) -> dict[str, Any]:
    src = load_image(ctx, ref)
    schema = _strict(
        {
            "matches": {
                "type": "array",
                "items": _strict({"box": _BOX, "confidence": {"type": "number"}}),
            }
        }
    )
    out = vlm(
        ctx,
        "image.ground",
        src,
        f"Find every region matching: {phrase!r}. Return an empty list if it is not visible.",
        schema,
    )
    matches: list[dict[str, Any]] = []
    evidence: list[ImageRegionEvidence] = []
    for m in out.get("matches", []):
        box = _grid_box(m.get("box") if isinstance(m, dict) else None, src)
        if box is None:
            continue
        conf = max(0.0, min(1.0, float(m.get("confidence", 0.0) or 0.0)))
        ev = _region_evidence(
            ctx, src, box, label=phrase, tool_name="image.ground", confidence=conf
        )
        ev.point = Point(x=(box.x0 + box.x1) / 2, y=(box.y0 + box.y1) / 2)
        evidence.append(ev)
        matches.append(
            {
                "box": [box.x0, box.y0, box.x1, box.y1],
                "point": [ev.point.x, ev.point.y],
                "confidence": conf,
                "evidence_id": ev.id,
            }
        )
    return {
        "phrase": phrase,
        "matches": matches,
        "evidence": [json.loads(e.model_dump_json()) for e in evidence],
    }


@tool(
    "image.classify",
    description="Pick one of the given labels for the image.",
    modalities=["image"],
)
def classify(ctx: RunContext, ref: str, labels: list[str]) -> dict[str, Any]:
    if not labels:
        raise ToolError(
            "classify needs at least one label", code="tool_input_invalid", retryable=False
        )
    src = load_image(ctx, ref)
    schema = _strict(
        {"label": {"type": "string", "enum": labels}, "confidence": {"type": "number"}}
    )
    out = vlm(
        ctx,
        "image.classify",
        src,
        f"Which single label fits best? Options: {', '.join(labels)}.",
        schema,
    )
    label = str(out.get("label", labels[0]))
    return {
        "label": label if label in labels else labels[0],
        "confidence": max(0.0, min(1.0, float(out.get("confidence", 0.0) or 0.0))),
    }


@tool("image.tag", description="Free-form tags describing the image.", modalities=["image"])
def tag(ctx: RunContext, ref: str, max_tags: int = 10) -> dict[str, Any]:
    src = load_image(ctx, ref)
    out = vlm(
        ctx,
        "image.tag",
        src,
        f"List up to {max_tags} short tags for this image.",
        _strict({"tags": {"type": "array", "items": {"type": "string"}}}),
    )
    tags = [str(t) for t in out.get("tags", [])][:max_tags]
    return {"tags": tags}


@tool(
    "image.ui_parse",
    description="Parse a screenshot into UI elements (type, text, box) with evidence.",
    modalities=["image"],
    produces_evidence=True,
)
def ui_parse(ctx: RunContext, ref: str, max_items: int = 80) -> dict[str, Any]:
    src = load_image(ctx, ref)
    schema = _strict(
        {
            "elements": {
                "type": "array",
                "items": _strict(
                    {
                        "type": {
                            "type": "string",
                            "enum": [
                                "button",
                                "link",
                                "input",
                                "text",
                                "image",
                                "icon",
                                "checkbox",
                                "menu",
                                "tab",
                                "other",
                            ],
                        },
                        "text": {"type": "string"},
                        "box": _BOX,
                    }
                ),
            }
        }
    )
    out = vlm(
        ctx,
        "image.ui_parse",
        src,
        f"List the interactive and textual elements of this screenshot (at most {max_items}).",
        schema,
    )
    elements: list[dict[str, Any]] = []
    evidence: list[ImageRegionEvidence] = []
    for el in list(out.get("elements", []))[:max_items]:
        box = _grid_box(el.get("box") if isinstance(el, dict) else None, src)
        if box is None:
            continue
        ev = _region_evidence(
            ctx,
            src,
            box,
            label=f"{el.get('type', 'other')}: {el.get('text', '')}",
            tool_name="image.ui_parse",
            confidence=None,
        )
        evidence.append(ev)
        elements.append(
            {
                "type": str(el.get("type", "other")),
                "text": str(el.get("text", "")),
                "box": [box.x0, box.y0, box.x1, box.y1],
                "evidence_id": ev.id,
            }
        )
    return {"elements": elements, "evidence": [json.loads(e.model_dump_json()) for e in evidence]}


# -- segmentation, embeddings ----------------------------------------------------------------


@tool(
    "image.segment",
    description="Masks for the given pixel boxes (box segmenter by default; SAM-family "
    "adapters are optional extras).",
    modalities=["image"],
    side_effect=SideEffect.local_artifact,
)
def segment(ctx: RunContext, ref: str, boxes: list[list[float]]) -> dict[str, Any]:
    src = load_image(ctx, ref)
    parsed = [(float(b[0]), float(b[1]), float(b[2]), float(b[3])) for b in boxes if len(b) == 4]
    mask = ops.mask_png(src.width, src.height, parsed)
    rle = ops.rle_encode(mask)
    art = ctx.artifacts.put(
        mask,
        kind=ArtifactKind.mask,
        media_type="image/png",
        parents=[src.artifact_id],
        producer=ProducerRef(kind="tool", name="image.segment", version="1"),
        media={
            "width": src.width,
            "height": src.height,
            "transform_chain": json.dumps(src.chain),
            "segmenter": "box",
        },
        run_id=ctx.run_id,
    )
    ctx.artifact_refs.append(art.ref())
    return {"mask_artifact_id": art.id, "rle": rle, "segmenter": "box", "regions": len(parsed)}


@tool(
    "image.embed",
    description="A deterministic perceptual embedding (near-duplicate similarity); "
    "CLIP/SigLIP adapters are optional extras.",
    modalities=["image"],
    side_effect=SideEffect.local_artifact,
)
def embed(ctx: RunContext, ref: str) -> dict[str, Any]:
    src = load_image(ctx, ref)
    vec = ops.perceptual_embedding(src.data)
    art = ctx.artifacts.put_json(
        {"model": "perceptual-16", "vector": vec},
        kind=ArtifactKind.embedding,
        parents=[src.artifact_id],
        run_id=ctx.run_id,
    )
    ctx.artifact_refs.append(art.ref())
    return {
        "embedding_artifact_id": art.id,
        "model": "perceptual-16",
        "dimensions": len(vec),
        "vector": vec,
    }


@tool(
    "image.similarity",
    description="Cosine similarity of two images' embeddings.",
    modalities=["image"],
)
def similarity(ctx: RunContext, ref_a: str, ref_b: str) -> dict[str, Any]:
    a = ops.perceptual_embedding(load_image(ctx, ref_a).data)
    b = ops.perceptual_embedding(load_image(ctx, ref_b).data)
    return {"score": round(ops.cosine(a, b), 4), "model": "perceptual-16"}


# -- generation (policy-gated) ----------------------------------------------------------------

_client: httpx.Client | None = None


def set_http_client(client: httpx.Client | None) -> None:
    global _client  # noqa: PLW0603
    _client = client


def _generation_allowed(ctx: RunContext) -> None:
    if ctx.request.policy.generation != "enabled":
        raise PolicyViolation(
            "image generation is disabled by policy (policy.generation)", code="generation_disabled"
        )


def _images_api(
    ctx: RunContext, path: str, body: dict[str, Any], *, files: dict[str, Any] | None = None
) -> dict[str, Any]:
    base = os.environ.get("HAWSER_IMAGE_API_BASE", "https://api.openai.com/v1").rstrip("/")
    key = os.environ.get("HAWSER_IMAGE_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not key:
        raise ProviderError(
            "no image API key (HAWSER_IMAGE_API_KEY or OPENAI_API_KEY)",
            code="missing_credentials",
            retryable=False,
        )
    client = _client or httpx.Client(timeout=120)
    headers = {"Authorization": f"Bearer {key}"}
    try:
        if files:
            res = client.post(f"{base}{path}", headers=headers, files=files, data=body)
        else:
            res = client.post(f"{base}{path}", headers=headers, json=body)
    except httpx.HTTPError as exc:
        raise ProviderError(
            f"image API request failed: {exc}", code="provider_unreachable"
        ) from exc
    if res.status_code >= 400:
        raise ProviderError(
            f"image API returned {res.status_code}: {res.text[:200]}",
            code="provider_error",
            retryable=res.status_code >= 500,
        )
    return res.json()


def _store_generated(
    ctx: RunContext, payload: dict[str, Any], *, prompt: str, parents: list[str], kind: str
) -> dict[str, Any]:
    items = payload.get("data") or []
    if not items or not isinstance(items[0], dict):
        raise ProviderError("image API returned no images", code="provider_error")
    first = items[0]
    if first.get("b64_json"):
        png = base64.b64decode(first["b64_json"])
    elif first.get("url"):
        client = _client or httpx.Client(timeout=120)
        png = client.get(str(first["url"])).content
    else:
        raise ProviderError("image API returned neither b64_json nor url", code="provider_error")
    art = ctx.artifacts.put(
        png,
        kind=ArtifactKind.generated,
        media_type="image/png",
        parents=parents,
        producer=ProducerRef(
            kind="model",
            name=str(payload.get("model") or os.environ.get("HAWSER_IMAGE_MODEL", "gpt-image-1")),
            version=None,
        ),
        media={"prompt": prompt[:500], "kind": kind, "evidence": False},
        run_id=ctx.run_id,
    )
    ctx.artifact_refs.append(art.ref())
    return {
        "artifact_id": art.id,
        "prompt": prompt,
        "kind": kind,
        "revised_prompt": first.get("revised_prompt"),
    }


@tool(
    "image.generate",
    description="Generate an image from a prompt (OpenAI-compatible images API). "
    "Needs policy.generation = enabled. Outputs carry no evidence claims.",
    modalities=["image"],
    side_effect=SideEffect.external_read,
)
def generate(ctx: RunContext, prompt: str, size: str = "1024x1024") -> dict[str, Any]:
    _generation_allowed(ctx)
    model = os.environ.get("HAWSER_IMAGE_MODEL", "gpt-image-1")
    payload = _images_api(
        ctx, "/images/generations", {"model": model, "prompt": prompt, "size": size, "n": 1}
    )
    payload.setdefault("model", model)
    return _store_generated(ctx, payload, prompt=prompt, parents=[], kind="generate")


@tool(
    "image.edit",
    description="Edit an image with a prompt (OpenAI-compatible images API). "
    "Needs policy.generation = enabled.",
    modalities=["image"],
    side_effect=SideEffect.external_read,
)
def edit(ctx: RunContext, ref: str, prompt: str, size: str = "1024x1024") -> dict[str, Any]:
    _generation_allowed(ctx)
    src = load_image(ctx, ref)
    model = os.environ.get("HAWSER_IMAGE_MODEL", "gpt-image-1")
    payload = _images_api(
        ctx,
        "/images/edits",
        {"model": model, "prompt": prompt, "size": size, "n": 1},
        files={"image": ("image.png", src.data, src.media_type)},
    )
    payload.setdefault("model", model)
    return _store_generated(ctx, payload, prompt=prompt, parents=[src.artifact_id], kind="edit")


for _name in ("image.generate", "image.edit"):
    registry.get(_name).spec.cost_estimate = CostEstimate(fixed_usd=0.04)

__all__ = ["ImageRef", "load_image", "set_http_client", "vlm"]

# Keep ModelCallUsage referenced for type-checkers reading evidence payloads.
_ = ModelCallUsage
