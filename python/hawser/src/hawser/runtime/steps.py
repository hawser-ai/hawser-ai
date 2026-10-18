"""Checkpointed step execution (blueprint §10.2, ADR-0002).

A step is the idempotency boundary. ``run(key, ...)`` returns the stored output when a completed
step with the same key exists for the run, so a resumed run never repeats paid work.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, TypeVar

from pydantic import BaseModel

from hawser.clock import now
from hawser.errors import HawserError
from hawser.ids import new_id
from hawser.models import (
    ArtifactKind,
    ArtifactRef,
    EventType,
    ModelCallUsage,
    Step,
    StepKind,
    StepStatus,
    ToolCallUsage,
    Usage,
)
from hawser.runtime.artifacts import ArtifactService
from hawser.runtime.events import EventEmitter
from hawser.storage import MetadataStore

T = TypeVar("T")


class ExecutionAborted(Exception):
    """Stop executing without recording anything (e.g. a worker lost its lease)."""


class StepHandle:
    """Given to a step function: identity plus usage recording."""

    def __init__(self, step: Step) -> None:
        self.step = step
        self.usage = Usage()

    @property
    def step_id(self) -> str:
        return self.step.id

    def record_model_call(self, usage: ModelCallUsage) -> None:
        usage.step_id = self.step.id
        self.usage.model_calls.append(usage)

    def record_tool_call(self, usage: ToolCallUsage) -> None:
        usage.step_id = self.step.id
        self.usage.tool_calls.append(usage)


class StepExecutor:
    def __init__(
        self,
        meta: MetadataStore,
        artifacts: ArtifactService,
        emitter: EventEmitter,
        *,
        run_id: str,
        usage: Usage,
        before_step: Callable[[], None] | None = None,
        commit_guard: Callable[[], None] | None = None,
    ) -> None:
        self.meta = meta
        self.artifacts = artifacts
        self.emitter = emitter
        self.run_id = run_id
        self.usage = usage
        #: Called before any new (non-checkpointed) step starts: cancel and deadline checks.
        self.before_step = before_step
        #: Called before a finished step is persisted; raise :class:`ExecutionAborted` to
        #: discard it (a worker whose lease was taken over must not overwrite the new owner).
        self.commit_guard = commit_guard

    def run(
        self,
        key: str,
        name: str,
        kind: StepKind,
        fn: Callable[[StepHandle], T],
        *,
        output_type: type[BaseModel] | None = None,
    ) -> T:
        existing = self.meta.get_step(self.run_id, key)
        if existing is not None and existing.status == StepStatus.completed and existing.output_ref:
            self._absorb_usage(existing.usage)
            self.emitter.emit(
                EventType.step_skipped, {"key": key, "reason": "checkpoint"}, step_id=existing.id
            )
            return self._load(existing.output_ref, output_type)  # type: ignore[return-value]

        if self.before_step is not None:
            self.before_step()
        step = Step(
            id=new_id("step"),
            run_id=self.run_id,
            key=key,
            name=name,
            kind=kind,
            status=StepStatus.running,
            attempt=(existing.attempt + 1) if existing else 1,
            started_at=now(),
        )
        self.meta.put_step(step)
        self.emitter.emit(
            EventType.step_started, {"key": key, "name": name, "kind": kind.value}, step_id=step.id
        )
        handle = StepHandle(step)
        try:
            output = fn(handle)
            if self.commit_guard is not None:
                self.commit_guard()
        except ExecutionAborted:
            raise
        except HawserError as exc:
            exc.with_context(run_id=self.run_id, step_id=step.id)
            self._fail(step, exc.to_dict())
            raise
        except Exception as exc:
            self._fail(
                step, {"type": type(exc).__name__, "code": "internal_error", "message": str(exc)}
            )
            raise
        artifact = self.artifacts.put_json(
            output, kind=ArtifactKind.step_output, run_id=self.run_id, step_id=step.id
        )
        step.status = StepStatus.completed
        step.completed_at = now()
        step.output_ref = artifact.ref()
        handle.usage.recompute_totals()
        step.usage = handle.usage
        self.meta.put_step(step)
        self._absorb_usage(handle.usage)
        self.emitter.emit(
            EventType.step_completed,
            {"key": key, "usage": handle.usage.totals.model_dump(mode="json")},
            step_id=step.id,
        )
        return output

    def _fail(self, step: Step, error: dict[str, Any]) -> None:
        step.status = StepStatus.failed
        step.completed_at = now()
        step.error = error
        self.meta.put_step(step)
        self.emitter.emit(EventType.step_failed, {"key": step.key, "error": error}, step_id=step.id)

    def _absorb_usage(self, usage: Usage) -> None:
        self.usage.model_calls.extend(usage.model_calls)
        self.usage.tool_calls.extend(usage.tool_calls)

    def _load(self, ref: ArtifactRef, output_type: type[BaseModel] | None) -> Any:
        raw = self.artifacts.get_bytes(ref.artifact_id)
        if output_type is not None:
            return output_type.model_validate_json(raw)
        return json.loads(raw)


__all__ = ["ExecutionAborted", "StepExecutor", "StepHandle"]
