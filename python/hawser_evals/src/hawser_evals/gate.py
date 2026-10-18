"""CI quality gates: thresholds over eval metrics."""

from __future__ import annotations

from typing import Any

from hawser_evals.runner import EvalRun


def check_thresholds(run: EvalRun, thresholds: dict[str, Any]) -> list[str]:
    """``{"field_accuracy": {"min": 0.9}, "review_rate": {"max": 0.2}, "cost_mean_usd": 0.05}``.

    A bare number means ``min`` for higher-is-better style metrics is ambiguous, so bare numbers
    are treated as ``max`` for ``*_rate``/``*_usd``/``*_ms`` metrics and ``min`` otherwise.
    Returns the list of failures (empty = pass).
    """
    failures: list[str] = []
    for key, raw in thresholds.items():
        value = run.metrics.get(key)
        if value is None:
            failures.append(f"{key}: metric not present")
            continue
        spec = raw
        if isinstance(raw, int | float):
            is_max = key.endswith(("_rate", "_usd", "_ms")) and not key.startswith("accepted")
            spec = {"max": raw} if is_max else {"min": raw}
        if "min" in spec and value < float(spec["min"]):
            failures.append(f"{key}: {value:.4f} < min {float(spec['min']):.4f}")
        if "max" in spec and value > float(spec["max"]):
            failures.append(f"{key}: {value:.4f} > max {float(spec['max']):.4f}")
    return failures


__all__ = ["check_thresholds"]
