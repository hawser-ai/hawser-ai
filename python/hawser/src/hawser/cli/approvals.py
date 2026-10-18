"""``hawser approvals list`` and ``hawser approve``: decide side-effect approvals and resume."""

from __future__ import annotations

import json
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from hawser.cli.common import runtime as _runtime
from hawser.errors import HawserError
from hawser.models import ApprovalStatus

approvals_app = typer.Typer(help="Approvals for side-effecting tool actions.", no_args_is_help=True)
console = Console()
err = Console(stderr=True)


@approvals_app.command("list")
def approvals_list(
    status: Annotated[
        str | None, typer.Option("--status", help="pending | approved | rejected")
    ] = "pending",
    run_id: Annotated[str | None, typer.Option("--run")] = None,
) -> None:
    """List approvals (pending by default)."""
    rt = _runtime()
    table = Table(title="Approvals")
    for col in ("approval", "run", "tool", "action", "status", "payload", "requested"):
        table.add_column(col)
    st = ApprovalStatus(status) if status and status != "all" else None
    for a in rt.approvals.list(run_id=run_id, status=st):
        table.add_row(
            a.id,
            a.run_id,
            a.tool,
            a.action_id,
            a.status.value,
            json.dumps(a.payload)[:60],
            a.requested_at.isoformat(timespec="seconds"),
        )
    console.print(table)


def approve_cmd(
    run_id: Annotated[str, typer.Argument(help="Run id whose pending approvals to decide.")],
    reject: Annotated[bool, typer.Option("--reject", help="Reject instead of approve.")] = False,
    comment: Annotated[str | None, typer.Option("--comment")] = None,
    by: Annotated[str, typer.Option("--by")] = "local",
    no_resume: Annotated[
        bool, typer.Option("--no-resume", help="Decide without resuming the run.")
    ] = False,
) -> None:
    """Approve (or reject) every pending action of a run, then resume it."""
    rt = _runtime()
    pending = rt.approvals.list(run_id=run_id, status=ApprovalStatus.pending)
    if not pending:
        err.print(f"[yellow]no pending approvals for {run_id}[/]")
        raise typer.Exit(1)
    for a in pending:
        rt.approvals.decide(a.id, approve=not reject, by=by, comment=comment)
        verdict = "[red]rejected[/]" if reject else "[green]approved[/]"
        console.print(f"{verdict} {a.tool} {a.action_id} ({a.id})")
    if no_resume:
        return
    try:
        result = rt.resume(run_id).result()
    except HawserError as exc:
        err.print(f"[red]{exc.code}[/]: {exc.message}")
        raise typer.Exit(1) from None
    console.print(f"resumed {run_id} -> [bold]{result.status.value}[/]")
    if result.error:
        console.print(f"[red]{result.error.get('code')}[/]: {result.error.get('message')}")


__all__ = ["approvals_app", "approve_cmd"]
