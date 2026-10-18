"""Binding resolution: ``$input.<name>``, ``$node.<id>[.path[0].more]``."""

from __future__ import annotations

import re
from typing import Any

_BINDING = re.compile(r"^\$(input|node)\.([A-Za-z0-9_\-]+)((?:\.[A-Za-z0-9_\-]+|\[\d+\])*)$")
_PATH_TOKEN = re.compile(r"\.([A-Za-z0-9_\-]+)|\[(\d+)\]")


class UnresolvedBinding(KeyError):
    pass


def parse_binding(value: Any) -> tuple[str, str, list[str | int]] | None:
    """``"$node.extract.items[0].sku"`` -> ``("node", "extract", ["items", 0, "sku"])``."""
    if not isinstance(value, str):
        return None
    m = _BINDING.match(value.strip())
    if not m:
        return None
    scope, name, rest = m.group(1), m.group(2), m.group(3)
    path: list[str | int] = []
    for key, idx in _PATH_TOKEN.findall(rest):
        path.append(int(idx) if idx else key)
    return scope, name, path


def references(value: Any) -> set[str]:
    """Node ids referenced anywhere in ``value`` (dicts/lists/strings)."""
    out: set[str] = set()
    if isinstance(value, dict):
        for v in value.values():  # type: ignore[union-attr]
            out |= references(v)
    elif isinstance(value, list):
        for v in value:  # type: ignore[union-attr]
            out |= references(v)
    else:
        parsed = parse_binding(value)
        if parsed and parsed[0] == "node":
            out.add(parsed[1])
    return out


def walk_path(data: Any, path: list[str | int]) -> Any:
    cur = data
    for tok in path:
        try:
            cur = cur[tok]  # type: ignore[index]
        except (KeyError, IndexError, TypeError):
            raise UnresolvedBinding(f"path {path} not found") from None
    return cur


def resolve(value: Any, *, inputs: dict[str, Any], outputs: dict[str, Any]) -> Any:
    """Replace bindings inside ``value`` (recursively) with their resolved values."""
    if isinstance(value, dict):
        return {k: resolve(v, inputs=inputs, outputs=outputs) for k, v in value.items()}  # type: ignore[union-attr]
    if isinstance(value, list):
        return [resolve(v, inputs=inputs, outputs=outputs) for v in value]  # type: ignore[union-attr]
    parsed = parse_binding(value)
    if parsed is None:
        return value
    scope, name, path = parsed
    table = inputs if scope == "input" else outputs
    if name not in table:
        raise UnresolvedBinding(f"${scope}.{name} is not available")
    return walk_path(table[name], path)


__all__ = ["UnresolvedBinding", "parse_binding", "references", "resolve", "walk_path"]
