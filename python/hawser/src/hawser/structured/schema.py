"""Schema normalisation and the extraction envelope.

The model never sees the caller's schema directly. Every leaf field is wrapped as::

    {"value": <leaf type, never null>, "source_text": str, "found": bool}

so the runtime can anchor evidence and represent *unknown* explicitly. Objects and arrays keep
their structure. Envelope objects are strict (all properties required, no additional
properties), which is what provider structured-output modes expect.

**No nullable or union types are introduced, and the envelope is deliberately small.**
Providers compile schemas into grammars: Anthropic caps union-typed parameters at 16 and
rejects grammars above a size threshold (a four-key envelope with a status enum and a page
number already exceeds it on a 15-field invoice). So ``source_text`` uses ``""`` for "none",
``found=false`` means unknown, and a not-found leaf carries a typed placeholder in ``value``
that the runtime discards. Nullability in the caller's schema (``Optional[X]``) is stripped
from the leaf type and restored by the unwrap step. Page numbers are not asked for; the
anchorer searches every page.
"""

from __future__ import annotations

import copy
from typing import Any

from pydantic import BaseModel

from hawser.models.common import JsonSchema

STATUS_VALUES = ["found", "unknown", "not_applicable"]
#: The envelope keys, in order. Kept to three: provider grammar compilers reject larger
#: per-leaf envelopes on real-world schemas (see docs/architecture/structured-output.md).
ENVELOPE_KEYS = ["value", "source_text", "found"]
_STRIP_KEYS = {"default", "title", "examples", "$comment"}

SOURCE_TEXT_DESCRIPTION = (
    "Exact text in the source supporting the value, copied verbatim. Empty string if none."
)
FOUND_DESCRIPTION = "true when the value is present in the documents; false if unknown."


def to_json_schema(schema: type[BaseModel] | JsonSchema) -> JsonSchema:
    if isinstance(schema, dict):
        return copy.deepcopy(schema)
    return schema.model_json_schema()


def inline_refs(schema: JsonSchema) -> JsonSchema:
    """Resolve local ``$ref``s (``#/$defs/...`` and ``#/definitions/...``) into an inline schema.

    Recursive schemas are rejected: they cannot be enveloped into a finite strict schema.
    """
    root = schema
    defs: dict[str, Any] = {}
    defs.update(root.get("$defs", {}))
    defs.update(root.get("definitions", {}))

    def resolve(node: Any, stack: tuple[str, ...]) -> Any:
        if isinstance(node, list):
            return [resolve(n, stack) for n in node]  # type: ignore[misc]
        if not isinstance(node, dict):
            return node
        node_d: dict[str, Any] = node  # type: ignore[assignment]
        if "$ref" in node_d:
            ref = str(node_d["$ref"])
            name = ref.rsplit("/", 1)[-1]
            if not (ref.startswith(("#/$defs/", "#/definitions/"))) or name not in defs:
                raise ValueError(f"unsupported $ref {ref!r}; only local $defs are supported")
            if name in stack:
                raise ValueError(f"recursive schema via {ref!r} is not supported")
            target = resolve(defs[name], (*stack, name))
            merged = dict(target)
            for k, v in node_d.items():
                if k != "$ref":
                    merged[k] = v
            return merged
        return {
            k: resolve(v, stack) for k, v in node_d.items() if k not in ("$defs", "definitions")
        }

    out = resolve(root, ())
    assert isinstance(out, dict)
    return out  # type: ignore[return-value]


def _types(node: JsonSchema) -> set[str]:  # noqa: PLR0911
    t = node.get("type")
    if isinstance(t, str):
        return {t}
    if isinstance(t, list):
        return {str(x) for x in t}  # type: ignore[union-attr]
    if "properties" in node:
        return {"object"}
    if "items" in node:
        return {"array"}
    if "enum" in node:
        return {"enum"}
    for key in ("anyOf", "oneOf"):
        if key in node:
            return {"union"}
    return set()


def is_object(node: JsonSchema) -> bool:
    return "object" in _types(node) or "properties" in node


def is_array(node: JsonSchema) -> bool:
    return "array" in _types(node) or "items" in node


def is_nullable(node: JsonSchema) -> bool:
    if "null" in _types(node):
        return True
    for key in ("anyOf", "oneOf"):
        for opt in node.get(key, []) or []:
            if isinstance(opt, dict) and _types(opt) == {"null"}:  # type: ignore[arg-type]
                return True
    return False


