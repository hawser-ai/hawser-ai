"""``hawser review``: list, show, edit, approve, reject."""

from __future__ import annotations

import json
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.table import Table

from hawser.config import HawserConfig
from hawser.errors import HawserError
from hawser.models.review import ReviewStatus
from hawser.runtime.local import LocalRuntime

review_app = typer.Typer(help="Human review of needs_review runs.", no_args_is_help=True)
console = Console()
err = Console(stderr=True)


def _rt() -> LocalRuntime:
    return LocalRuntime(HawserConfig.load())


def _parse_value(raw: str) -> Any:
    try:
        return json.loads(raw)
    except ValueError:
        return raw


@review_app.command("list")
def review_list(
    status: Annotated[
        str | None, typer.Option("--status", help="open | approved | rejected")
    ] = None,
) -> None:
    """List reviews, plus runs that need review but have no review opened yet."""
    rt = _rt()
    table = Table(title="Reviews")
    for col in ("run", "review", "status", "edits", "version", "updated"):
        table.add_column(col)
    seen: set[str] = set()
    for r in rt.reviews.list(status=ReviewStatus(status) if status else None):
        seen.add(r.run_id)
        table.add_row(
            r.run_id,
            r.id,
            r.status.value,
            str(len(r.decisions)),
            str(r.version),
            r.updated_at.isoformat(timespec="seconds"),
        )
    if status in (None, "open"):
        for s in rt.list(limit=200):
            if s.status.value == "needs_review" and s.id not in seen:
                table.add_row(
                    s.id,
                    "-",
                    "needs_review (not opened)",
                    "0",
                    "-",
                    s.created_at.isoformat(timespec="seconds"),
                )
    console.print(table)


@review_app.command("show")
def review_show(run_id: Annotated[str, typer.Argument()]) -> None:
    """Show the fields with issues, their evidence text, and any edits so far."""
    rt = _rt()
    result = rt.get(run_id)
    if result is None:
        err.print(f"[red]unknown run {run_id}[/]")
        raise typer.Exit(1)
    review = rt.reviews.get(run_id)
    console.print(
        f"run {run_id}: [bold]{result.status.value}[/]"
        + (f"  review {review.id} v{review.version} {review.status.value}" if review else "")
    )
    by_id = {e.id: e for e in result.evidence}
    issues_by_field: dict[str, list[str]] = {}
    for i in result.validation.issues:
        issues_by_field.setdefault(i.field or "(document)", []).append(
            f"{i.severity.value}: {i.message}"
        )
    table = Table(title="Fields needing attention")
    for col in ("field", "current value", "status", "issues", "evidence text"):
        table.add_column(col)
    current = rt.reviews.current_result(run_id)
    for ptr, msgs in sorted(issues_by_field.items()):
        meta = result.field_meta.get(ptr)
        ev_text = ""
        if meta:
            for ev_id in meta.evidence:
                ev_text = getattr(by_id.get(ev_id), "text", "") or ""
        table.add_row(
            ptr,
            json.dumps(_get(current, ptr)),
            meta.status.value if meta else "",
            "\n".join(msgs),
            ev_text,
        )
    console.print(table)
    if review and review.decisions:
        console.print("edits:")
        for d in review.decisions:
            console.print(
                f"  {d.field}: {json.dumps(d.old)} -> {json.dumps(d.new)} "
                f"({d.reviewer}{', ' + d.comment if d.comment else ''})"
            )


def _get(data: Any, ptr: str) -> Any:
    cur = data
    for tok in ptr.split("/")[1:] if ptr else []:
        try:
            cur = cur[int(tok)] if isinstance(cur, list) else cur[tok]
        except (KeyError, IndexError, TypeError, ValueError):
            return None
    return cur


@review_app.command("edit")
def review_edit(
    run_id: Annotated[str, typer.Argument()],
    set_: Annotated[
        list[str], typer.Option("--set", help="POINTER=VALUE (JSON or string); repeatable.")
    ],
    comment: Annotated[str | None, typer.Option("--comment")] = None,
    reviewer: Annotated[str, typer.Option("--reviewer")] = "local",
    version: Annotated[
        int | None,
        typer.Option("--version", help="Expected review version (optimistic concurrency)."),
    ] = None,
) -> None:
    """Record field corrections on the run's review."""
    rt = _rt()
    changes: dict[str, Any] = {}
    for item in set_:
        if "=" not in item:
            err.print(f"[red]expected POINTER=VALUE, got {item!r}[/]")
            raise typer.Exit(2)
        ptr, _, raw = item.partition("=")
        changes[ptr] = _parse_value(raw)
    try:
        review = rt.reviews.edit(
            run_id, changes, reviewer=reviewer, comment=comment, expected_version=version
        )
    except HawserError as exc:
        err.print(f"[red]{exc.code}[/]: {exc.message}")
        raise typer.Exit(1) from None
    console.print(
        f"[green]recorded[/] {len(changes)} edit(s); review {review.id} is now v{review.version}"
    )


@review_app.command("approve")
def review_approve(
    run_id: Annotated[str, typer.Argument()],
    comment: Annotated[str | None, typer.Option("--comment")] = None,
    reviewer: Annotated[str, typer.Option("--reviewer")] = "local",
    version: Annotated[int | None, typer.Option("--version")] = None,
) -> None:
    """Approve: creates a reviewed revision and moves the run to accepted."""
    _decide(run_id, True, comment, reviewer, version)


@review_app.command("reject")
def review_reject(
    run_id: Annotated[str, typer.Argument()],
    comment: Annotated[str | None, typer.Option("--comment")] = None,
    reviewer: Annotated[str, typer.Option("--reviewer")] = "local",
    version: Annotated[int | None, typer.Option("--version")] = None,
) -> None:
    """Reject the run."""
    _decide(run_id, False, comment, reviewer, version)


def _decide(
    run_id: str, approve: bool, comment: str | None, reviewer: str, version: int | None
) -> None:
    rt = _rt()
    try:
        fn = rt.reviews.approve if approve else rt.reviews.reject
        revision = fn(run_id, reviewer=reviewer, comment=comment, expected_version=version)
    except HawserError as exc:
        err.print(f"[red]{exc.code}[/]: {exc.message}")
        raise typer.Exit(1) from None
    view = rt.get(run_id)
    verdict = "approved" if approve else "rejected"
    status = view.status.value if view else "?"
    console.print(f"[green]{verdict}[/] run {run_id} -> {status}; revision {revision.id}")


__all__ = ["review_app"]
