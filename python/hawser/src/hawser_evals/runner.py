"""Run a dataset through a runtime and compute metrics, with a configuration snapshot."""

from __future__ import annotations

import statistics
import subprocess
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

import hawser
from hawser.client import Client
from hawser.clock import now
from hawser.ids import new_id
from hawser.models import PathInput, Policy, RunResult
from hawser.runtime.local import LocalRuntime
from hawser_evals.dataset import Dataset, EvalCase
from hawser_evals.scoring import CaseScore, score_case


class EvalConfig(BaseModel):
    dataset: str
    skill: str | None = None
    skill_version: str | None = None
    skill_hash: str | None = None
    mode: str | None = None
    model: str
    registry_version: str
    record_mode: str
    cassette_dir: str | None = None
    policy: dict[str, Any] = Field(default_factory=dict)
    hawser_version: str = hawser.__version__
    git_sha: str | None = None
    tags: list[str] = Field(default_factory=list)
    budget_usd: float | None = None
    started_at: str


class EvalRun(BaseModel):
    id: str
    config: EvalConfig
    cases: list[CaseScore] = Field(default_factory=list)
    metrics: dict[str, float] = Field(default_factory=dict)
    clusters: list[dict[str, Any]] = Field(default_factory=list)
    finished_at: str | None = None

    @property
    def passed_ids(self) -> set[str]:
        return {c.case_id for c in self.cases if c.passed}


def _git_sha() -> str | None:
    try:
        return (
            subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            ).stdout.strip()
            or None
        )
    except (OSError, subprocess.SubprocessError):
        return None


def _pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = max(0, min(len(s) - 1, round((len(s) - 1) * p)))
    return float(s[k])


def compute_metrics(cases: list[CaseScore]) -> dict[str, float]:
    scored = [c for c in cases if c.error is None]
    total_leaves = sum(c.total for c in scored)
    correct = sum(c.correct for c in scored)
    leaves = [leaf for c in scored for leaf in c.leaves]
    exp_unknown = [leaf for leaf in leaves if leaf.expected is None]
    act_unknown = [leaf for leaf in leaves if leaf.actual is None]
    tp = sum(1 for leaf in leaves if leaf.expected is None and leaf.actual is None)
    costs = [c.cost_usd for c in cases]
    lat = [float(c.latency_ms) for c in cases]
    statuses = Counter(c.status for c in cases)
    with_status = [c for c in cases if c.status_ok is not None]
    cov = [c.evidence_coverage for c in scored if c.evidence_coverage is not None]
    m: dict[str, float] = {
        "cases": float(len(cases)),
        "errors": float(sum(1 for c in cases if c.error is not None)),
        "field_accuracy": correct / total_leaves if total_leaves else 0.0,
        "doc_pass_rate": (sum(1 for c in scored if c.passed) / len(scored)) if scored else 0.0,
        "status_accuracy": (sum(1 for c in with_status if c.status_ok) / len(with_status))
        if with_status
        else 0.0,
        "accepted_rate": statuses.get("accepted", 0) / len(cases) if cases else 0.0,
        "review_rate": statuses.get("needs_review", 0) / len(cases) if cases else 0.0,
        "rejected_rate": statuses.get("rejected", 0) / len(cases) if cases else 0.0,
        "failed_rate": statuses.get("failed", 0) / len(cases) if cases else 0.0,
        "unknown_precision": (tp / len(act_unknown)) if act_unknown else 1.0,
        "unknown_recall": (tp / len(exp_unknown)) if exp_unknown else 1.0,
        "evidence_coverage": statistics.fmean(cov) if cov else 0.0,
        "cost_total_usd": sum(costs),
        "cost_mean_usd": statistics.fmean(costs) if costs else 0.0,
        "cost_p50_usd": _pct(costs, 0.5),
        "cost_p95_usd": _pct(costs, 0.95),
        "latency_p50_ms": _pct(lat, 0.5),
        "latency_p95_ms": _pct(lat, 0.95),
        "model_calls_mean": statistics.fmean([c.model_calls for c in cases]) if cases else 0.0,
        "repair_attempts_mean": statistics.fmean([c.repair_attempts for c in cases])
        if cases
        else 0.0,
    }
    return {k: round(v, 6) for k, v in m.items()}


