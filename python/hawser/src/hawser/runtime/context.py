"""Per-run context object and the context builder (blueprint §13, v1).

The builder decides what the model sees: native text for text pages, rendered images for
pages that need OCR, image assets as images, text/JSON assets inline — all within a token
budget derived from the model's context window. Selections are recorded as an event.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from hawser.config import HawserConfig
from hawser.evidence import DocumentIndex
from hawser.gateway import Gateway, ImagePart, TextPart
from hawser.gateway.base import DocumentPart
from hawser.models import (
    ArtifactKind,
    ArtifactRef,
    Asset,
    MediaClass,
    ModelSpec,
    RunRequest,
    RunWarning,
    Usage,
)
from hawser.models.common import DpiTransform
from hawser.runtime.artifacts import ArtifactService
from hawser.runtime.events import EventEmitter
from hawser.runtime.steps import StepExecutor
from hawser.storage import MetadataStore, RunRecord
from hawser_media import pdf as pdf_mod

#: Rough token estimate: characters per token for English text.
CHARS_PER_TOKEN = 4
#: Image tokens per Anthropic's guidance (w*h/750), capped by their downscaling.
IMAGE_TOKEN_CAP = 1_600
#: Reserved for system prompt, schema and output.
RESERVED_TOKENS = 6_000


@dataclass
class RunContext:
    config: HawserConfig
    run: RunRecord
    request: RunRequest
    meta: MetadataStore
    artifacts: ArtifactService
    gateway: Gateway
    emitter: EventEmitter
    steps: StepExecutor
    usage: Usage
    artifact_refs: list[ArtifactRef] = field(default_factory=list)
    warnings: list[RunWarning] = field(default_factory=list)
    repair_attempts: int = 0
    #: The resolved skill when the run uses one (set by dispatch).
    skill: Any = None
    #: True when re-executing a paused run; status transitions already made are skipped.
    resuming: bool = False
    #: Resolves object-storage inputs (``s3://bucket/key``) when the deployment allows them.
    object_reader: Callable[[str], bytes] | None = None

    @property
    def run_id(self) -> str:
        return self.run.id

    @property
    def tenant_id(self) -> str:
        return self.run.tenant_id

    def warn(self, code: str, message: str, **details: Any) -> None:
        self.warnings.append(RunWarning(code=code, message=message, details=details))


@dataclass
class DocumentContext:
    asset: Asset
    index: DocumentIndex | None = None


@dataclass
class ContextSelection:
    parts: list[TextPart | ImagePart | DocumentPart]
    estimated_tokens: int
    budget_tokens: int
    included: list[dict[str, Any]]
    dropped: list[dict[str, Any]]


def estimate_text_tokens(text: str) -> int:
    return max(1, len(text) // CHARS_PER_TOKEN)


def estimate_image_tokens(width: int, height: int) -> int:
    return min(IMAGE_TOKEN_CAP, max(1, (width * height) // 750))


def _tag(name: str, page: int, total: int, *, rendered: bool = False) -> str:
    suffix = ' rendered="true"/>' if rendered else ">"
    return f'<document name="{name}" page="{page}" of="{total}"{suffix}'


def build_context(  # noqa: PLR0915
    ctx: RunContext,
    docs: list[DocumentContext],
    spec: ModelSpec,
    *,
    instruction: str,
    schema_json: str | None,
) -> ContextSelection:
    budget = int(spec.context_window * ctx.config.context_fraction) - RESERVED_TOKENS
    if schema_json:
        budget -= estimate_text_tokens(schema_json)
    budget = max(budget, 2_000)

    candidates: list[tuple[dict[str, Any], TextPart | ImagePart, int]] = []
    for doc in docs:
        asset = doc.asset
        name = asset.name or asset.id
        if asset.media_class == MediaClass.pdf and doc.index is not None:
            total = len(doc.index.pages)
            data: bytes | None = None
            for page in doc.index.pages:
                if page.needs_ocr:
                    if data is None:
                        data = ctx.artifacts.get_bytes(asset.original_artifact_id)
                    rendered = pdf_mod.render_page(data, page.page, dpi=ctx.config.render_dpi)
                    art = ctx.artifacts.put(
                        rendered.png,
                        kind=ArtifactKind.rendered_page,
                        media_type="image/png",
                        parents=[asset.original_artifact_id],
                        transform=DpiTransform(dpi=rendered.dpi),
                        media={
                            "page": page.page,
                            "width": rendered.width_px,
                            "height": rendered.height_px,
                        },
                        run_id=ctx.run_id,
                    )
                    ctx.artifact_refs.append(art.ref())
                    page.render_artifact_id = art.id
                    page.render_dpi = rendered.dpi
                    tokens = estimate_image_tokens(rendered.width_px, rendered.height_px)
                    label = f"{name}#page={page.page}"
                    header = TextPart(text=_tag(name, page.page, total, rendered=True))
                    candidates.append(
                        ({"asset": asset.id, "page": page.page, "kind": "image"}, header, 1)
                    )
                    candidates.append(
                        (
                            {"asset": asset.id, "page": page.page, "kind": "image"},
                            ImagePart(media_type="image/png", data=rendered.png, label=label),
                            tokens,
                        )
                    )
                else:
                    text = f"{_tag(name, page.page, total)}\n{page.text}\n</document>"
                    candidates.append(
                        (
                            {"asset": asset.id, "page": page.page, "kind": "text"},
                            TextPart(text=text),
                            estimate_text_tokens(text),
                        )
                    )
        elif asset.media_class == MediaClass.image:
            data = ctx.artifacts.get_bytes(asset.original_artifact_id)
            w = asset.manifest.image.width if asset.manifest.image else 1000
            h = asset.manifest.image.height if asset.manifest.image else 1000
            candidates.append(
                (
                    {"asset": asset.id, "kind": "image"},
                    TextPart(text=f'<document name="{name}" page="1" of="1" rendered="true"/>'),
                    1,
                )
            )
            candidates.append(
                (
                    {"asset": asset.id, "kind": "image"},
                    ImagePart(media_type=asset.media_type, data=data, label=name),
                    estimate_image_tokens(w, h),
                )
            )
        elif asset.media_class in (MediaClass.text, MediaClass.json):
            data = ctx.artifacts.get_bytes(asset.original_artifact_id)
            body = data.decode("utf-8", errors="replace")
            text = f'<document name="{name}" page="1" of="1">\n{body}\n</document>'
            candidates.append(
                (
                    {"asset": asset.id, "kind": "text"},
                    TextPart(text=text),
                    estimate_text_tokens(text),
                )
            )

    included: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    parts: list[TextPart | ImagePart | DocumentPart] = []
    used = 0
    for info, part, tokens in candidates:
        if used + tokens > budget:
            dropped.append({**info, "tokens": tokens})
            continue
        used += tokens
        included.append({**info, "tokens": tokens})
        parts.append(part)

    if dropped:
        ctx.warn(
            "context_truncated",
            f"{len(dropped)} input part(s) were dropped to fit the model's context budget",
            dropped=dropped,
        )

    head = TextPart(text=instruction)
    tail = TextPart(
        text="Use only the documents above. Return the requested structure now."
        if schema_json
        else "Answer using only the documents above, citing page numbers where relevant."
    )
    return ContextSelection(
        parts=[head, *parts, tail],
        estimated_tokens=used,
        budget_tokens=budget,
        included=included,
        dropped=dropped,
    )


def selection_payload(sel: ContextSelection) -> dict[str, Any]:
    return {
        "estimated_tokens": sel.estimated_tokens,
        "budget_tokens": sel.budget_tokens,
        "included": sel.included,
        "dropped": sel.dropped,
    }


def schema_to_json(schema: dict[str, Any] | None) -> str | None:
    return None if schema is None else json.dumps(schema, separators=(",", ":"))


__all__ = [
    "ContextSelection",
    "DocumentContext",
    "RunContext",
    "build_context",
    "estimate_image_tokens",
    "estimate_text_tokens",
    "schema_to_json",
    "selection_payload",
]
