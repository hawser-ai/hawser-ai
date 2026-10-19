"""Compare two evaluation runs: metric deltas and per-case regressions."""

from __future__ import annotations

from typing import Any

from hawser_evals.report import HEADLINE, fmt_metric
from hawser_evals.runner import EvalRun

#: Metrics where a higher value is better; everything else lower-is-better.
HIGHER_IS_BETTER = {
    "field_accuracy",
    "doc_pass_rate",
    "status_accuracy",
    "accepted_rate",
    "unknown_precision",
    "unknown_recall",
    "evidence_coverage",
}


def compare(baseline: EvalRun, candidate: EvalRun) -> dict[str, Any]:
    metrics: dict[str, dict[str, float]] = {}
    for key in sorted(set(baseline.metrics) | set(candidate.metrics)):
        a, b = baseline.metrics.get(key, 0.0), candidate.metrics.get(key, 0.0)
        metrics[key] = {"baseline": a, "candidate": b, "delta": round(b - a, 6)}
    base_cases = {c.case_id: c for c in baseline.cases}
    cand_cases = {c.case_id: c for c in candidate.cases}
    common = sorted(set(base_cases) & set(cand_cases))
    regressions = [cid for cid in common if base_cases[cid].passed and not cand_cases[cid].passed]
    improvements = [cid for cid in common if not base_cases[cid].passed and cand_cases[cid].passed]
    status_changes = [
        {"case": cid, "from": base_cases[cid].status, "to": cand_cases[cid].status}
        for cid in common
        if base_cases[cid].status != cand_cases[cid].status
    ]
    return {
        "baseline": {
            "id": baseline.id,
            "model": baseline.config.model,
            "skill_version": baseline.config.skill_version,
        },
        "candidate": {
            "id": candidate.id,
            "model": candidate.config.model,
            "skill_version": candidate.config.skill_version,
        },
        "metrics": metrics,
        "common_cases": len(common),
        "regressions": regressions,
        "improvements": improvements,
        "status_changes": status_changes,
    }


def render_compare_md(cmp: dict[str, Any]) -> str:
    b, c = cmp["baseline"], cmp["candidate"]
    lines = [
        f"# Compare {b['id']} → {c['id']}",
        "",
        f"- baseline: model `{b['model']}` skill {b['skill_version']}",
        f"- candidate: model `{c['model']}` skill {c['skill_version']}",
        f"- common cases: {cmp['common_cases']} · regressions: {len(cmp['regressions'])}"
        f" · improvements: {len(cmp['improvements'])}",
        "",
        "| metric | baseline | candidate | delta | |",
        "|---|---|---|---|---|",
    ]
    for key, label in HEADLINE:
        if key not in cmp["metrics"]:
            continue
        m = cmp["metrics"][key]
        delta = m["delta"]
        better = (delta > 0) == (key in HIGHER_IS_BETTER) if abs(delta) > 1e-9 else None
        flag = "" if better is None else ("✔" if better else "✘")
        lines.append(
            f"| {label} | {fmt_metric(key, m['baseline'])} | {fmt_metric(key, m['candidate'])} "
            f"| {delta:+.4f} | {flag} |"
        )
    if cmp["regressions"]:
        lines += ["", "## Regressions", ""] + [f"- {cid}" for cid in cmp["regressions"]]
    if cmp["improvements"]:
        lines += ["", "## Improvements", ""] + [f"- {cid}" for cid in cmp["improvements"]]
    if cmp["status_changes"]:
        lines += ["", "## Status changes", ""] + [
            f"- {s['case']}: {s['from']} → {s['to']}" for s in cmp["status_changes"]
        ]
    return "\n".join(lines) + "\n"


__all__ = ["HIGHER_IS_BETTER", "compare", "render_compare_md"]
