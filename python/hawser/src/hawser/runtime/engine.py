"""The execution engine: one code path for the local runtime and the server worker (ADR-0010).

An ``Engine`` owns no process model. It creates run records, executes a run to its next resting
point (terminal, ``needs_review`` or ``awaiting_approval``), and assembles views. Whoever calls it
decides *when* and *where*: ``LocalRuntime`` calls it inline; ``hawserd worker`` calls it from a
leased job. Durability comes from the stores: every step is checkpointed, so re-executing a run
with ``resuming=True`` replays finished steps instead of repeating paid work.
"""

from __future__ import annotations

import time
import traceback
from collections.abc import Callable
from typing import Any

from hawser.clock import monotonic_ms, now
from hawser.config import HawserConfig
from hawser.errors import ApprovalRequired, BudgetExceeded, Cancelled, HawserError, PolicyViolation
from hawser.gateway import Gateway
from hawser.ids import new_id
from hawser.models import (
    EventType,
    Revision,
    RunRequest,
    RunResult,
    RunStatus,
    RunSummary,
    Severity,
    Usage,
)
from hawser.review import ReviewService
from hawser.runtime.artifacts import ArtifactService
from hawser.runtime.context import RunContext
from hawser.runtime.direct import set_status
from hawser.runtime.dispatch import execute
from hawser.runtime.events import EventEmitter
from hawser.runtime.state import OUTCOME_STATUSES, status_for_error, status_path, transition
from hawser.runtime.steps import ExecutionAborted, StepExecutor
from hawser.skills.registry import SkillRegistry
from hawser.storage import ArtifactStore, MetadataStore, RunRecord

ObjectReader = Callable[[str], bytes]


