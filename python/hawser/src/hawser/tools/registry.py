"""Tool registry: name -> (ToolSpec, callable)."""

from __future__ import annotations

import inspect
import types
import typing
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from hawser.errors import PolicyViolation
from hawser.models import SideEffect, ToolSpec


@dataclass(frozen=True)
class RegisteredTool:
    spec: ToolSpec
    fn: Callable[..., Any]


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, RegisteredTool] = {}

    def register(self, spec: ToolSpec, fn: Callable[..., Any]) -> None:
        self._tools[spec.name] = RegisteredTool(spec, fn)

    def get(self, name: str, *, allowed: list[str] | None = None) -> RegisteredTool:
        base = name.split("@", 1)[0]
        if (
            allowed is not None
            and base not in allowed
            and not any(base.startswith(a.rstrip("*")) for a in allowed if a.endswith("*"))
        ):
            raise PolicyViolation(
                f"tool {base!r} is not permitted for this run", code="tool_not_allowed"
            )
        t = self._tools.get(base)
        if t is None:
            raise PolicyViolation(f"unknown tool {base!r}", code="tool_unknown")
        return t

    def names(self) -> list[str]:
        return sorted(self._tools)

    def specs(self) -> list[ToolSpec]:
        return [t.spec for _, t in sorted(self._tools.items())]


registry = ToolRegistry()

_JSON_TYPES: dict[Any, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
    bytes: "string",
}


def _json_type(hint: Any) -> dict[str, Any]:
    origin = typing.get_origin(hint)
    args = typing.get_args(hint)
    if origin in (types.UnionType, typing.Union):
        non_null = [a for a in args if a is not type(None)]
        if len(non_null) == 1:
            return _json_type(non_null[0])
        return {}
    if origin in (list, tuple):
        return {"type": "array"}
    if origin is dict:
        return {"type": "object"}
    if isinstance(hint, type) and issubclass(hint, BaseModel):
        return {"type": "object"}
    t = _JSON_TYPES.get(hint)
    return {"type": t} if t else {}


def schema_from_signature(fn: Callable[..., Any]) -> dict[str, Any]:
    """Derive an input schema from a tool function's parameters (names, basic types, required).

    ``ctx`` and ``action_id`` are runtime-supplied and excluded. The planner and the static plan
    validator rely on this, so a tool's real argument names are always known.
    """
    try:
        hints = typing.get_type_hints(fn)
    except Exception:
        hints = {}
    props: dict[str, Any] = {}
    required: list[str] = []
    for name, param in inspect.signature(fn).parameters.items():
        if name in ("ctx", "action_id") or param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
            continue
        props[name] = _json_type(hints.get(name))
        if param.default is inspect.Parameter.empty:
            required.append(name)
    schema: dict[str, Any] = {"type": "object", "properties": props, "additionalProperties": False}
    if required:
        schema["required"] = required
    return schema


def tool(
    name: str,
    *,
    version: str = "1.0.0",
    description: str,
    side_effect: SideEffect = SideEffect.none,
    permissions: list[str] | None = None,
    modalities: list[str] | None = None,
    produces_evidence: bool = False,
    input_schema: dict[str, Any] | None = None,
    output_schema: dict[str, Any] | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Register a Python callable as a tool. The callable receives a ``RunContext`` first."""

    def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
        spec = ToolSpec(
            name=name,
            version=version,
            description=description,
            input_schema=input_schema or schema_from_signature(fn),
            output_schema=output_schema or {"type": "object"},
            modalities=modalities or [],
            permissions=permissions or [],
            side_effect=side_effect,
            produces_evidence=produces_evidence,
        )
        registry.register(spec, fn)
        fn.hawser_tool_name = name  # type: ignore[attr-defined]
        return fn

    return deco


__all__ = ["RegisteredTool", "ToolRegistry", "registry", "schema_from_signature", "tool"]
