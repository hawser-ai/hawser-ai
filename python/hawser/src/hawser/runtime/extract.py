"""The shared extraction core: context -> model -> unwrap -> anchor.

Used by direct mode, by the ``document.extract`` tool inside workflows, and by repair.
Every call is two checkpointed steps (``<prefix>:extract`` and ``<prefix>:anchor``).
"""

from __future__ import annotations

import json
from importlib import resources
from typing import Any

from pydantic import BaseModel, Field

from hawser.evidence import AnchorResult, anchor_all
from hawser.gateway import Message, ModelRequest, ModelResponse, TextPart
from hawser.models import EventType, Evidence, FieldMeta, ModelSpec, StepKind
from hawser.models.common import JsonSchema
from hawser.runtime.budget import check_before_model_call, estimate_call
from hawser.runtime.context import (
    DocumentContext,
    RunContext,
    build_context,
    schema_to_json,
    selection_payload,
)
from hawser.runtime.steps import StepHandle
from hawser.structured import envelope_schema, unwrap
from hawser.structured.unwrap import Anchor


class Extraction(BaseModel):
    """Serialisable outcome of one extraction (a step output)."""

    result: Any = None
    field_meta: dict[str, FieldMeta] = Field(default_factory=dict)
    evidence: list[Evidence] = Field(default_factory=list)
    parse_issues: list[str] = Field(default_factory=list)
    truncated: bool = False
    structured_fallback: bool = False
    model: str | None = None
    #: The quoted source texts per field, kept so evidence can be re-anchored (``reocr``).
    anchors: list[Anchor] = Field(default_factory=list)

    def evidence_map(self) -> dict[str, Evidence]:
        return {e.id: e for e in self.evidence}


def prompt(name: str) -> str:
    return resources.files("hawser.prompts").joinpath(name).read_text(encoding="utf-8")


def _corrections_part(corrections: list[str], previous: Any) -> TextPart:
    lines = "\n".join(f"- {c}" for c in corrections)
    prev = json.dumps(previous, indent=None, default=str)[:6000]
    return TextPart(
        text=(
            "A previous attempt produced the values below, which failed these checks:\n"
            f"{lines}\n\nPrevious values (for reference only):\n{prev}\n\n"
            "Re-read the documents and correct the failing fields. Keep every other field "
            "exactly as before. If a failing value genuinely is not in the documents, mark it "
            "found: false rather than guessing."
        )
    )


def extract_structured(
    ctx: RunContext,
    docs: list[DocumentContext],
    *,
    schema: JsonSchema | None,
    instruction: str,
    spec: ModelSpec,
    prefix: str = "extract",
    candidates: list[str] | None = None,
    system_prompt: str | None = None,
    corrections: list[str] | None = None,
    previous: Any = None,
) -> Extraction:
    """``schema`` must already be inlined (no ``$ref``)."""
    envelope = envelope_schema(schema) if schema is not None else None
    selection = build_context(
        ctx, docs, spec, instruction=instruction, schema_json=schema_to_json(envelope)
    )
    ctx.emitter.emit(EventType.context_selected, {"prefix": prefix, **selection_payload(selection)})
    parts = list(selection.parts)
    if corrections:
        parts.insert(len(parts) - 1, _corrections_part(corrections, previous))
    system = system_prompt or prompt("direct_extract.md" if envelope else "direct_answer.md")
    request = ModelRequest(
        model=spec.id,
        system=system,
        messages=[Message(role="user", parts=parts)],
        output_schema=envelope,
        max_output_tokens=ctx.config.max_output_tokens,
        effort=ctx.config.default_effort,
    )

    order = candidates or [spec.id]

    def _call(h: StepHandle) -> ModelResponse:
        check_before_model_call(
            ctx.usage,
            ctx.request.policy,
            estimate_call(request, spec),
            repair_attempts=ctx.repair_attempts,
        )
        resp = ctx.gateway.complete_any(request, order, policy=ctx.request.policy)
        h.record_model_call(resp.usage)
        for failed in resp.fallback_from:
            ctx.emitter.emit(
                EventType.model_fallback,
                {
                    "prefix": prefix,
                    "from": failed,
                    "to": f"{resp.usage.provider}/{resp.usage.model}",
                },
                step_id=h.step_id,
            )
        ctx.emitter.emit(
            EventType.model_called,
            {
                "prefix": prefix,
                "model": f"{resp.usage.provider}/{resp.usage.model}",
                "replayed": resp.usage.replayed,
                "input_tokens": resp.usage.input_tokens,
                "output_tokens": resp.usage.output_tokens,
                "cost_usd": resp.usage.cost_usd,
                "latency_ms": resp.usage.latency_ms,
                "stop_reason": resp.stop_reason,
                "structured_fallback": resp.structured_fallback,
            },
            step_id=h.step_id,
        )
        return resp

    response = ctx.steps.run(
        f"{prefix}:model", f"{prefix}: model call", StepKind.model, _call, output_type=ModelResponse
    )
    if response.truncated:
        ctx.warn(
            "output_truncated",
            f"{prefix}: the model hit its output token limit; the result may be incomplete",
        )
    if response.structured_fallback:
        ctx.warn(
            "structured_output_fallback",
            f"{prefix}: the provider rejected the schema grammar; JSON was prompt-guided",
        )

    if schema is None:
        return Extraction(result=response.text, model=spec.id, truncated=response.truncated)

    if response.parsed is None:
        unwrapped = unwrap(None, schema)
        unwrapped.parse_issues.insert(
            0, f"model output was not valid JSON: {response.parse_error or 'empty'}"
        )
    else:
        unwrapped = unwrap(response.parsed, schema)

    indexes = [d.index for d in docs if d.index is not None]
    anchoring = ctx.steps.run(
        f"{prefix}:anchor",
        f"{prefix}: anchor evidence",
        StepKind.tool,
        lambda h: anchor_all(indexes, unwrapped.anchors, step_id=h.step_id),
        output_type=AnchorResult,
    )
    field_meta = unwrapped.field_meta
    for ptr, ev_id in anchoring.by_pointer.items():
        meta = field_meta.setdefault(ptr, FieldMeta())
        meta.evidence = [ev_id]
        meta.anchor_confidence = anchoring.confidence.get(ptr)
    ctx.emitter.emit(
        EventType.evidence_anchored,
        {
            "prefix": prefix,
            "anchored": len(anchoring.by_pointer),
            "unanchored": anchoring.unanchored,
        },
    )
    return Extraction(
        result=unwrapped.result,
        field_meta=field_meta,
        evidence=list(anchoring.evidence),
        parse_issues=unwrapped.parse_issues,
        truncated=response.truncated,
        structured_fallback=response.structured_fallback,
        model=spec.id,
        anchors=list(unwrapped.anchors),
    )


__all__ = ["Extraction", "extract_structured", "prompt"]
