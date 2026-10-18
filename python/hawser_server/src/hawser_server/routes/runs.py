from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header, Query, Request, Response
from fastapi.responses import StreamingResponse

from hawser.models import (
    TERMINAL_STATUSES,
    Event,
    PathInput,
    Revision,
    RunRequest,
    RunResult,
    RunStatus,
    RunSummary,
    Step,
)
from hawser_server.deps import PrincipalDep, ServicesDep, owned_run
from hawser_server.errors import BadRequest, Conflict, NotFound
from hawser_server.schemas import CancelResponse, RunPage, UsageReport
from hawser_server.sse import stream_run_events
from hawser_server.util import decode_cursor, encode_cursor, ts

router = APIRouter(prefix="/v1", tags=["runs"])


@router.post("/runs", response_model=RunSummary, status_code=201)
def create_run(
    svc: ServicesDep,
    who: PrincipalDep,
    request: RunRequest,
    response: Response,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> RunSummary:
    """Create a run and queue it. ``Idempotency-Key`` returns the original run on a repeat."""
    if any(isinstance(i, PathInput) for i in request.inputs):
        raise BadRequest(
            "path inputs are local to the SDK; upload the file with POST /v1/assets first",
            code="path_inputs_unsupported",
        )
    if idempotency_key:
        if request.idempotency_key and request.idempotency_key != idempotency_key:
            raise BadRequest(
                "Idempotency-Key header and body idempotency_key differ",
                code="idempotency_mismatch",
            )
        request = request.model_copy(update={"idempotency_key": idempotency_key})
    run, created = svc.engine.create_run(request, tenant_id=who.tenant_id)
    if created:
        svc.queue.enqueue(run.id, run.tenant_id)
    else:
        response.status_code = 200
    response.headers["Location"] = f"/v1/runs/{run.id}"
    return svc.engine.summary(run)


@router.get("/runs", response_model=RunPage)
def list_runs(
    svc: ServicesDep,
    who: PrincipalDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: str | None = None,
    status: RunStatus | None = None,
) -> RunPage:
    before = decode_cursor(cursor) if cursor else None
    if cursor and before is None:
        raise BadRequest("malformed cursor", code="bad_cursor")
    runs = svc.meta.list_runs(
        who.tenant_id, limit=limit + 1, before=before, status=status.value if status else None
    )
    page = runs[:limit]
    nxt = encode_cursor(page[-1].created_at.isoformat(), page[-1].id) if len(runs) > limit else None
    return RunPage(items=[svc.engine.summary(r) for r in page], next=nxt)


@router.get("/runs/{run_id}", response_model=RunSummary)
def get_run(svc: ServicesDep, who: PrincipalDep, run_id: str) -> RunSummary:
    return svc.engine.summary(owned_run(svc, who, run_id))


@router.get("/runs/{run_id}/result", response_model=RunResult)
def get_result(svc: ServicesDep, who: PrincipalDep, run_id: str) -> RunResult:
    run = owned_run(svc, who, run_id)
    result = svc.engine.view(run.id)
    if result is None:
        raise NotFound(
            f"run {run_id} has no result yet (status {run.status.value})",
            code="result_not_ready",
            details={"status": run.status.value},
        )
    return result


@router.get("/runs/{run_id}/events", response_model=list[Event])
def get_events(  # noqa: PLR0917 - FastAPI injects each parameter
    svc: ServicesDep,
    who: PrincipalDep,
    run_id: str,
    request: Request,
    after_seq: Annotated[int, Query(ge=0)] = 0,
    stream: bool = False,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> list[Event] | StreamingResponse:
    """The event log as JSON, or as SSE with ``Accept: text/event-stream`` / ``?stream=true``."""
    run = owned_run(svc, who, run_id)
    wants_sse = stream or "text/event-stream" in request.headers.get("accept", "")
    if last_event_id and last_event_id.isdigit():
        after_seq = max(after_seq, int(last_event_id))
    if not wants_sse:
        return svc.meta.list_events(run.id, after_seq=after_seq)
    gen = stream_run_events(
        svc,
        run.id,
        after_seq=after_seq,
        poll_s=svc.config.poll_interval_s,
        keepalive_s=svc.config.sse_keepalive_s,
    )
    return StreamingResponse(
        gen,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/runs/{run_id}/steps", response_model=list[Step])
def get_steps(svc: ServicesDep, who: PrincipalDep, run_id: str) -> list[Step]:
    return svc.meta.list_steps(owned_run(svc, who, run_id).id)


@router.get("/runs/{run_id}/revisions", response_model=list[Revision])
def get_revisions(svc: ServicesDep, who: PrincipalDep, run_id: str) -> list[Revision]:
    return svc.meta.list_revisions(owned_run(svc, who, run_id).id)


@router.post("/runs/{run_id}/cancel", response_model=CancelResponse)
def cancel_run(svc: ServicesDep, who: PrincipalDep, run_id: str) -> CancelResponse:
    """Cancel: immediately when nothing is executing, otherwise at the next step boundary."""
    run = owned_run(svc, who, run_id)
    if run.status in TERMINAL_STATUSES:
        return CancelResponse(run_id=run.id, cancelled=False, status=run.status)
    job = svc.queue.get(run.id)
    executing = job is not None and job.state == "leased"
    if not executing:
        svc.queue.remove(run.id)
        result = svc.engine.abandon(run, status=RunStatus.cancelled, error=None)
        svc.webhooks.fan_out(
            run.tenant_id,
            "run.completed",
            {"run_id": run.id, "status": result.status.value},
            run_id=run.id,
        )
        return CancelResponse(run_id=run.id, cancelled=True, status=result.status)
    svc.meta.request_cancel(run.id, run.tenant_id)
    return CancelResponse(run_id=run.id, cancelled=True, status=run.status)


@router.post("/runs/{run_id}/resume", response_model=RunSummary)
def resume_run(svc: ServicesDep, who: PrincipalDep, run_id: str) -> RunSummary:
    """Re-queue a run paused on ``awaiting_approval`` (after its approvals were decided)."""
    run = owned_run(svc, who, run_id)
    job = svc.queue.get(run.id)
    if job is not None and job.state in ("pending", "leased") and run.status == RunStatus.running:
        return svc.engine.summary(run)  # already queued by an approval decision
    if run.status != RunStatus.awaiting_approval:
        raise Conflict(
            f"run {run_id} is {run.status.value}; only awaiting_approval runs resume",
            code="resume_not_applicable",
        )
    run = svc.engine.begin_resume(run.id)
    svc.queue.enqueue(run.id, run.tenant_id, resume=True)
    return svc.engine.summary(run)


@router.get("/usage", response_model=UsageReport, tags=["usage"])
def usage(svc: ServicesDep, who: PrincipalDep, since: str | None = None) -> UsageReport:
    """Totals over this tenant's runs (optionally those created at or after ``since``)."""
    runs = svc.meta.list_runs(who.tenant_id, limit=10_000)
    report = UsageReport(
        tenant_id=who.tenant_id,
        since=since,
        runs=0,
        model_calls=0,
        input_tokens=0,
        output_tokens=0,
        cost_usd=0.0,
    )
    for r in runs:
        if since and ts(r.created_at) < since:
            continue
        result = svc.meta.get_result(r.id)
        report.runs += 1
        if result is None:
            continue
        t = result.usage.totals
        report.model_calls += t.model_calls
        report.input_tokens += t.input_tokens
        report.output_tokens += t.output_tokens
        report.cost_usd += t.cost_usd
    return report