def structural_union(node: JsonSchema) -> JsonSchema | None:
    """``Optional[Object]`` arrives as ``anyOf: [X, null]``. Return X when X is object/array."""
    for key in ("anyOf", "oneOf"):
        options = [o for o in node.get(key, []) or [] if isinstance(o, dict)]
        non_null = [o for o in options if _types(o) != {"null"}]  # type: ignore[arg-type]
        if (
            len(non_null) == 1
            and len(options) == 2
            and (is_object(non_null[0]) or is_array(non_null[0]))  # type: ignore[arg-type]
        ):
            return non_null[0]  # type: ignore[return-value]
    return None


def strip_null(node: JsonSchema) -> JsonSchema:
    """Return the leaf schema without its null alternative (``Optional[X]`` -> ``X``)."""
    out = {k: v for k, v in node.items() if k not in _STRIP_KEYS}
    t = out.get("type")
    if isinstance(t, list):
        remaining = [str(x) for x in t if x != "null"]  # type: ignore[union-attr]
        if len(remaining) == 1:
            out["type"] = remaining[0]
        elif remaining:
            out["type"] = remaining
        else:
            out["type"] = "string"
        return out
    for key in ("anyOf", "oneOf"):
        if key in out:
            options = [
                o
                for o in out[key]
                if isinstance(o, dict) and _types(o) != {"null"}  # type: ignore[arg-type]
            ]
            if len(options) == 1:
                merged: JsonSchema = {k: v for k, v in out.items() if k != key}
                merged.update(strip_null(options[0]))  # type: ignore[arg-type]
                return merged
            if options:
                out[key] = options
            else:
                del out[key]
                out["type"] = "string"
    if not _types(out):
        out["type"] = "string"
    return out


def _leaf_envelope(leaf: JsonSchema) -> JsonSchema:
    desc = leaf.get("description")
    props: dict[str, Any] = {
        "value": strip_null(leaf),
        "source_text": {"type": "string", "description": SOURCE_TEXT_DESCRIPTION},
        "found": {"type": "boolean", "description": FOUND_DESCRIPTION},
    }
    env: JsonSchema = {
        "type": "object",
        "properties": props,
        "required": ENVELOPE_KEYS,
        "additionalProperties": False,
    }
    if desc:
        env["description"] = str(desc)
    return env


def envelope_schema(schema: JsonSchema) -> JsonSchema:
    """Transform an (inlined) schema into its envelope form."""

    def walk(node: JsonSchema) -> JsonSchema:
        structural = structural_union(node)
        if structural is not None:
            # Optional object/array: emit the structure itself; an absent object is expressed
            # by its leaves being "unknown". No union is introduced.
            inner = walk(structural)
            if node.get("description") and not inner.get("description"):
                inner["description"] = node["description"]
            return inner
        if is_object(node):
            props = node.get("properties", {}) or {}
            out: JsonSchema = {
                "type": "object",
                "properties": {k: walk(v) for k, v in props.items()},
                "required": list(props.keys()),
                "additionalProperties": False,
            }
            if node.get("description"):
                out["description"] = node["description"]
            return out
        if is_array(node):
            items = node.get("items", {}) or {}
            out = {"type": "array", "items": walk(items)}
            if node.get("description"):
                out["description"] = node["description"]
            return out
        return _leaf_envelope(node)

    return walk(schema)


def count_unions(schema: JsonSchema) -> int:
    """Number of union-typed parameters (type arrays or anyOf/oneOf) — providers cap this."""

    def walk(node: Any) -> int:
        if isinstance(node, list):
            return sum(walk(n) for n in node)  # type: ignore[misc]
        if not isinstance(node, dict):
            return 0
        node_d: dict[str, Any] = node  # type: ignore[assignment]
        n = 0
        t = node_d.get("type")
        if isinstance(t, list) and len(t) > 1:  # type: ignore[arg-type]
            n += 1
        if "anyOf" in node_d or "oneOf" in node_d:
            n += 1
        for k, v in node_d.items():
            if k == "properties" and isinstance(v, dict):
                n += walk(list(v.values()))  # type: ignore[union-attr]
            elif k in ("items", "anyOf", "oneOf"):
                n += walk(v)
        return n

    return walk(schema)


__all__ = [
    "ENVELOPE_KEYS",
    "FOUND_DESCRIPTION",
    "SOURCE_TEXT_DESCRIPTION",
    "STATUS_VALUES",
    "count_unions",
    "envelope_schema",
    "inline_refs",
    "is_array",
    "is_nullable",
    "is_object",
    "strip_null",
    "structural_union",
    "to_json_schema",
]
