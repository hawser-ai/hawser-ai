"""Typer application: init, quickstart, run, inspect, runs, models."""

from __future__ import annotations

import importlib
import json
import shutil
import sys
from importlib import resources
from pathlib import Path
from typing import Annotated, Any

import typer
import yaml
from pydantic import BaseModel
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from hawser import __version__
from hawser.cli.approvals import approvals_app, approve_cmd
from hawser.cli.common import STATE, AnyRuntime
from hawser.cli.common import runtime as _any_runtime
from hawser.cli.evals import compare_cmd, eval_app
from hawser.cli.review import review_app
from hawser.cli.skills import skill_app
from hawser.client import Client
from hawser.config import CONFIG_FILENAME, RecordMode
from hawser.models import Event, Policy, RunResult, RunStatus
from hawser.runtime.local import LocalRuntime
from hawser.runtime.remote import RemoteRuntime

app = typer.Typer(
    name="hawser",
    help="Hawser: verifiable multimodal AI workflows.",
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
)
runs_app = typer.Typer(help="Inspect past runs.", no_args_is_help=True)
models_app = typer.Typer(help="Model registry.", no_args_is_help=True)
app.add_typer(runs_app, name="runs")
app.add_typer(models_app, name="models")
app.add_typer(skill_app, name="skill")
app.add_typer(review_app, name="review")
app.add_typer(eval_app, name="eval")
app.add_typer(approvals_app, name="approvals")
app.command("approve")(approve_cmd)
app.command("compare")(compare_cmd)

console = Console()
err = Console(stderr=True)

QUICKSTART_INSTRUCTION = "Extract the invoice."
_STATUS_STYLE = {
    RunStatus.accepted: "bold green",
    RunStatus.needs_review: "bold yellow",
    RunStatus.rejected: "bold red",
    RunStatus.failed: "bold red",
    RunStatus.cancelled: "dim",
}


def _version(value: bool) -> None:
    if value:
        console.print(f"hawser {__version__}")
        raise typer.Exit()


@app.callback()
def _main(
    version: Annotated[
        bool, typer.Option("--version", callback=_version, is_eager=True, help="Show the version.")
    ] = False,
    server: Annotated[
        str | None,
        typer.Option("--server", help="hawserd URL (or HAWSER_SERVER_URL / [server] url)."),
    ] = None,
    api_key: Annotated[
        str | None, typer.Option("--api-key", help="API key for --server (or HAWSER_API_KEY).")
    ] = None,
) -> None:
    """Hawser: build verifiable multimodal AI workflows with your choice of models."""
    STATE["server_url"] = server
    STATE["api_key"] = api_key


# -- helpers -------------------------------------------------------------------------------


def _runtime(
    cwd: Path | None = None,
    *,
    cassette: Path | None = None,
    record_mode: str | None = None,
    model: str | None = None,
    force_local: bool = False,
) -> AnyRuntime:
    overrides: dict[str, Any] = {}
    if cassette is not None:
        overrides["cassette_dir"] = cassette
        overrides["record_mode"] = record_mode or RecordMode.auto.value
    elif record_mode is not None:
        overrides["record_mode"] = record_mode
    if model is not None:
        overrides["default_model"] = model
    return _any_runtime(cwd, force_local=force_local, **overrides)


def _local(**kwargs: Any) -> LocalRuntime:
    rt = _runtime(force_local=True, **kwargs)
    assert isinstance(rt, LocalRuntime)
    return rt


def _progress(event: Event) -> None:
    p = event.payload
    if event.type.value == "step.started":
        console.print(f"[dim]{event.seq:>3}[/] step {p.get('key')} started")
    elif event.type.value == "step.completed":
        console.print(f"[dim]{event.seq:>3}[/] step {p.get('key')} completed")
    elif event.type.value == "run.status_changed":
        console.print(f"[dim]{event.seq:>3}[/] {p.get('from')} -> {p.get('to')}")