class Engine:
    def __init__(
        self,
        config: HawserConfig,
        *,
        meta: MetadataStore,
        store: ArtifactStore,
        gateway: Gateway,
        skills: SkillRegistry,
        object_reader: ObjectReader | None = None,
    ) -> None:
        self.config = config
        self.meta = meta
        self.store = store
        self.gateway = gateway
        self.skills = skills
        #: Resolves ``s3://`` style inputs; ``None`` means such inputs are rejected at admission.
        self.object_reader = object_reader

    # -- records ---------------------------------------------------------------------------
    def artifacts_for(self, tenant_id: str) -> ArtifactService:
        return ArtifactService(self.store, self.meta, tenant_id)

    def create_run(self, request: RunRequest, *, tenant_id: str) -> tuple[RunRecord, bool]:
        """Create the run record (or return the idempotent original). ``True`` when created."""
        if request.idempotency_key:
            existing = self.meta.find_run_by_idempotency_key(tenant_id, request.idempotency_key)
            if existing is not None:
                return existing, False
        run_id = new_id("run")
        run = RunRecord(
            id=run_id,
            tenant_id=tenant_id,
            status=RunStatus.queued,
            request=request,
            trace_id=run_id,
            created_at=now(),
            updated_at=now(),
        )
        self.meta.put_run(run)
        self.meta.append_event(
            run_id=run.id,
            tenant_id=tenant_id,
            type=EventType.run_created,
            payload={"mode": request.mode.value if request.mode else None},
        )
        return run, True

    def begin_resume(self, run_id: str) -> RunRecord:
        """Move an ``awaiting_approval`` run back to ``running`` (the caller then executes)."""
        run = self.meta.get_run(run_id)
        if run is None:
            raise PolicyViolation(f"unknown run {run_id}", code="run_unknown")
        if run.status != RunStatus.awaiting_approval:
            raise PolicyViolation(
                f"run {run_id} is {run.status.value}; only awaiting_approval runs resume",
                code="resume_not_applicable",
            )
        old = run.status
        run.status = transition(old, RunStatus.running, run_id=run.id)
        run.updated_at = now()
        self.meta.put_run(run)
        self.meta.append_event(
            run_id=run.id,
            tenant_id=run.tenant_id,
            type=EventType.run_status_changed,
            payload={"from": old.value, "to": RunStatus.running.value, "resume": True},
        )
        return run

    # -- execution -------------------------------------------------------------------------
    def execute(
        self,
        run: RunRecord,
        *,
        resuming: bool = False,
        guard: Callable[[], None] | None = None,
    ) -> RunResult:
        """Run to the next resting point and persist the result, revision and final event.

        ``guard`` is called before every new step (after the cancel and deadline checks); a
        worker uses it to abort when its lease was lost.
        """
        emitter = EventEmitter(self.meta, run_id=run.id, tenant_id=run.tenant_id)
        usage = Usage()
        artifacts = self.artifacts_for(run.tenant_id)
        started = monotonic_ms()
        deadline = self._deadline(run)
        steps = StepExecutor(
            self.meta,
            artifacts,
            emitter,
            run_id=run.id,
            usage=usage,
            before_step=lambda: self._check_signals(run, deadline, guard),
            commit_guard=guard,
        )
        ctx = RunContext(
            config=self.config,
            run=run,
            request=run.request,
            meta=self.meta,
            artifacts=artifacts,
            gateway=self.gateway,
            emitter=emitter,
            steps=steps,
            usage=usage,
            resuming=resuming,
            object_reader=self.object_reader,
        )
        try:
            self._check_signals(run, deadline, guard)
            result = execute(ctx, self.skills)
        except ExecutionAborted:
            raise
        except ApprovalRequired as exc:
            exc.with_context(run_id=run.id)
            result = self._pause(ctx, exc)
        except HawserError as exc:
            exc.with_context(run_id=run.id)
            status = status_for_error(exc, review_on_failure=run.request.policy.review_on_failure)
            result = self._finish_with_error(ctx, status, exc.to_dict())
        except Exception as exc:
            error: dict[str, Any] = {
                "type": type(exc).__name__,
                "code": "internal_error",
                "message": str(exc),
                "traceback": traceback.format_exc(limit=8),
            }
            result = self._finish_with_error(ctx, RunStatus.failed, error)
        result.usage.totals.wall_time_ms = monotonic_ms() - started
        return self._persist(run, result, emitter, ctx.pending_transitions)

    def _persist(
        self,
        run: RunRecord,
        result: RunResult,
        emitter: EventEmitter,
        pending: list[tuple[RunStatus, RunStatus]],
    ) -> RunResult:
        """Store the revision and result, then publish the run's outcome status and events.

        The run record (and so the status every reader sees) is written last: a reader that
        sees ``accepted`` or ``needs_review`` can always fetch the result.
        """
        run.completed_at = result.completed_at
        run.updated_at = now()
        machine = Revision(
            id=new_id("rev"),
            run_id=run.id,
            tenant_id=run.tenant_id,
            kind="machine",
            result=result.result,
            field_meta=result.field_meta,
            author="system",
            created_at=now(),
        )
        self.meta.put_revision(machine)
        result.revision_id = machine.id
        self.meta.put_result(result)
        self.meta.put_run(run)
        for old, new in pending:
            emitter.emit(EventType.run_status_changed, {"from": old.value, "to": new.value})
        emitter.emit(
            EventType.result_assembled,
            {"status": result.status.value, "cost_usd": result.usage.totals.cost_usd},
        )
        return result

    def _deadline(self, run: RunRecord) -> float | None:
        limit = run.request.policy.limits.max_wall_time_s
        return None if limit is None else time.monotonic() + limit

    def _check_signals(
        self, run: RunRecord, deadline: float | None, guard: Callable[[], None] | None
    ) -> None:
        if self.meta.cancel_requested(run.id):
            raise Cancelled("the run was cancelled", code="cancelled", run_id=run.id)
        if deadline is not None and time.monotonic() > deadline:
            raise BudgetExceeded(
                "the run exceeded its wall-time limit",
                code="max_wall_time",
                details={"max_wall_time_s": run.request.policy.limits.max_wall_time_s},
                run_id=run.id,
            )
        if guard is not None:
            guard()

    def abandon(
        self, run: RunRecord, *, status: RunStatus, error: dict[str, Any] | None
    ) -> RunResult:
        """Finish a run that is not executing (cancel before start, exhausted worker attempts)."""
        emitter = EventEmitter(self.meta, run_id=run.id, tenant_id=run.tenant_id)
        pending: list[tuple[RunStatus, RunStatus]] = []
        if run.status != status:
            for inter in status_path(run.status, status):
                if inter in OUTCOME_STATUSES:
                    old, run.status = run.status, transition(run.status, inter, run_id=run.id)
                    pending.append((old, inter))
                else:
                    self._move(run, inter, emitter)
        run.error = error
        existing = self.meta.get_result(run.id)
        result = RunResult(
            run_id=run.id,
            status=status,
            mode=run.request.mode,
            result=None,
            artifacts=list(existing.artifacts) if existing else [],
            usage=existing.usage if existing else Usage(),
            trace_id=run.trace_id,
            error=error,
            warnings=list(existing.warnings) if existing else [],
            created_at=run.created_at,
            completed_at=now(),
        )
        return self._persist(run, result, emitter, pending)

    def _move(self, run: RunRecord, new: RunStatus, emitter: EventEmitter) -> None:
        old = run.status
        run.status = transition(old, new, run_id=run.id)
        run.updated_at = now()
        self.meta.put_run(run)
        emitter.emit(EventType.run_status_changed, {"from": old.value, "to": new.value})

    def _pause(self, ctx: RunContext, exc: ApprovalRequired) -> RunResult:
        """Park the run on awaiting_approval; the result carries the pending approval."""
        set_status(ctx, RunStatus.awaiting_approval)
        ctx.usage.recompute_totals()
        ctx.warn("approval_pending", exc.message, **exc.details)
        return RunResult(
            run_id=ctx.run_id,
            status=RunStatus.awaiting_approval,
            mode=ctx.request.mode,
            artifacts=list(ctx.artifact_refs),
            usage=ctx.usage,
            trace_id=ctx.run.trace_id,
            warnings=list(ctx.warnings),
            created_at=ctx.run.created_at,
            completed_at=None,
        )

    def _finish_with_error(
        self, ctx: RunContext, status: RunStatus, error: dict[str, Any]
    ) -> RunResult:
        if ctx.run.status != status:
            # A budget stop before or mid-run routes to review; the state machine only allows
            # that from validating, so walk the shortest legal path there.
            for inter in status_path(ctx.run.status, status):
                set_status(ctx, inter)
        ctx.run.error = error
        ctx.usage.recompute_totals()
        return RunResult(
            run_id=ctx.run_id,
            status=status,
            mode=ctx.request.mode,
            result=None,
            artifacts=list(ctx.artifact_refs),
            usage=ctx.usage,
            trace_id=ctx.run.trace_id,
            error=error,
            warnings=list(ctx.warnings),
            created_at=ctx.run.created_at,
            completed_at=now(),
        )

    # -- views -----------------------------------------------------------------------------
    def view(self, run_id: str) -> RunResult | None:
        """The current result: machine result overlaid with the latest reviewed revision."""
        result = self.meta.get_result(run_id)
        if result is None:
            return None
        run = self.meta.get_run(run_id)
        if run is None:
            return result
        latest = ReviewService(self.meta, run.tenant_id).latest_revision(run_id)
        if run.status != result.status and latest is not None and latest.kind == "reviewed":
            # A reviewer's decision settles the machine's error-severity issues: they stay in
            # the report for the record, downgraded to ``info`` and marked resolved.
            resolved = [
                i.model_copy(
                    update={
                        "severity": Severity.info,
                        "details": {**i.details, "resolved_by": latest.id},
                    }
                )
                if i.severity == Severity.error
                else i
                for i in result.validation.issues
            ]
            report = result.validation.model_copy(update={"issues": resolved})
            return result.model_copy(
                update={
                    "status": run.status,
                    "result": latest.result,
                    "revision_id": latest.id,
                    "validation": report,
                }
            )
        if run.status != result.status:
            return result.model_copy(update={"status": run.status})
        return result

    def summary(self, run: RunRecord) -> RunSummary:
        result = self.meta.get_result(run.id)
        return RunSummary(
            id=run.id,
            tenant_id=run.tenant_id,
            status=run.status,
            mode=result.mode if result else run.request.mode,
            skill=run.request.skill,
            cost_usd=result.usage.totals.cost_usd if result else 0.0,
            created_at=run.created_at,
            completed_at=run.completed_at,
            metadata=run.request.metadata,
            artifact_count=len(result.artifacts) if result else 0,
        )

    def summaries(
        self,
        tenant_id: str,
        *,
        limit: int = 50,
        before: tuple[str, str] | None = None,
        status: str | None = None,
    ) -> list[RunSummary]:
        return [
            self.summary(r)
            for r in self.meta.list_runs(tenant_id, limit=limit, before=before, status=status)
        ]


__all__ = ["Engine", "ExecutionAborted", "ObjectReader"]
