"""Sub-schemas and value merging for field-level repair (``ask_field``)."""

from __future__ import annotations

import copy
from typing import Any

from hawser.models.common import JsonSchema
from hawser.structured.pointers import pointer
from hawser.structured.schema import is_array, is_object, structural_union


def _tokens(ptr: str) -> list[str]:
    return [t.replace("~1", "/").replace("~0", "~") for t in ptr.split("/")[1:]] if ptr else []


def top_level_only(pointers: list[str]) -> bool:
    """True when every pointer addresses a direct property of the root object."""
    return all(len(_tokens(p)) == 1 for p in pointers)


def subschema(schema: JsonSchema, pointers: list[str]) -> JsonSchema:
    """Keep only the top-level properties named by ``pointers``. Requires :func:`top_level_only`."""
    names = {_tokens(p)[0] for p in pointers}
    props = schema.get("properties", {}) or {}
    out = copy.deepcopy(schema)
    out["properties"] = {k: v for k, v in props.items() if k in names}
    if "required" in out:
        out["required"] = [r for r in out["required"] if r in names]
    return out


def merge_values(base: Any, patch: Any, pointers: list[str]) -> Any:
    """Copy the values at ``pointers`` from ``patch`` into a deep copy of ``base``."""
    out = copy.deepcopy(base)
    for ptr in pointers:
        toks = _tokens(ptr)
        src: Any = patch
        dst: Any = out
        ok = True
        for t in toks[:-1]:
            key: Any = int(t) if isinstance(src, list) else t
            try:
                src = src[key]
                dst = dst[key]
            except (KeyError, IndexError, TypeError):
                ok = False
                break
        if not ok or not toks:
            continue
        last: Any = int(toks[-1]) if isinstance(dst, list) else toks[-1]
        try:
            value = src[last]
        except (KeyError, IndexError, TypeError):
            continue
        if isinstance(dst, dict) or (
            isinstance(dst, list) and isinstance(last, int) and last < len(dst)
        ):
            dst[last] = value
    return out


def leaf_pointers(schema: JsonSchema, data: Any) -> list[str]:
    """Every leaf pointer present in ``data`` according to ``schema``."""
    out: list[str] = []

    def walk(node: JsonSchema, value: Any, path: list[str | int]) -> None:
        structural = structural_union(node)
        if structural is not None:
            if value is not None:
                walk(structural, value, path)
            return
        if is_object(node):
            if not isinstance(value, dict):
                return
            for k, sub in (node.get("properties", {}) or {}).items():
                walk(sub, value.get(k), [*path, k])  # type: ignore[union-attr]
            return
        if is_array(node):
            if not isinstance(value, list):
                return
            items: JsonSchema = node.get("items", {}) or {}
            for i, v in enumerate(value):  # type: ignore[arg-type]
                walk(items, v, [*path, i])
            return
        out.append(pointer(path))

    walk(schema, data, [])
    return out


__all__ = ["leaf_pointers", "merge_values", "subschema", "top_level_only"]
