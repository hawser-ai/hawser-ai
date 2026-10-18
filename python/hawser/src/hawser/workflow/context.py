"""The object a workflow's ``run(ctx)`` receives.

Every model or tool call goes through ``ctx.step``/``ctx.extract`` so it is checkpointed by
key: a resumed run skips completed steps. Evidence from extractions is attached to the final
result by pointer prefix; derived values get calculation evidence linking their inputs.
"""

from __future__ import annotations

import inspect
import json
import typing
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from typing import Any, TypeVar

from pydantic import BaseModel

from hawser.errors import PlanInvalid, PolicyViolation
from hawser.ids import new_id
from hawser.models import (
    Asset,
    CalculationEvidence,
    Evidence,
    FieldMeta,
    FieldStatus,
    Issue,
    ModelSpec,
    ProducerRef,
    RepairKind,
    Severity,
    StepKind,
)
from hawser.models.common import JsonSchema
from hawser.runtime.context import DocumentContext, RunContext
from hawser.runtime.extract import Extraction, extract_structured
from hawser.runtime.steps import StepHandle
from hawser.skills.bundle import Skill
from hawser.structured import inline_refs, to_json_schema
from hawser.tools.broker import Broker
from hawser.validation.core import make_issue

T = TypeVar("T")

_WORKFLOW_PRODUCER = ProducerRef(kind="tool", name="workflow.derive", version="1")


class WorkflowOutput(BaseModel):
    """What the runtime assembles after ``run(ctx)`` returns."""

    result: Any
    field_meta: dict[str, FieldMeta]
    evidence: list[Evidence]
    issues: list[Issue]


