"""Unwrap an envelope response into a schema-conformant result plus field metadata."""

from __future__ import annotations

from typing import Any

import jsonschema
from pydantic import BaseModel, Field

from hawser.models.common import JsonSchema
from hawser.models.run import FieldMeta, FieldStatus
from hawser.structured.pointers import pointer
from hawser.structured.schema import STATUS_VALUES, is_array, is_object, structural_union


class Anchor(BaseModel):
    pointer: str
    source_text: str
    page: int | None = None
    value: Any = None


class Unwrapped(BaseModel):
    result: Any = None
    field_meta: dict[str, FieldMeta] = Field(default_factory=dict)
    anchors: list[Anchor] = Field(default_factory=list)
    #: Places where the model's output did not follow the envelope. Not schema errors.
    parse_issues: list[str] = Field(default_factory=list)


def _all_unknown(node: Any) -> bool:
    """True when every leaf envelope under ``node`` has a non-found status (or none exist)."""
    if isinstance(node, dict):
        node_d: dict[str, Any] = node  # type: ignore[assignment]
        if "value" in node_d and ("found" in node_d or "status" in node_d):
            return _status_of(node_d) != "found"
        return all(_all_unknown(v) for v in node_d.values())
    if isinstance(node, list):
        return all(_all_unknown(v) for v in node)  # type: ignore[union-attr]
    return True


def _status_of(node: dict[str, Any]) -> str:
    """Envelope status: the ``found`` boolean, or a legacy ``status`` string if present."""
    if "status" in node and node["status"] in STATUS_VALUES:
        return str(node["status"])
    found = node.get("found", True)
    return "found" if found is True or found == "true" else "unknown"


def unwrap(output: Any, schema: JsonSchema) -> Unwrapped:
    """``schema`` is the caller's *inlined* schema (not the envelope)."""
    u = Unwrapped()

    def leaf(node: Any, path: list[str | int]) -> Any:
        ptr = pointer(path)
        if not isinstance(node, dict) or "value" not in node:
            u.parse_issues.append(f"{ptr or '/'}: expected an envelope object")
            u.field_meta[ptr] = FieldMeta(status=FieldStatus.unknown)
            return None
        node_d: dict[str, Any] = node  # type: ignore[assignment]
        status = _status_of(node_d)
        value = node_d.get("value")
        if status != "found":
            value = None  # the placeholder is discarded
        meta = FieldMeta(status=FieldStatus(status))
        u.field_meta[ptr] = meta
        src = node_d.get("source_text")
        if status == "found" and isinstance(src, str) and src.strip():
            page = node_d.get("page")
            u.anchors.append(
                Anchor(
                    pointer=ptr,
                    source_text=src,
                    page=int(page) if isinstance(page, int) and page >= 1 else None,
                    value=value,
                )
            )
        return value

    def walk(node_schema: JsonSchema, data: Any, path: list[str | int]) -> Any:  # noqa: PLR0911
        structural = structural_union(node_schema)
        if structural is not None:
            if data is None or _all_unknown(data):
                # Optional object/array that the model marked entirely unknown -> null.
                u.field_meta[pointer(path)] = FieldMeta(status=FieldStatus.unknown)
                return None
            return walk(structural, data, path)
        if is_object(node_schema):
            props: dict[str, Any] = node_schema.get("properties", {}) or {}
            if not isinstance(data, dict):
                u.parse_issues.append(f"{pointer(path) or '/'}: expected an object")
                return None
            data_d: dict[str, Any] = data  # type: ignore[assignment]
            return {k: walk(v, data_d.get(k), [*path, k]) for k, v in props.items()}
        if is_array(node_schema):
            items: JsonSchema = node_schema.get("items", {}) or {}
            if not isinstance(data, list):
                u.parse_issues.append(f"{pointer(path) or '/'}: expected an array")
                return [] if data is None else None
            data_l: list[Any] = data  # type: ignore[assignment]
            return [walk(items, item, [*path, i]) for i, item in enumerate(data_l)]
        return leaf(data, path)

    u.result = walk(schema, output, [])
    return u


def validate_against(schema: JsonSchema, instance: Any) -> list[tuple[str, str]]:
    """Return ``[(json_pointer, message)]`` for every schema violation."""
    validator = jsonschema.Draft202012Validator(schema)
    out: list[tuple[str, str]] = []
    for err in sorted(validator.iter_errors(instance), key=lambda e: list(e.absolute_path)):
        ptr = pointer([p for p in err.absolute_path])  # type: ignore[misc]
        out.append((ptr, err.message))
    return out


__all__ = ["Anchor", "Unwrapped", "unwrap", "validate_against"]
