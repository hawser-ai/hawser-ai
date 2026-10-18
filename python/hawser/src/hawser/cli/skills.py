"""``hawser skill``: list, validate, new, install, test, export."""

from __future__ import annotations

import json
import shutil
import tarfile
import tempfile
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from hawser.client import Client
from hawser.config import HawserConfig, RecordMode
from hawser.errors import HawserError
from hawser.models import PathInput, RunStatus
from hawser.runtime.local import LocalRuntime
from hawser.skills import Lockfile, SkillRegistry, load_bundle

skill_app = typer.Typer(help="Skill bundles.", no_args_is_help=True)

SKILL_YAML = """name: {name}
version: 0.1.0
description: Describe what this skill produces.
mode: workflow
instruction: Extract the document.
inputs:
  document:
    type: document
output_schema: schemas.py:Output
tools:
  - document.extract
validators: []
evidence:
  required: []
policy:
  repair:
    attempts: 1
  review_on_failure: true
"""

SCHEMAS_PY = """from pydantic import BaseModel, Field


class Output(BaseModel):
    title: str = Field(description="The document title")
    date: str | None = Field(default=None, description="ISO 8601 date")
"""

WORKFLOW_PY = """from hawser.workflow import WorkflowContext

from schemas import Output


def run(ctx: WorkflowContext) -> dict:
    doc = ctx.extract("document", Output)
    ctx.attach("", doc)
    return doc.result or {}
"""
console = Console()
err = Console(stderr=True)


@skill_app.command("list")
def skill_list() -> None:
    """List skills found on the search paths."""
    cfg = HawserConfig.load()
    reg = SkillRegistry(list(cfg.skill_paths))
    lock = Lockfile.load()
    table = Table(title="Skills")
    for col in ("name", "version", "mode", "locked", "path"):
        table.add_column(col)
    for s in reg.list():
        locked = lock.skills.get(s.name)
        state = "-" if locked is None else ("ok" if lock.verify(s) is None else "drift")
        table.add_row(s.name, s.version, s.manifest.mode.value, state, str(s.path))
    console.print(table)


@skill_app.command("validate")
def skill_validate(path: Annotated[Path, typer.Argument(help="Bundle directory.")]) -> None:
    """Load a bundle and report its manifest, tools, validators and hash."""
    try:
        s = load_bundle(path)
    except HawserError as exc:
        err.print(f"[red]{exc.code}[/]: {exc.message}")
        raise typer.Exit(1) from None
    console.print(
        f"[green]ok[/] {s.name}@{s.version} ({s.manifest.mode.value}) {s.bundle_hash[:19]}…"
    )
    console.print(f"inputs: {', '.join(s.manifest.inputs)}")
    console.print(f"tools: {', '.join(s.manifest.tools) or '-'}")
    console.print(
        "validators: "
        + (", ".join(v if isinstance(v, str) else v.id for v in s.manifest.validators) or "-")
    )
    console.print(f"local validators: {', '.join(s.validators) or '-'}")
    console.print(f"output fields: {', '.join(s.output_schema.get('properties', {}))}")


@skill_app.command("new")
def skill_new(
    name: Annotated[str, typer.Argument(help="Skill name (slug).")],
    directory: Annotated[Path, typer.Option("--dir")] = Path("skills"),
) -> None:
    """Scaffold a minimal workflow skill bundle."""
    target = directory / name
    if target.exists():
        err.print(f"[red]{target} already exists[/]")
        raise typer.Exit(1)
    (target / "prompts").mkdir(parents=True)
    (target / "skill.yaml").write_text(SKILL_YAML.format(name=name), encoding="utf-8")
    (target / "schemas.py").write_text(SCHEMAS_PY, encoding="utf-8")
    (target / "workflow.py").write_text(WORKFLOW_PY, encoding="utf-8")
    (target / "CHANGELOG.md").write_text(f"# {name}\n\n## 0.1.0\n- scaffolded\n", encoding="utf-8")
    console.print(f"[green]created[/] {target}")


@skill_app.command("install")
def skill_install(
    path: Annotated[
        Path, typer.Argument(help="Bundle directory (copied into ./skills unless already there).")
    ],
    accept_permissions: Annotated[
        bool, typer.Option("--accept-permissions", help="Accept widened tool/model permissions.")
    ] = False,
) -> None:
    """Record a bundle in hawser.lock, showing the permission diff; copy it into ./skills."""
    s = load_bundle(path)
    lock = Lockfile.load()
    diff = lock.diff(s)
    console.print_json(json.dumps(diff))
    if lock.widens(s) and not accept_permissions:
        err.print("[yellow]this upgrade widens permissions; re-run with --accept-permissions[/]")
        raise typer.Exit(2)
    dest = Path("skills") / s.name
    if path.resolve() != dest.resolve():
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(path, dest, ignore=shutil.ignore_patterns("__pycache__"))
        s = load_bundle(dest)
    lock.record(s)
    lock.save()
    console.print(f"[green]installed[/] {s.name}@{s.version} -> {dest} (locked)")


@skill_app.command("test")
def skill_test(
    path: Annotated[Path, typer.Argument(help="Bundle directory.")],
    live: Annotated[
        bool, typer.Option("--live", help="Call the provider; otherwise replay only.")
    ] = False,
    model: Annotated[str | None, typer.Option("--model")] = None,
) -> None:
    """Run the bundle's examples/ cases (inputs/*.pdf + expected.json) through the runtime."""
    s = load_bundle(path)
    cases = (
        sorted(p for p in (s.path / "examples").iterdir() if (p / "expected.json").exists())
        if (s.path / "examples").exists()
        else []
    )
    if not cases:
        err.print("[yellow]no examples/<case>/expected.json found[/]")
        raise typer.Exit(1)
    cfg = HawserConfig.load(
        cassette_dir=s.path / "examples" / "cassette",
        record_mode=RecordMode.auto.value if live else RecordMode.replay.value,
        default_model=model,
        skill_paths=[s.path.parent],
    )
    client = Client(LocalRuntime(cfg))
    failures = 0
    for case in cases:
        inputs = [PathInput(path=str(f), name=f.name) for f in sorted((case / "inputs").iterdir())]
        result = client.run(skill=s.name, inputs=inputs, models=model)
        expected = json.loads((case / "expected.json").read_text(encoding="utf-8"))
        ok = result.status != RunStatus.failed and result.result == expected.get(
            "result", result.result
        )
        failures += 0 if ok else 1
        mark = "[green]ok[/]" if ok else "[red]FAIL[/]"
        cost = result.usage.totals.cost_usd
        console.print(f"{mark} {case.name}: {result.status.value} (${cost:.4f})")
    raise typer.Exit(1 if failures else 0)


@skill_app.command("export")
def skill_export(
    path: Annotated[Path, typer.Argument(help="Bundle directory.")],
    out: Annotated[Path | None, typer.Option("--out")] = None,
) -> None:
    """Pack a bundle into a .tar.gz with its hash in the name."""
    s = load_bundle(path)
    out = out or Path(f"{s.name}-{s.version}-{s.bundle_hash[7:19]}.tar.gz")
    with tempfile.TemporaryDirectory() as tmp, tarfile.open(out, "w:gz") as tar:
        staged = Path(tmp) / s.name
        shutil.copytree(s.path, staged, ignore=shutil.ignore_patterns("__pycache__", "cassette"))
        tar.add(staged, arcname=s.name)
    console.print(f"[green]exported[/] {out} ({s.bundle_hash})")


__all__ = ["skill_app"]
