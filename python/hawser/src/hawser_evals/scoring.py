"""Field-level scoring of a result against an expected document."""

from __future__ import annotations

import fnmatch
import math
import re
from typing import Any, Literal

from pydantic import BaseModel, Field

from hawser.evidence.anchor import normalise
from hawser.models import FieldStatus, RunResult
from hawser_evals.dataset import ScoringConfig

Outcome = Literal["correct", "mismatch", "missed", "hallucinated", "extra"]

_INDEX = re.compile(r"/\d+(?=/|$)")


def template(ptr: str) -> str:
    """``/line_items/3/sku`` -> ``/line_items/*/sku``."""
    return _INDEX.sub("/*", ptr)


class LeafScore(BaseModel):
    pointer: str
    template: str
    expected: Any = None
    actual: Any = None
    outcome: Outcome


class CaseScore(BaseModel):
    case_id: str
    run_id: str | None = None
    status: str
    expected_status: str | None = None
    status_ok: bool | None = None
    leaves: list[LeafScore] = Field(default_factory=list)
    correct: int = 0
    total: int = 0
    passed: bool = False
    evidence_coverage: float | None = None
    cost_usd: float = 0.0
    latency_ms: int = 0
    model_calls: int = 0
    repair_attempts: int = 0
    error: str | None = None
    tags: list[str] = Field(default_factory=list)

    @property
    def accuracy(self) -> float:
        return self.correct / self.total if self.total else 0.0


def _leaf_equal(e: Any, a: Any, tol: float) -> bool:
    if isinstance(e, bool) or isinstance(a, bool):
        return e == a
    if isinstance(e, int | float) and isinstance(a, int | float):
        return math.isclose(float(e), float(a), abs_tol=tol, rel_tol=1e-9)
    if isinstance(e, str) and isinstance(a, str):
        return normalise(e)[0] == normalise(a)[0]
    if isinstance(e, int | float) and isinstance(a, str):
        try:
            return math.isclose(float(e), float(a.replace(",", "")), abs_tol=tol)
        except ValueError:
            return False
    return e == a


def _is_leaf(v: Any) -> bool:
    return not isinstance(v, dict | list)


def _align_lists(
    expected: list[Any], actual: list[Any], key: str | None
) -> list[tuple[int | None, int | None]]:
    """Pairs of (expected index, actual index). Unmatched items appear with ``None``."""
    if not key:
        n = max(len(expected), len(actual))
        return [
            (i if i < len(expected) else None, i if i < len(actual) else None) for i in range(n)
        ]
    pairs: list[tuple[int | None, int | None]] = []
    used: set[int] = set()
    for i, e in enumerate(expected):
        ek = normalise(str((e or {}).get(key) if isinstance(e, dict) else e))[0]
        hit = None
        for j, a in enumerate(actual):
            if j in used:
                continue
            ak = normalise(str((a or {}).get(key) if isinstance(a, dict) else a))[0]
            if ek and ek == ak:
                hit = j
                break
        if hit is None and i < len(actual) and i not in used:  # same index when free
            hit = i
        if hit is not None:
            used.add(hit)
        pairs.append((i, hit))
    for j in range(len(actual)):
        if j not in used:
            pairs.append((None, j))
    return pairs


def compare_documents(expected: Any, actual: Any, cfg: ScoringConfig) -> list[LeafScore]:
    out: list[LeafScore] = []

    def ignored(ptr: str) -> bool:
        return any(fnmatch.fnmatchcase(ptr, pat) for pat in cfg.ignore)

    def leaf(ptr: str, e: Any, a: Any) -> None:
        if ignored(ptr):
            return
        if e is None and a is None:
            outcome: Outcome = "correct"
        elif e is not None and a is None:
            outcome = "missed"
        elif e is None and a is not None:
            outcome = "hallucinated"
        else:
            outcome = "correct" if _leaf_equal(e, a, cfg.numeric_tolerance) else "mismatch"
        out.append(
            LeafScore(pointer=ptr, template=template(ptr), expected=e, actual=a, outcome=outcome)
        )

    def walk(ptr: str, e: Any, a: Any) -> None:
        if isinstance(e, dict):
            a_d: dict[str, Any] = a if isinstance(a, dict) else {}
            for k, ev in e.items():
                walk(f"{ptr}/{k}", ev, a_d.get(k))
            return
        if isinstance(e, list):
            a_l: list[Any] = a if isinstance(a, list) else []
            key = next(
                (
                    k
                    for pat, k in cfg.list_keys.items()
                    if fnmatch.fnmatchcase(template(ptr), pat) or ptr == pat
                ),
                None,
            )
            for ei, ai in _align_lists(e, a_l, key):
                if ei is None:
                    if ai is not None and not ignored(f"{ptr}/{ai}"):
                        out.append(
                            LeafScore(
                                pointer=f"{ptr}/{ai}",
                                template=template(f"{ptr}/{ai}"),
                                actual=a_l[ai],
                                outcome="extra",
                            )
                        )
                    continue
                sub_a = a_l[ai] if ai is not None else None
                if _is_leaf(e[ei]):
                    leaf(f"{ptr}/{ei}", e[ei], sub_a)
                else:
                    walk(f"{ptr}/{ei}", e[ei], sub_a)
            return
        leaf(ptr, e, a)

    walk("", expected, actual)
    return out


def score_case(
    case_id: str,
    expected: Any,
    result: RunResult | None,
    cfg: ScoringConfig,
    *,
    expected_status: str | None = None,
    tags: list[str] | None = None,
    error: str | None = None,
) -> CaseScore:
    if result is None:
        return CaseScore(
            case_id=case_id,
            status="error",
            expected_status=expected_status,
            status_ok=False,
            error=error,
            tags=tags or [],
        )
    leaves = compare_documents(expected, result.result, cfg) if result.result is not None else []
    correct = sum(1 for leaf in leaves if leaf.outcome == "correct")
    found = [
        m
        for m in result.field_meta.values()
        if m.status in (FieldStatus.found, FieldStatus.derived)
    ]
    coverage = (sum(1 for m in found if m.evidence) / len(found)) if found else None
    t = result.usage.totals
    return CaseScore(
        case_id=case_id,
        run_id=result.run_id,
        status=result.status.value,
        expected_status=expected_status,
        status_ok=(result.status.value == expected_status) if expected_status else None,
        leaves=leaves,
        correct=correct,
        total=len(leaves),
        passed=bool(leaves) and correct == len(leaves),
        evidence_coverage=coverage,
        cost_usd=t.cost_usd,
        latency_ms=t.wall_time_ms,
        model_calls=t.model_calls,
        repair_attempts=t.repair_attempts,
        error=error or (result.error or {}).get("message") if result.error else None,
        tags=tags or [],
    )


__all__ = ["CaseScore", "LeafScore", "Outcome", "compare_documents", "score_case", "template"]
