"""A small, safe expression language for declarative validation rules.

Rules are Python expressions evaluated by an AST walker with a whitelist — no ``eval``.
Values come from the result document::

    approx(sum(line_items[:].line_total), subtotal)      # sums over an array
    approx(subtotal + tax, total, 0.01)                  # absolute tolerance
    invoice.currency == purchase_order.currency           # nested objects
    len(line_items) > 0 and all(line_items[:].quantity)   # list helpers

Supported: names (top-level keys), attribute access (dict keys; mapped over lists), ``[:]``
(the whole list) and integer indexes, arithmetic, comparisons, ``and``/``or``/``not``, and the
functions ``approx``, ``sum``, ``len``, ``min``, ``max``, ``abs``, ``round``, ``all``, ``any``,
``count``, ``present``.
"""

from __future__ import annotations

import ast
import math
import operator
from collections.abc import Callable
from typing import Any

DEFAULT_TOLERANCE = 0.005


class ExpressionError(ValueError):
    pass


def _approx(a: Any, b: Any, tol: float = DEFAULT_TOLERANCE) -> bool:
    if a is None or b is None:
        return False
    try:
        return math.isclose(float(a), float(b), abs_tol=tol, rel_tol=0.0)
    except (TypeError, ValueError):
        return False


def _num_list(x: Any) -> list[float]:
    if isinstance(x, list | tuple):
        return [float(v) for v in x if v is not None]  # type: ignore[union-attr]
    return [] if x is None else [float(x)]


def _sum(x: Any) -> float:
    return sum(_num_list(x))


def _len(x: Any) -> int:
    return len(x) if isinstance(x, list | tuple | str | dict) else 0


def _min(x: Any) -> float | None:
    vals = _num_list(x)
    return min(vals) if vals else None


def _max(x: Any) -> float | None:
    vals = _num_list(x)
    return max(vals) if vals else None


def _abs(x: Any) -> float | None:
    return abs(float(x)) if x is not None else None


def _round(x: Any, n: Any = 2) -> float | None:
    return round(float(x), int(n)) if x is not None else None


def _all(x: Any) -> bool:
    return all(bool(v) for v in x) if isinstance(x, list | tuple) else bool(x)


def _any(x: Any) -> bool:
    return any(bool(v) for v in x) if isinstance(x, list | tuple) else bool(x)


def _count(x: Any) -> int:
    return sum(1 for v in x if v is not None) if isinstance(x, list | tuple) else 0


def _present(x: Any) -> bool:
    return x is not None and x not in ("", [])


_FUNCS: dict[str, Callable[..., Any]] = {
    "approx": _approx,
    "sum": _sum,
    "len": _len,
    "min": _min,
    "max": _max,
    "abs": _abs,
    "round": _round,
    "all": _all,
    "any": _any,
    "count": _count,
    "present": _present,
}

_BIN: dict[type[ast.operator], Callable[[Any, Any], Any]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Mod: operator.mod,
}
_CMP: dict[type[ast.cmpop], Callable[[Any, Any], bool]] = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.In: lambda a, b: a in b,
    ast.NotIn: lambda a, b: a not in b,
}


def _getattr(value: Any, name: str) -> Any:
    if isinstance(value, dict):
        return value.get(name)  # type: ignore[union-attr]
    if isinstance(value, list):
        return [_getattr(v, name) for v in value]  # type: ignore[union-attr]
    return None


class _Evaluator(ast.NodeVisitor):
    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data

    def visit(self, node: ast.AST) -> Any:  # noqa: PLR0911, PLR0912
        if isinstance(node, ast.Expression):
            return self.visit(node.body)
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            if node.id in self.data:
                return self.data[node.id]
            if node.id in ("True", "False", "None"):
                return {"True": True, "False": False, "None": None}[node.id]
            return None
        if isinstance(node, ast.Attribute):
            return _getattr(self.visit(node.value), node.attr)
        if isinstance(node, ast.Subscript):
            base = self.visit(node.value)
            sl = node.slice
            if isinstance(sl, ast.Slice):
                if sl.lower is None and sl.upper is None and sl.step is None:
                    return list(base) if isinstance(base, list | tuple) else []
                raise ExpressionError("only the full slice [:] is supported")
            idx = self.visit(sl)
            try:
                return base[idx]
            except (KeyError, IndexError, TypeError):
                return None
        if isinstance(node, ast.BinOp):
            op = _BIN.get(type(node.op))
            if op is None:
                raise ExpressionError(f"operator {type(node.op).__name__} is not supported")
            left, right = self.visit(node.left), self.visit(node.right)
            if left is None or right is None:
                return None
            try:
                return op(float(left), float(right))
            except (TypeError, ValueError, ZeroDivisionError):
                return None
        if isinstance(node, ast.UnaryOp):
            v = self.visit(node.operand)
            if isinstance(node.op, ast.Not):
                return not v
            if isinstance(node.op, ast.USub):
                return -float(v) if v is not None else None
            raise ExpressionError("unsupported unary operator")
        if isinstance(node, ast.BoolOp):
            values = [self.visit(v) for v in node.values]
            return all(values) if isinstance(node.op, ast.And) else any(values)
        if isinstance(node, ast.Compare):
            left = self.visit(node.left)
            for op_node, comparator in zip(node.ops, node.comparators, strict=True):
                right = self.visit(comparator)
                fn = _CMP.get(type(op_node))
                if fn is None:
                    raise ExpressionError(f"comparison {type(op_node).__name__} is not supported")
                try:
                    if not fn(left, right):
                        return False
                except TypeError:
                    return False
                left = right
            return True
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCS:
                raise ExpressionError("only whitelisted functions may be called")
            args = [self.visit(a) for a in node.args]
            kwargs = {k.arg: self.visit(k.value) for k in node.keywords if k.arg}
            return _FUNCS[node.func.id](*args, **kwargs)
        if isinstance(node, ast.List | ast.Tuple):
            return [self.visit(e) for e in node.elts]
        raise ExpressionError(f"unsupported syntax: {type(node).__name__}")


def evaluate(expression: str, data: dict[str, Any]) -> Any:
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ExpressionError(f"invalid expression {expression!r}: {exc.msg}") from exc
    return _Evaluator(data).visit(tree)


def compile_check(expression: str) -> None:
    """Raise :class:`ExpressionError` if the expression uses unsupported syntax."""
    evaluate(expression, {})


__all__ = ["DEFAULT_TOLERANCE", "ExpressionError", "compile_check", "evaluate"]