class WorkflowContext:
    def __init__(
        self,
        run_ctx: RunContext,
        skill: Skill,
        documents: dict[str, list[DocumentContext]],
        spec: ModelSpec,
        *,
        candidates: list[str] | None = None,
        prefix: str = "",
    ) -> None:
        self._ctx = run_ctx
        self._prefix = f"{prefix}/" if prefix and prefix != "workflow" else ""
        self.candidates = candidates or [spec.id]
        self.broker = Broker(run_ctx)
        self._tool_calls: list[str] = []
        self.skill = skill
        self._documents = documents
        self.model = spec
        self._field_meta: dict[str, FieldMeta] = {}
        self._evidence: list[Evidence] = []
        self._issues: list[Issue] = []
        self._extractions: dict[str, Extraction] = {}

    # -- inputs ----------------------------------------------------------------------------
    @property
    def inputs(self) -> dict[str, Asset]:
        return {name: docs[0].asset for name, docs in self._documents.items() if docs}

    def documents(self, name: str) -> list[DocumentContext]:
        if name not in self._documents:
            raise PlanInvalid(f"workflow input {name!r} was not provided", code="input_missing")
        return self._documents[name]

    @property
    def policy(self) -> Any:
        return self._ctx.request.policy

    def log(self, message: str, **details: Any) -> None:
        self._ctx.warn("workflow_note", message, **details) if details.get("warn") else None

    # -- steps -----------------------------------------------------------------------------
    def step(
        self,
        key: str,
        fn: Callable[..., T],
        *args: Any,
        output_type: type[BaseModel] | None = None,
        kind: StepKind = StepKind.compute,
        **kwargs: Any,
    ) -> T:
        """Run ``fn(*args, **kwargs)`` as a checkpointed step. Output must be JSON/pydantic.

        Registered tools go through the broker (permissions, schemas, approvals, limits).
        """
        output_type = output_type or _return_model(fn)
        name = getattr(fn, "hawser_tool_name", None)
        if name:
            payload = _bind(fn, args, kwargs)

            def via_broker(h: StepHandle) -> Any:
                result = self.broker.call(name, payload, handle=h)
                return result.raw if result.raw is not None else result.output

            out = self._ctx.steps.run(
                self._prefix + key, key, StepKind.tool, via_broker, output_type=output_type
            )
        else:
            self._check_tool(fn)
            out = self._ctx.steps.run(
                self._prefix + key,
                key,
                kind,
                lambda _h: fn(*args, **kwargs),
                output_type=output_type,
            )
        if output_type is not None and isinstance(out, dict):
            return output_type.model_validate(out)  # type: ignore[return-value]
        return out  # type: ignore[return-value]

    def tool(
        self, name: str, *, key: str | None = None, action_id: str | None = None, **payload: Any
    ) -> Any:
        """Call a registered tool by name through the broker, as a checkpointed step."""
        step_key = key or f"tool:{name}:{len(self._tool_calls)}"
        self._tool_calls.append(step_key)
        return self._ctx.steps.run(
            self._prefix + step_key,
            name,
            StepKind.tool,
            lambda h: self.broker.call(name, payload, handle=h, action_id=action_id).output,
        )

    def extract(
        self,
        name: str,
        schema: type[BaseModel] | JsonSchema,
        instruction: str | None = None,
        *,
        key: str | None = None,
        prompt: str | None = None,
    ) -> Extraction:
        """Schema-guided extraction over the documents bound to input ``name``."""
        self._check_tool_name("document.extract")
        docs = self.documents(name)
        inlined = inline_refs(to_json_schema(schema))
        text = (
            instruction
            or self.skill.manifest.instruction
            or f"Extract the {name.replace('_', ' ')}."
        )
        system = self.skill.prompt(prompt) if prompt else None
        extraction = extract_structured(
            self._ctx,
            docs,
            schema=inlined,
            instruction=text,
            spec=self.model,
            prefix=self._prefix + (key or f"extract:{name}"),
            system_prompt=system,
            candidates=self.candidates,
        )
        self._extractions[key or f"extract:{name}"] = extraction
        return extraction

    def parallel(
        self, key: str, fns: list[Callable[[], T]], *, max_workers: int | None = None
    ) -> list[T]:
        limit = self._ctx.request.policy.limits.max_parallel
        workers = max_workers or limit or 4
        if limit is not None and workers > limit:
            raise PolicyViolation(
                f"parallelism {workers} exceeds policy limit {limit}", code="parallel_limit"
            )

        def call(f: Callable[[], T]) -> T:
            return f()

        with ThreadPoolExecutor(max_workers=workers) as pool:
            return list(pool.map(call, fns))

    def loop(self, key: str, max_iterations: int | None = None) -> Iterator[int]:
        limit = max_iterations or self._ctx.request.policy.limits.max_loop_iterations or 100
        yield from range(limit)
        self._ctx.warn("loop_limit", f"loop {key!r} reached its limit of {limit} iterations")

    # -- evidence --------------------------------------------------------------------------
    def attach(self, prefix: str, extraction: Extraction) -> None:
        """Expose an extraction's fields under ``prefix`` in the final result (``/invoice``)."""
        prefix = prefix.rstrip("/")
        for ptr, meta in extraction.field_meta.items():
            self._field_meta[f"{prefix}{ptr}"] = meta.model_copy(deep=True)
        self._evidence.extend(extraction.evidence)

    def derive(
        self, pointer: str, *, expression: str, inputs: list[str], value: Any = None
    ) -> None:
        """Record that ``pointer`` was computed deterministically from ``inputs`` (pointers)."""
        input_ids: list[str] = []
        asset_id: str | None = None
        for p in inputs:
            meta = self._field_meta.get(p)
            if meta and meta.evidence:
                input_ids.extend(meta.evidence)
        by_id = {e.id: e for e in self._evidence}
        for i in input_ids:
            if i in by_id:
                asset_id = by_id[i].asset_id
                break
        if asset_id is None:
            first = next(iter(self.inputs.values()), None)
            asset_id = first.id if first else None
        if asset_id is None:
            return
        ev = CalculationEvidence(
            id=new_id("ev"),
            asset_id=asset_id,
            producer=_WORKFLOW_PRODUCER,
            expression=expression,
            inputs=input_ids,
            value=value
            if isinstance(value, int | float | str | bool) or value is None
            else json.dumps(value),
            confidence=1.0,
        )
        self._evidence.append(ev)
        self._field_meta[pointer] = FieldMeta(
            status=FieldStatus.derived, evidence=[ev.id], anchor_confidence=1.0
        )

    def mark(self, pointer: str, status: FieldStatus = FieldStatus.derived) -> None:
        self._field_meta.setdefault(pointer, FieldMeta(status=status))

    def flag(
        self,
        rule_id: str,
        message: str,
        *,
        severity: Severity = Severity.warning,
        field: str | None = None,
        repair: RepairKind = RepairKind.review,
        evidence: list[str] | None = None,
        **details: Any,
    ) -> None:
        """Raise a validation finding from workflow logic (e.g. an unmatched line item)."""
        self._issues.append(
            make_issue(
                rule_id,
                severity,
                message,
                field_ptr=field,
                repair=repair,
                evidence=evidence,
                **details,
            )
        )

    # -- assembly --------------------------------------------------------------------------
    def assemble(self, result: Any) -> WorkflowOutput:
        if isinstance(result, BaseModel):
            result = result.model_dump(mode="json")
        return WorkflowOutput(
            result=result,
            field_meta=dict(self._field_meta),
            evidence=list(self._evidence),
            issues=list(self._issues),
        )

    # -- permissions -----------------------------------------------------------------------
    def _check_tool(self, fn: Callable[..., Any]) -> None:
        name = getattr(fn, "hawser_tool_name", None)
        if name:
            self._check_tool_name(name)

    def _check_tool_name(self, name: str) -> None:
        allowed = (
            self._ctx.request.tools
            if self._ctx.request.tools is not None
            else self.skill.manifest.tools
        )
        if (
            allowed
            and name not in allowed
            and not any(name.startswith(a[:-1]) for a in allowed if a.endswith("*"))
        ):
            raise PolicyViolation(
                f"tool {name!r} is not permitted by the skill manifest / request",
                code="tool_not_allowed",
            )


def _return_model(fn: Callable[..., Any]) -> type[BaseModel] | None:
    try:
        hints = typing.get_type_hints(fn)
    except Exception:
        return None
    ret = hints.get("return")
    return ret if isinstance(ret, type) and issubclass(ret, BaseModel) else None


def _bind(fn: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
    bound = inspect.signature(fn).bind_partial(*args, **kwargs)
    payload = {k: v for k, v in bound.arguments.items() if k != "ctx"}
    return {
        k: (v.model_dump(mode="json") if isinstance(v, BaseModel) else v)
        for k, v in payload.items()
    }


__all__ = ["WorkflowContext", "WorkflowOutput"]