def _load_schema(spec: str) -> dict[str, Any] | type[BaseModel]:
    if ":" in spec and not Path(spec).exists():
        module_name, _, attr = spec.partition(":")
        # Like `python -m`, look in the current directory first: the model usually sits next to
        # the documents, and a console script does not put the working directory on sys.path.
        cwd = str(Path.cwd())
        if cwd not in sys.path:
            sys.path.insert(0, cwd)
        try:
            module = importlib.import_module(module_name)
        except ImportError as exc:
            raise typer.BadParameter(f"cannot import {module_name!r} for --schema: {exc}") from exc
        obj = getattr(module, attr, None)
        if isinstance(obj, type) and issubclass(obj, BaseModel):
            return obj
        raise typer.BadParameter(f"{spec} is not a Pydantic model")
    path = Path(spec)
    if not path.exists():
        raise typer.BadParameter(f"schema file not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise typer.BadParameter("schema file must contain a JSON object")
    return data  # type: ignore[return-value]


def _load_policy(path: Path | None) -> Policy:
    if path is None:
        return Policy()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return Policy.model_validate(raw)


def _print_result(result: RunResult, *, as_json: bool, runtime: AnyRuntime | None = None) -> None:
    if as_json:
        console.print_json(result.model_dump_json())
        return
    style = _STATUS_STYLE.get(result.status, "bold")
    console.print(
        Panel(f"[{style}]{result.status.value.upper()}[/]  run {result.run_id}", title="hawser")
    )
    if result.error:
        console.print(f"[red]{result.error.get('code')}[/]: {result.error.get('message')}")
    if result.result is not None:
        if isinstance(result.result, str):
            console.print(result.result)
        else:
            console.print_json(json.dumps(result.result))
    if result.field_meta:
        _print_fields(result)
    if result.validation.issues:
        _print_issues(result)
    for w in result.warnings:
        console.print(f"[yellow]warning[/] {w.code}: {w.message}")
    _print_usage(result)
    console.print(f"[dim]hawser inspect {result.run_id}[/]")


def _print_fields(result: RunResult, only: str | None = None) -> None:
    table = Table(title="Fields", show_lines=False)
    table.add_column("field")
    table.add_column("value")
    table.add_column("status")
    table.add_column("page", justify="right")
    table.add_column("bbox (pt)")
    table.add_column("conf", justify="right")
    by_id = {e.id: e for e in result.evidence}
    values = _flatten(result.result)
    for ptr, meta in sorted(result.field_meta.items()):
        if only and ptr != only:
            continue
        ev = next((by_id[i] for i in meta.evidence if i in by_id), None)
        page = str(getattr(ev, "page", "")) if ev else ""
        bbox = getattr(ev, "bbox", None)
        bbox_s = (
            f"{bbox.x0:.0f},{bbox.y0:.0f}-{bbox.x1:.0f},{bbox.y1:.0f}"
            if bbox
            else ("page" if ev else "")
        )
        conf = f"{meta.anchor_confidence:.2f}" if meta.anchor_confidence is not None else ""
        status = meta.status.value
        if meta.issues:
            status += f" ({len(meta.issues)} issue)"
        table.add_row(ptr, _short(values.get(ptr)), status, page, bbox_s, conf)
    console.print(table)


def _print_issues(result: RunResult) -> None:
    table = Table(title="Validation")
    table.add_column("severity")
    table.add_column("rule")
    table.add_column("field")
    table.add_column("message")
    for i in result.validation.issues:
        colour = {"error": "red", "warning": "yellow"}.get(i.severity.value, "white")
        table.add_row(f"[{colour}]{i.severity.value}[/]", i.rule_id, i.field or "", i.message)
    console.print(table)


def _print_usage(result: RunResult) -> None:
    t = result.usage.totals
    replayed = any(c.replayed for c in result.usage.model_calls)
    note = " [dim](replayed from cassette, no cost incurred)[/]" if replayed else ""
    console.print(
        f"usage: {t.model_calls} model call(s), {t.input_tokens} in / {t.output_tokens} out, "
        f"${t.cost_usd:.4f}, {t.wall_time_ms} ms{note}"
    )


def _flatten(data: Any, path: str = "", out: dict[str, Any] | None = None) -> dict[str, Any]:
    out = {} if out is None else out
    if isinstance(data, dict):
        for k, v in data.items():  # type: ignore[union-attr]
            _flatten(v, f"{path}/{k}", out)
    elif isinstance(data, list):
        for i, v in enumerate(data):  # type: ignore[arg-type]
            _flatten(v, f"{path}/{i}", out)
    else:
        out[path] = data
    return out


def _short(value: Any, width: int = 40) -> str:
    s = "" if value is None else str(value)
    return s if len(s) <= width else s[: width - 1] + "…"


def _exit_code(status: RunStatus) -> int:
    return {
        RunStatus.accepted: 0,
        RunStatus.needs_review: 0,
        RunStatus.rejected: 2,
        RunStatus.failed: 1,
    }.get(status, 1)


# -- commands ------------------------------------------------------------------------------


@app.command()
def init(
    directory: Annotated[Path, typer.Argument(help="Project directory to initialise.")] = Path("."),
    force: Annotated[bool, typer.Option("--force", help="Overwrite existing files.")] = False,
    server: Annotated[
        str | None, typer.Option("--server", help="Point the CLI and SDK at a hawserd URL.")
    ] = None,
) -> None:
    """Create hawser.toml, a local .hawser/ store and the quickstart example."""
    directory.mkdir(parents=True, exist_ok=True)
    toml = directory / CONFIG_FILENAME
    if toml.exists() and not force:
        err.print(f"[yellow]{toml} already exists; use --force to overwrite[/]")
    else:
        text = (
            "[hawser]\n"
            '# default_model = "anthropic/claude-opus-5-5"\n'
            '# default_effort = "low"\n'
            "# max_pages = 200\n"
        )
        if server:
            text += (
                f'\n[server]\nurl = "{server}"\n# api_key: set HAWSER_API_KEY in the environment\n'
            )
        toml.write_text(text, encoding="utf-8")
    if server:
        console.print(
            f"server [bold]{server}[/]; export HAWSER_API_KEY=<key> before running commands"
        )
    (directory / ".hawser").mkdir(exist_ok=True)
    qs = directory / "quickstart"
    src = resources.files("hawser._quickstart")
    qs.mkdir(exist_ok=True)
    for entry in src.iterdir():
        if entry.name in ("__init__.py", "__pycache__"):  # package plumbing, not example files
            continue
        target = qs / entry.name
        if entry.is_dir():
            if target.exists() and force:
                shutil.rmtree(target)
            if not target.exists():
                target.mkdir()
                for sub in entry.iterdir():
                    (target / sub.name).write_bytes(sub.read_bytes())
        elif not target.exists() or force:
            target.write_bytes(entry.read_bytes())
    console.print(
        Panel(
            f"Initialised [bold]{directory.resolve()}[/]\n\n"
            "Next:\n"
            + (f"  cd {directory}\n" if str(directory) != "." else "")
            + "  hawser quickstart            # recorded example, no API key needed\n"
            "  hawser quickstart --live     # real model; needs ANTHROPIC_API_KEY\n",
            title="hawser init",
        )
    )


@app.command()
def quickstart(
    live: Annotated[
        bool, typer.Option("--live", help="Call the real provider instead of the recording.")
    ] = False,
    model: Annotated[str | None, typer.Option("--model", help="Registry model id to pin.")] = None,
    as_json: Annotated[
        bool, typer.Option("--json", help="Print the full RunResult as JSON.")
    ] = False,
) -> None:
    """Run the bundled invoice example (recorded by default)."""
    qs = Path("quickstart")
    if not (qs / "invoice.pdf").exists():
        err.print("[red]no quickstart/ directory here; run `hawser init` first[/]")
        raise typer.Exit(1)
    runtime = _local(
        cassette=qs / "cassette",
        record_mode=RecordMode.record.value if live else RecordMode.replay.value,
        model=model,
    )
    client = Client(runtime)
    result = client.run(
        QUICKSTART_INSTRUCTION,
        inputs=[qs / "invoice.pdf"],
        output_schema=_load_schema(str(qs / "invoice.schema.json")),
        policy=_load_policy(qs / "policy.yaml"),
    )
    _print_result(result, as_json=as_json, runtime=runtime)
    if (
        result.status == RunStatus.failed
        and result.error
        and result.error.get("code") == "cassette_miss"
    ):
        err.print(
            "[yellow]The recording does not match this request. Re-record with "
            "`hawser quickstart --live` after setting ANTHROPIC_API_KEY.[/]"
        )
    raise typer.Exit(_exit_code(result.status))


@app.command()
def run(  # noqa: PLR0917
    instruction: Annotated[str, typer.Option("--instruction", "-i", help="What to do.")],
    inputs: Annotated[
        list[Path] | None, typer.Option("--input", "-f", help="Input file (repeatable).")
    ] = None,
    schema: Annotated[
        str | None, typer.Option("--schema", help="JSON Schema file or module:Model.")
    ] = None,
    policy: Annotated[Path | None, typer.Option("--policy", help="Policy YAML file.")] = None,
    model: Annotated[str | None, typer.Option("--model", help="Registry model id to pin.")] = None,
    cassette: Annotated[
        Path | None, typer.Option("--cassette", help="Cassette directory (replay/record).")
    ] = None,
    record_mode: Annotated[
        str | None, typer.Option("--record-mode", help="replay | record | auto | live")
    ] = None,
    as_json: Annotated[
        bool, typer.Option("--json", help="Print the full RunResult as JSON.")
    ] = False,
) -> None:
    """Run an instruction over inputs, optionally into a schema."""
    runtime = _runtime(cassette=cassette, record_mode=record_mode, model=model)
    request = Client.build_request(
        instruction,
        inputs=list(inputs or []),
        output_schema=_load_schema(schema) if schema else None,
        policy=_load_policy(policy),
        models=model,
    )
    handle = runtime.submit(request)
    if isinstance(runtime, RemoteRuntime) and not as_json:
        console.print(f"[dim]submitted {handle.run_id} to {runtime.base_url}[/]")
        for event in runtime.stream_events(handle.run_id):
            _progress(event)
    result = handle.result()
    _print_result(result, as_json=as_json, runtime=runtime)
    raise typer.Exit(_exit_code(result.status))


@app.command()
def inspect(  # noqa: PLR0912
    run_id: Annotated[str, typer.Argument(help="Run id (run_...).")],
    field: Annotated[
        str | None, typer.Option("--field", help="Show one field's evidence (JSON pointer).")
    ] = None,
    events: Annotated[bool, typer.Option("--events", help="Print the event timeline.")] = False,
    plan: Annotated[bool, typer.Option("--plan", help="Print the agent plan (nodes).")] = False,
    as_json: Annotated[
        bool, typer.Option("--json", help="Print the full RunResult as JSON.")
    ] = False,
) -> None:
    """Show a run's status, steps, fields, evidence and usage."""
    runtime = _runtime()
    result = runtime.get(run_id)
    if result is None:
        err.print(f"[red]unknown run {run_id}[/]")
        raise typer.Exit(1)
    if as_json:
        console.print_json(result.model_dump_json())
        return
    if field:
        _print_fields(result, only=field)
        for ev in result.evidence_for(field):
            console.print_json(ev.model_dump_json())
        return
    style = _STATUS_STYLE.get(result.status, "bold")
    console.print(
        Panel(
            f"[{style}]{result.status.value.upper()}[/]  {result.run_id}  mode={result.mode}",
            title="run",
        )
    )

    steps = Table(title="Steps")
    for col in ("key", "kind", "status", "ms", "calls", "cost"):
        steps.add_column(col)
    for s in runtime.steps(run_id):
        ms = ""
        if s.started_at and s.completed_at:
            ms = str(int((s.completed_at - s.started_at).total_seconds() * 1000))
        steps.add_row(
            s.key,
            s.kind.value,
            s.status.value,
            ms,
            str(s.usage.totals.model_calls),
            f"{s.usage.totals.cost_usd:.4f}",
        )
    console.print(steps)

    if result.field_meta:
        _print_fields(result)
    if result.validation.issues:
        _print_issues(result)
    if plan:
        plan_ev = next(
            (
                e
                for e in runtime.events(run_id)
                if e.type.value == "plan.created" and "plan_id" in e.payload
            ),
            None,
        )
        if plan_ev is None:
            console.print("[yellow]no agent plan for this run[/]")
        else:
            src, attempts = plan_ev.payload.get("source"), plan_ev.payload.get("attempts", 0)
            ptable = Table(title=f"Plan ({src}, {attempts} attempt(s))")
            for col in ("node", "kind", "tool"):
                ptable.add_column(col)
            for n in plan_ev.payload.get("nodes", []):
                ptable.add_row(n["id"], n["kind"], n.get("tool") or "")
            console.print(ptable)
    if events:
        timeline = Table(title="Events")
        timeline.add_column("seq", justify="right")
        timeline.add_column("at")
        timeline.add_column("type")
        timeline.add_column("payload")
        for e in runtime.events(run_id):
            timeline.add_row(
                str(e.seq),
                e.at.strftime("%H:%M:%S.%f")[:-3],
                e.type.value,
                _short(json.dumps(e.payload), 80),
            )
        console.print(timeline)
    _print_usage(result)


@runs_app.command("list")
def runs_list(limit: Annotated[int, typer.Option("--limit", "-n")] = 20) -> None:
    """List recent runs."""
    runtime = _runtime()
    table = Table(title="Runs")
    table.add_column("run", no_wrap=True)
    for col in ("status", "mode", "cost", "created"):
        table.add_column(col)
    for r in runtime.list(limit=limit):
        table.add_row(
            r.id,
            r.status.value,
            r.mode.value if r.mode else "",
            f"{r.cost_usd:.4f}",
            r.created_at.isoformat(timespec="seconds"),
        )
    console.print(table)


@runs_app.command("export")
def runs_export(
    run_id: Annotated[str, typer.Argument()],
    revision: Annotated[
        str, typer.Option("--revision", help="machine | reviewed | latest")
    ] = "latest",
) -> None:
    """Print a run's result (machine, reviewed, or latest revision) as JSON."""
    runtime = _runtime()
    result = runtime.get(run_id)
    if result is None:
        err.print(f"[red]unknown run {run_id}[/]")
        raise typer.Exit(1)
    revs = runtime.reviews.revisions(run_id)
    chosen = None
    if revision == "machine":
        chosen = next((r for r in revs if r.kind == "machine"), None)
    elif revision == "reviewed":
        chosen = next((r for r in reversed(revs) if r.kind == "reviewed"), None)
        if chosen is None:
            err.print("[yellow]no reviewed revision yet[/]")
            raise typer.Exit(1)
    else:
        chosen = revs[-1] if revs else None
    payload = {
        "run_id": run_id,
        "status": result.status.value,
        "revision_id": chosen.id if chosen else result.revision_id,
        "revision_kind": chosen.kind if chosen else "machine",
        "result": chosen.result if chosen else result.result,
    }
    console.print_json(json.dumps(payload))


@models_app.command("check")
def models_check(
    model_id: Annotated[
        str, typer.Argument(help="Registry model id, e.g. openrouter/anthropic/claude-haiku-5.5")
    ],
) -> None:
    """Verify a registry entry with a few cheap live calls (text, structured output, image)."""
    from hawser.gateway.testing import compatibility_checks, summarize  # noqa: PLC0415

    runtime = _local()
    spec = runtime.gateway.registry.get(model_id)
    adapter = runtime.gateway.adapters.get(spec.provider)
    if adapter is None:
        err.print(f"[red]no adapter for provider {spec.provider}[/]")
        raise typer.Exit(1)
    results = compatibility_checks(adapter, spec, live=True)
    for r in results:
        console.print(f"{'[green]ok[/]  ' if r.passed else '[red]FAIL[/]'} {r.name}: {r.detail}")
    s = summarize(results)
    console.print(f"{s['passed']}/{len(results)} checks passed")
    raise typer.Exit(0 if not s["failed"] else 1)


@models_app.command("list")
def models_list() -> None:
    """List registry models with capabilities and pricing."""
    runtime = _local()
    table = Table(title=f"Models (registry {runtime.gateway.registry.version})")
    for col in ("id", "modalities", "context", "structured", "$/Mtok in", "$/Mtok out", "hosted"):
        table.add_column(col)
    for m in runtime.gateway.registry.all():
        p = m.pricing
        table.add_row(
            m.id,
            ",".join(x.value for x in m.modalities),
            f"{m.context_window:,}",
            "yes" if m.structured_output else "no",
            f"{p.input_per_mtok:.2f}" if p else "-",
            f"{p.output_per_mtok:.2f}" if p else "-",
            "yes" if m.data_handling.hosted else "no",
        )
    console.print(table)


__all__ = ["app"]
