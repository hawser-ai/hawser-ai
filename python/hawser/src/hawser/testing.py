"""Test doubles that are also useful for examples: a static model adapter and an envelope builder.

``StaticAdapter`` returns a fixed parsed output for any request. It never talks to a provider,
so tests and the synthetic quickstart cassette are deterministic and free.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from hawser.gateway.base import ModelRequest, ModelResponse
from hawser.models import ModelCallUsage, ModelSpec
from hawser.models.common import JsonSchema
from hawser.structured.pointers import pointer
from hawser.structured.schema import is_array, is_object, strip_null, structural_union


def placeholder(leaf: JsonSchema) -> Any:
    """A typed placeholder for a leaf whose status is not ``found`` (discarded by unwrap)."""
    node = strip_null(leaf)
    if "enum" in node:
        return node["enum"][0]
    t = node.get("type")
    if isinstance(t, list):
        t = t[0]
    return {"integer": 0, "number": 0, "boolean": False, "array": [], "object": {}}.get(str(t), "")


def build_envelope(
    schema: JsonSchema,
    result: Any,
    *,
    source_texts: dict[str, str] | None = None,
    unknown: set[str] | None = None,
) -> Any:
    """Wrap ``result`` (conforming to the inlined ``schema``) into envelope form.

    ``source_texts`` maps JSON pointers to the verbatim text; leaves without an entry get an
    empty ``source_text``. Pointers in ``unknown`` (and ``None`` values) are emitted with
    ``found=false`` and a typed placeholder value.
    """
    source_texts = source_texts or {}
    unknown = unknown or set()

    def walk(node: JsonSchema, data: Any, path: list[str | int]) -> Any:
        structural = structural_union(node)
        if structural is not None:
            return walk(structural, data, path)
        if is_object(node):
            props: dict[str, Any] = node.get("properties", {}) or {}
            d: dict[str, Any] = data if isinstance(data, dict) else {}  # type: ignore[assignment]
            return {k: walk(v, d.get(k), [*path, k]) for k, v in props.items()}
        if is_array(node):
            items: JsonSchema = node.get("items", {}) or {}
            lst: list[Any] = data if isinstance(data, list) else []  # type: ignore[assignment]
            return [walk(items, v, [*path, i]) for i, v in enumerate(lst)]
        ptr = pointer(path)
        if ptr in unknown or data is None:
            return {"value": placeholder(node), "source_text": "", "found": False}
        return {"value": data, "source_text": source_texts.get(ptr, ""), "found": True}

    return walk(schema, result, [])


class StaticAdapter:
    """Returns ``output`` (or ``output_fn(request)``) for every call, with estimated usage."""

    provider = "anthropic"

    def __init__(
        self,
        output: Any = None,
        *,
        output_fn: Callable[[ModelRequest], Any] | None = None,
        text: str | None = None,
        fail_times: int = 0,
        failure: Exception | None = None,
    ) -> None:
        self.output = output
        self.output_fn = output_fn
        self.text = text
        self.fail_times = fail_times
        self.failure = failure
        self.calls: list[ModelRequest] = []

    def complete(self, spec: ModelSpec, request: ModelRequest) -> ModelResponse:
        self.calls.append(request)
        if self.fail_times > 0 and self.failure is not None:
            self.fail_times -= 1
            raise self.failure
        parsed = self.output_fn(request) if self.output_fn else self.output
        if request.output_schema is not None:
            text = json.dumps(parsed, sort_keys=True)
        else:
            text = (
                self.text if self.text is not None else (parsed if isinstance(parsed, str) else "")
            )
            parsed = None
        input_chars = sum(
            len(p.text) if p.kind == "text" else 4_000 for m in request.messages for p in m.parts
        ) + len(request.system or "")
        usage = ModelCallUsage(
            provider=self.provider,
            model=spec.model,
            input_tokens=max(1, input_chars // 4),
            output_tokens=max(1, len(text) // 4),
            cost_usd=0.0,
            latency_ms=1,
        )
        return ModelResponse(text=text, parsed=parsed, usage=usage, stop_reason="end_turn")


__all__ = ["StaticAdapter", "build_envelope", "placeholder"]
