"""The in-process local runtime (ADR-0002). Development-grade; Temporal is production (Phase 5)."""

from __future__ import annotations

import traceback
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from hawser.clock import monotonic_ms, now
from hawser.config import HawserConfig, RecordMode
from hawser.errors import ApprovalRequired, HawserError, PolicyViolation
from hawser.gateway import Gateway, ModelAdapter, load_registry
from hawser.gateway.anthropic import AnthropicAdapter
from hawser.gateway.openai_compat import OpenAICompatibleAdapter
from hawser.ids import new_id
from hawser.models import (
    Event,
    EventType,
    Revision,
    RunRequest,
    RunResult,
    RunStatus,
    RunSummary,
    Usage,
)
from hawser.recording import Cassette
from hawser.review import ReviewService
from hawser.review.approvals import ApprovalService
from hawser.runtime.artifacts import ArtifactService
from hawser.runtime.base import RunHandle
from hawser.runtime.context import RunContext
from hawser.runtime.direct import set_status
from hawser.runtime.dispatch import execute
from hawser.runtime.events import EventEmitter
from hawser.runtime.state import can_transition, status_for_error, transition
from hawser.runtime.steps import StepExecutor
from hawser.skills.registry import SkillRegistry
from hawser.storage import LocalArtifactStore, RunRecord, SQLiteMetadataStore


class LocalRuntime:
    def __init__(
        self,
        config: HawserConfig | None = None,
        *,
        adapters: dict[str, ModelAdapter] | None = None,
        gateway: Gateway | None = None,
    ) -> None:
        self.config = config or HawserConfig.load()
        home = Path(self.config.home)
        home.mkdir(parents=True, exist_ok=True)
        self.meta = SQLiteMetadataStore(home / "hawser.db")
        self.store = LocalArtifactStore(home / "store")
        self.artifacts = ArtifactService(self.store, self.meta, self.config.tenant_id)
        self.skills = SkillRegistry(list(self.config.skill_paths))
        self.reviews = ReviewService(self.meta, self.config.tenant_id)
        self.approvals = ApprovalService(self.meta, self.config.tenant_id)
        if gateway is not None:
            self.gateway = gateway
        else:
            cassette: Cassette | None = None
            if self.config.record_mode != RecordMode.live:
                cassette = Cassette(
                    Path(self.config.cassette_dir or home / "cassettes"), self.config.record_mode
                )
            self.gateway = Gateway(
                load_registry(),
                adapters or default_adapters(),
                cassette=cassette,
                store_raw=self.config.store_raw,
            )

    # -- Runtime protocol ------------------------------------------------------------------
    def submit(self, request: RunRequest) -> RunHandle:
        tenant = self.config.tenant_id
        if request.idempotency_key:
            existing = self.meta.find_run_by_idempotency_key(tenant, request.idempotency_key)
            if existing is not None:
                return RunHandle(self, existing.id)
        run = RunRecord(
            id=new_id("run"),
            tenant_id=tenant,
            status=RunStatus.queued,
            request=request,
            trace_id=new_id("run"),
            created_at=now(),
            updated_at=now(),
        )
        run.trace_id = run.id
        self.meta.put_run(run)
        emitter = EventEmitter(self.meta, run_id=run.id, tenant_id=tenant)
        emitter.emit(EventType.run_created, {"mode": request.mode.value if request.mode else None})
        self._execute(run, emitter)
        return RunHandle(self, run.id)

    def get(self, run_id: str) -> RunResult | None:
        """The current view: machine result overlaid with the latest reviewed revision."""
        result = self.meta.get_result(run_id)
        if result is None:
            return None
        run = self.meta.get_run(run_id)
        latest = self.reviews.latest_revision(run_id)
        if (
            run is not None
            and run.status != result.status
            and latest is not None
            and latest.kind == "reviewed"
        ):
            return result.model_copy(
                update={"status": run.status, "result": latest.result, "revision_id": latest.id}
            )
        return result

    def events(self, run_id: str, *, after_seq: int = 0) -> Iterator[Event]:
        yield from self.meta.list_events(run_id, after_seq=after_seq)

    def list(self, *, limit: int = 50) -> list[RunSummary]:
        out: list[RunSummary] = []
        for r in self.meta.list_runs(self.config.tenant_id, limit=limit):
            result = self.meta.get_result(r.id)
            out.append(
                RunSummary(
                    id=r.id,
                    tenant_id=r.tenant_id,
                    status=r.status,
                    mode=result.mode if result else r.request.mode,
                    skill=r.request.skill,
                    cost_usd=result.usage.totals.cost_usd if result else 0.0,
                    created_at=r.created_at,
                    completed_at=r.completed_at,
                    metadata=r.request.metadata,
                    artifact_count=len(result.artifacts) if result else 0,
                )
            )
        return out

    def resume(self, run_id: str) -> RunHandle:
        """Re-execute a run paused on ``awaiting_approval``; done steps replay from checkpoints."""
        run = self.meta.get_run(run_id)
        if run is None:
            raise PolicyViolation(f"unknown run {run_id}", code="run_unknown")
        if run.status != RunStatus.awaiting_approval:
            raise PolicyViolation(
                f"run {run_id} is {run.status.value}; only awaiting_approval runs resume",
                code="resume_not_applicable",
            )
        emitter = EventEmitter(self.meta, run_id=run.id, tenant_id=run.tenant_id)
        old = run.status
        run.status = transition(old, RunStatus.running, run_id=run.id)
        run.updated_at = now()
        self.meta.put_run(run)
        emitter.emit(
            EventType.run_status_changed,
            {"from": old.value, "to": RunStatus.running.value, "resume": True},
        )
        self._execute(run, emitter, resuming=True)
        return RunHandle(self, run.id)

    def cancel(self, run_id: str) -> bool:
        # Synchronous execution: by the time a caller holds a run id, the run is finished.
        return False

    def close(self) -> None:
        self.meta.close()

    # -- execution -------------------------------------------------------------------------
    def _execute(
        self, run: RunRecord, emitter: EventEmitter, *, resuming: bool = False
    ) -> RunResult:
        usage = Usage()
        ctx = RunContext(
            config=self.config,
            run=run,
            request=run.request,
            meta=self.meta,
            artifacts=self.artifacts,
            gateway=self.gateway,
            emitter=emitter,
            steps=StepExecutor(self.meta, self.artifacts, emitter, run_id=run.id, usage=usage),
            usage=usage,
            resuming=resuming,
        )
        started = monotonic_ms()
        try:
            result = execute(ctx, self.skills)
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
        run.completed_at = result.completed_at
        run.updated_at = now()
        self.meta.put_run(run)
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
        emitter.emit(
            EventType.result_assembled,
            {"status": result.status.value, "cost_usd": result.usage.totals.cost_usd},
        )
        return result

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
            # A budget stop mid-run routes to review; the state machine only allows that
            # from validating, so pass through it when necessary.
            for inter in (RunStatus.running, RunStatus.validating):
                if can_transition(ctx.run.status, status):
                    break
                if can_transition(ctx.run.status, inter):
                    set_status(ctx, inter)
            set_status(ctx, status)
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


def default_adapters() -> dict[str, ModelAdapter]:
    """First-party adapters keyed by provider. Credentials are resolved lazily per call."""
    return {
        "anthropic": AnthropicAdapter(),
        "openrouter": OpenAICompatibleAdapter.openrouter(),
        "openai": OpenAICompatibleAdapter.openai(),
        "ollama": OpenAICompatibleAdapter.ollama(),
    }


__all__ = ["LocalRuntime", "default_adapters"]
