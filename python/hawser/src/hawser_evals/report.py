"""Persist and render evaluation runs."""

from __future__ import annotations

import json
from pathlib import Path

from hawser_evals.runner import EvalRun

HEADLINE = [
    ("field_accuracy", "Field accuracy"),
    ("doc_pass_rate", "Document pass rate"),
    ("status_accuracy", "Status accuracy"),
    ("review_rate", "Review rate"),
    ("failed_rate", "Failed rate"),
    ("evidence_coverage", "Evidence coverage"),
    ("unknown_precision", "Unknown precision"),
    ("unknown_recall", "Unknown recall"),
    ("cost_mean_usd", "Cost / case (mean, USD)"),
    ("cost_p95_usd", "Cost / case (p95, USD)"),
    ("latency_p50_ms", "Latency p50 (ms)"),
    ("latency_p95_ms", "Latency p95 (ms)"),
    ("model_calls_mean", "Model calls / case"),
    ("repair_attempts_mean", "Repair attempts / case"),
]


def fmt_metric(key: str, value: float) -> str:
    if key.endswith("_rate") or key in (
        "field_accuracy",
        "evidence_coverage",
        "unknown_precision",
        "unknown_recall",
        "status_accuracy",
    ):
        return f"{value:.1%}"
    if key.endswith("_usd"):
        return f"${value:.4f}"
    if key.endswith("_ms"):
        return f"{value:.0f}"
    return f"{value:.2f}"


def render_md(run: EvalRun) -> str:
    c = run.config
    lines = [
        f"# Evaluation {run.id}",
        "",
        f"- dataset: `{c.dataset}` · skill: `{c.skill}`@{c.skill_version}"
        f" · mode: `{c.mode or 'skill default'}`",
        f"- model: `{c.model}` · registry `{c.registry_version}` · record mode `{c.record_mode}`",
        f"- hawser {c.hawser_version} · git `{c.git_sha or '?'}` · started {c.started_at}",
        f"- cases: {int(run.metrics.get('cases', 0))} (errors {int(run.metrics.get('errors', 0))})"
        + (f" · tags {c.tags}" if c.tags else ""),
        "",
        "## Metrics",
        "",
        "| metric | value |",
        "|---|---|",
    ]
    for key, label in HEADLINE:
        if key in run.metrics:
            lines.append(f"| {label} | {fmt_metric(key, run.metrics[key])} |")
    lines.append(
        f"| Total cost | {fmt_metric('cost_total_usd', run.metrics.get('cost_total_usd', 0.0))} |"
    )
    if run.clusters:
        lines += [
            "",
            "## Failure clusters",
            "",
            "| field | outcome | count | examples |",
            "|---|---|---|---|",
        ]
        for cl in run.clusters:
            lines.append(
                f"| `{cl['field']}` | {cl['outcome']} | {cl['count']} | {', '.join(cl['cases'])} |"
            )
    lines += [
        "",
        "## Cases",
        "",
        "| case | status | expected | fields | pass | evidence | cost | ms | run |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for case in run.cases:
        cov = f"{case.evidence_coverage:.0%}" if case.evidence_coverage is not None else "-"
        lines.append(
            f"| {case.case_id} | {case.status} | {case.expected_status or '-'} "
            f"| {case.correct}/{case.total} | {'yes' if case.passed else 'no'} | {cov} "
            f"| ${case.cost_usd:.4f} | {case.latency_ms} | {case.run_id or case.error or ''} |"
        )
    return "\n".join(lines) + "\n"


def write_report(run: EvalRun, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(run.model_dump_json(indent=2) + "\n", encoding="utf-8")
    (out_dir / "report.md").write_text(render_md(run), encoding="utf-8")
    return out_dir


def load_report(path: Path) -> EvalRun:
    p = Path(path)
    if p.is_dir():
        p = p / "report.json"
    return EvalRun.model_validate(json.loads(p.read_text(encoding="utf-8")))


__all__ = ["HEADLINE", "load_report", "render_md", "write_report"]