def failure_clusters(cases: list[CaseScore], *, top: int = 15) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[str]] = defaultdict(list)
    for c in cases:
        for leaf in c.leaves:
            if leaf.outcome != "correct":
                groups[(leaf.template, leaf.outcome)].append(c.case_id)
        if c.error:
            groups[("(run)", "error")].append(c.case_id)
    ranked = sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    return [
        {"field": tmpl, "outcome": outcome, "count": len(ids), "cases": sorted(set(ids))[:5]}
        for (tmpl, outcome), ids in ranked[:top]
    ]


def run_eval(
    dataset: Dataset,
    runtime: LocalRuntime,
    *,
    skill: str | None = None,
    mode: str | None = None,
    model: str | None = None,
    policy: Policy | None = None,
    tags: list[str] | None = None,
    limit: int | None = None,
    ids: list[str] | None = None,
    budget_usd: float | None = None,
    on_case: Any = None,
) -> EvalRun:
    skill_name = skill or dataset.skill
    client = Client(runtime)
    skill_obj = runtime.skills.resolve(skill_name) if skill_name else None
    model_id = model or runtime.config.default_model
    config = EvalConfig(
        dataset=dataset.ref,
        skill=skill_name,
        skill_version=skill_obj.version if skill_obj else None,
        skill_hash=skill_obj.bundle_hash if skill_obj else None,
        mode=mode,
        model=model_id,
        registry_version=runtime.gateway.registry.version,
        record_mode=runtime.config.record_mode.value,
        cassette_dir=str(runtime.config.cassette_dir) if runtime.config.cassette_dir else None,
        policy=(policy or Policy()).model_dump(mode="json", exclude_defaults=True),
        git_sha=_git_sha(),
        tags=tags or [],
        budget_usd=budget_usd,
        started_at=now().isoformat(),
    )
    run = EvalRun(id=new_id("eval"), config=config)
    spent = 0.0
    for case in dataset.select(tags=tags, limit=limit, ids=ids):
        if budget_usd is not None and spent >= budget_usd:
            run.cases.append(
                score_case(
                    case.id,
                    None,
                    None,
                    dataset.scoring,
                    expected_status=case.expected_status,
                    tags=case.tags,
                    error="budget_exhausted",
                )
            )
            continue
        result: RunResult | None = None
        error: str | None = None
        try:
            result = client.run(
                skill=skill_name,
                mode=mode,
                models=model_id,
                inputs=[
                    PathInput(
                        path=str(dataset.input_path(case, name)), name=f"{name}{Path(rel).suffix}"
                    )
                    for name, rel in case.inputs.items()
                ],
                policy=policy,
                metadata={"eval_id": run.id, "eval_case": case.id, "dataset": dataset.ref},
            )
            spent += result.usage.totals.cost_usd
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        scored = score_case(
            case.id,
            dataset.expected_data(case),
            result,
            dataset.scoring,
            expected_status=case.expected_status,
            tags=case.tags,
            error=error,
        )
        run.cases.append(scored)
        if on_case is not None:
            on_case(scored)
    run.metrics = compute_metrics(run.cases)
    run.clusters = failure_clusters(run.cases)
    run.finished_at = now().isoformat()
    return run


def case_from_dataset(dataset: Dataset, case: EvalCase) -> dict[str, Any]:
    return {
        "id": case.id,
        "inputs": {k: str(dataset.input_path(case, k)) for k in case.inputs},
        "expected": dataset.expected_data(case),
    }


__all__ = [
    "EvalConfig",
    "EvalRun",
    "case_from_dataset",
    "compute_metrics",
    "failure_clusters",
    "run_eval",
]
