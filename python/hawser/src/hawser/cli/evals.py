"""``hawser eval run|gate`` and ``hawser compare``."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Annotated

import typer
import yaml
from rich.console import Console
from rich.table import Table

from hawser.config import HawserConfig, RecordMode
from hawser.models import Policy
from hawser.runtime.local import LocalRuntime

eval_app = typer.Typer(help="Evaluate skills against datasets.", no_args_is_help=True)
console = Console()
err = Console(stderr=True)


def _slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9.-]+", "_", s)


@eval_app.command("run")
def eval_run(  # noqa: PLR0917
    dataset: Annotated[
        Path, typer.Option("--dataset", "-d", help="Dataset directory (with dataset.yaml).")
    ],
    skill: Annotated[
        str | None, typer.Option("--skill", help="Skill name; default from the dataset.")
    ] = None,
    mode: Annotated[str | None, typer.Option("--mode", help="direct | workflow | agent")] = None,
    model: Annotated[str | None, typer.Option("--model")] = None,
    tags: Annotated[
        list[str] | None, typer.Option("--tag", help="Only cases with this tag (repeatable).")
    ] = None,
    limit: Annotated[int | None, typer.Option("--limit")] = None,
    budget: Annotated[
        float | None, typer.Option("--budget", help="Stop after this much live spend (USD).")
    ] = None,
    live: Annotated[
        bool, typer.Option("--live", help="Call the provider on cassette misses (auto mode).")
    ] = False,
    policy: Annotated[Path | None, typer.Option("--policy")] = None,
    out: Annotated[
        Path | None, typer.Option("--out", help="Report directory; default .hawser/evals/<id>.")
    ] = None,
) -> None:
    """Run a dataset through a skill and write report.json / report.md."""
    from hawser_evals import CaseScore, load_dataset, run_eval, write_report  # noqa: PLC0415

    ds = load_dataset(dataset)
    cfg = HawserConfig.load()
    model_id = model or cfg.default_model
    cassette_dir = ds.path / "cassettes" / _slug(model_id)
    cfg = HawserConfig.load(
        cassette_dir=cassette_dir,
        record_mode=RecordMode.auto.value if live else RecordMode.replay.value,
        default_model=model_id,
    )
    runtime = LocalRuntime(cfg)
    pol = (
        Policy.model_validate(yaml.safe_load(policy.read_text(encoding="utf-8")) or {})
        if policy
        else None
    )
    total = len(ds.select(tags=tags, limit=limit))
    console.print(
        f"evaluating {ds.ref} with {skill or ds.skill} on {model_id}: {total} case(s)\n"
        f"cassettes: {cassette_dir}"
    )

    def progress(cs: CaseScore) -> None:
        tail = f"  {cs.error}" if cs.error else ""
        console.print(
            f"  {cs.case_id}: {cs.status} {cs.correct}/{cs.total} ${cs.cost_usd:.4f}{tail}"
        )

    run = run_eval(
        ds,
        runtime,
        skill=skill,
        mode=mode,
        model=model_id,
        policy=pol,
        tags=tags,
        limit=limit,
        budget_usd=budget,
        on_case=progress,
    )
    out_dir = out or Path(cfg.home) / "evals" / run.id
    write_report(run, out_dir)
    table = Table(title=f"{run.id}")
    table.add_column("metric")
    table.add_column("value", justify="right")
    for key in (
        "field_accuracy",
        "doc_pass_rate",
        "status_accuracy",
        "review_rate",
        "failed_rate",
        "evidence_coverage",
        "cost_total_usd",
        "cost_mean_usd",
        "latency_p50_ms",
        "model_calls_mean",
        "repair_attempts_mean",
    ):
        table.add_row(key, f"{run.metrics.get(key, 0.0):.4f}")
    console.print(table)
    if run.clusters:
        console.print(
            "top failure clusters: "
            + "; ".join(f"{c['field']} {c['outcome']} x{c['count']}" for c in run.clusters[:5])
        )
    console.print(f"report: {out_dir / 'report.md'}")


@eval_app.command("gate")
def eval_gate(
    report: Annotated[Path, typer.Argument(help="report.json or its directory.")],
    thresholds: Annotated[Path, typer.Option("--thresholds", help="YAML: {metric: {min|max: x}}")],
) -> None:
    """Exit non-zero when a metric breaches its threshold (CI gate)."""
    from hawser_evals import check_thresholds  # noqa: PLC0415
    from hawser_evals.report import load_report  # noqa: PLC0415

    run = load_report(report)
    spec = yaml.safe_load(thresholds.read_text(encoding="utf-8")) or {}
    failures = check_thresholds(run, spec)
    for f in failures:
        err.print(f"[red]gate[/] {f}")
    if failures:
        raise typer.Exit(1)
    console.print(f"[green]gate passed[/] ({len(spec)} threshold(s))")


def compare_cmd(
    baseline: Annotated[Path, typer.Argument(help="Baseline report.json or directory.")],
    candidate: Annotated[Path, typer.Argument(help="Candidate report.json or directory.")],
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Compare two evaluation reports: metric deltas, regressions, improvements."""
    from hawser_evals import compare, render_compare_md  # noqa: PLC0415
    from hawser_evals.report import load_report  # noqa: PLC0415

    cmp = compare(load_report(baseline), load_report(candidate))
    if as_json:
        console.print_json(json.dumps(cmp))
    else:
        console.print(render_compare_md(cmp))
    raise typer.Exit(1 if cmp["regressions"] else 0)


__all__ = ["compare_cmd", "eval_app"]


@eval_app.command("publish")
def eval_publish(
    report: Annotated[Path, typer.Argument(help="report.json or its directory.")],
    name: Annotated[str | None, typer.Option("--name", help="Display name on the server.")] = None,
) -> None:
    """Publish a report to the configured server (``--server`` / HAWSER_SERVER_URL)."""
    import json as _json  # noqa: PLC0415

    from hawser.cli.common import runtime as _runtime  # noqa: PLC0415
    from hawser.runtime.remote import RemoteRuntime  # noqa: PLC0415

    rt = _runtime()
    if not isinstance(rt, RemoteRuntime):
        err.print("[red]no server configured; pass --server or set HAWSER_SERVER_URL[/]")
        raise typer.Exit(2)
    path = report / "report.json" if report.is_dir() else report
    data = _json.loads(path.read_text(encoding="utf-8"))
    out = rt.post_json("/v1/evals", {"report": data, "name": name})
    console.print(f"published [bold]{out['id']}[/] ({out['name']}, {out['cases']} cases)")
