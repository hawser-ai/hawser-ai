"""The tool broker (blueprint §11): the only path through which a run invokes a tool.

Every call: permission check (skill ∩ request ∩ policy) → input schema validation →
side-effect approval → budget reservation → bounded execution → output validation/stripping →
usage settlement → ``tool.called`` event. Tools receive a ``RunContext`` only when they declare
a ``ctx`` parameter; they never see provider credentials.
"""

from __future__ import annotations

import fnmatch
import hashlib
import inspect
import typing
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from typing import Any

import jsonschema
from pydantic import BaseModel, Field

from hawser.clock import monotonic_ms
from hawser.errors import ApprovalRequired, HawserError, PolicyViolation, ToolError
from hawser.models import (
    ApprovalStatus,
    ArtifactRef,
    EventType,
    Evidence,
    SideEffect,
    ToolCallUsage,
)
from hawser.recording import canonical_json
from hawser.review.approvals import ApprovalService
from hawser.runtime.budget import check_before_tool_call
from hawser.runtime.context import RunContext
from hawser.runtime.steps import StepHandle
from hawser.tools.registry import RegisteredTool, ToolRegistry
from hawser.tools.registry import registry as default_registry


class ToolResult(BaseModel):
    output: Any = None
    usage: ToolCallUsage
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    #: The tool's native return value (e.g. a Pydantic model) for in-process callers.
    raw: Any = Field(default=None, exclude=True)


def _matches(name: str, patterns: list[str]) -> bool:
    return any(name == p or fnmatch.fnmatchcase(name, p) for p in patterns)


class Broker:
    def __init__(self, ctx: RunContext, *, registry: ToolRegistry | None = None) -> None:
        self.ctx = ctx
        self.registry = registry or default_registry
        self.approvals = ApprovalService(ctx.meta, ctx.tenant_id)

    # -- permissions -----------------------------------------------------------------------
    def allowed(self, name: str) -> bool:
        layers: list[list[str] | None] = [
            self.ctx.skill.manifest.tools if self.ctx.skill is not None else None,
            self.ctx.request.tools,
            self.ctx.request.policy.allowed_tools,
        ]
        return all(_matches(name, layer) for layer in layers if layer is not None)

    def spec(self, name: str) -> RegisteredTool:
        tool = self.registry.get(name)
        if not self.allowed(name):
            raise PolicyViolation(
                f"tool {name!r} is not permitted: skill, request and policy must all allow it",
                code="tool_not_allowed",
            )
        return tool

    # -- approvals -------------------------------------------------------------------------
    def _action_id(self, name: str, payload: dict[str, Any], explicit: str | None) -> str:
        if explicit:
            return explicit
        return hashlib.sha256(
            canonical_json({"tool": name, "input": payload}).encode()
        ).hexdigest()[:16]

    def _pre_approved(self, name: str, payload: dict[str, Any], action_id: str) -> bool:
        patterns = self.ctx.request.policy.approvals.pre_approved
        keys = [f"{name}:{action_id}"]
        for k in ("url", "target", "destination", "path"):
            if isinstance(payload.get(k), str):
                keys.append(f"{name}:{payload[k]}")
        return any(_matches(k, patterns) for k in keys)

    def _require_approval(
        self, name: str, payload: dict[str, Any], action_id: str, step_id: str | None
    ) -> None:
        policy = self.ctx.request.policy
        if not policy.approvals.require_for_external_write or self._pre_approved(
            name, payload, action_id
        ):
            return
        approval = self.approvals.find(self.ctx.run_id, name, action_id)
        if approval is None:
            approval = self.approvals.request(
                self.ctx.run_id,
                tool=name,
                action_id=action_id,
                payload=_redact(payload),
                step_id=step_id,
            )
        if approval.status == ApprovalStatus.rejected:
            raise ToolError(
                f"action {action_id} of {name} was rejected by {approval.decided_by}",
                code="action_rejected",
                retryable=False,
                details={"approval_id": approval.id},
            )
        if approval.status == ApprovalStatus.pending:
            raise ApprovalRequired(
                f"{name} needs approval before it may run (approval {approval.id})",
                details={"approval_id": approval.id, "tool": name, "action_id": action_id},
            )

    # -- call ------------------------------------------------------------------------------
    def call(
        self,
        name: str,
        payload: dict[str, Any] | None = None,
        *,
        handle: StepHandle | None = None,
        action_id: str | None = None,
    ) -> ToolResult:
        payload = dict(payload or {})
        tool = self.spec(name)
        spec = tool.spec
        step_id = handle.step_id if handle else None

        if spec.input_schema.get("properties") or spec.input_schema.get("required"):
            errors = sorted(
                jsonschema.Draft202012Validator(spec.input_schema).iter_errors(payload),
                key=lambda e: list(e.absolute_path),
            )
            if errors:
                raise ToolError(
                    f"invalid input for {name}: {errors[0].message}",
                    code="tool_input_invalid",
                    retryable=False,
                    details={"errors": [e.message for e in errors[:5]]},
                )

        aid = self._action_id(name, payload, action_id)
        if spec.side_effect == SideEffect.external_write:
            self._require_approval(name, payload, aid, step_id)

        est_cost = spec.cost_estimate.fixed_usd if spec.cost_estimate else 0.0
        check_before_tool_call(self.ctx.usage, self.ctx.request.policy, est_cost)

        kwargs = _coerce(tool.fn, dict(payload))
        params = inspect.signature(tool.fn).parameters
        if "ctx" in params:
            kwargs["ctx"] = self.ctx
        if "action_id" in params and "action_id" not in kwargs:
            kwargs["action_id"] = aid

        started = monotonic_ms()
        raw = self._run_bounded(name, tool, kwargs, spec.limits.wall_s)
        latency = monotonic_ms() - started

        output = raw.model_dump(mode="json") if isinstance(raw, BaseModel) else raw
        output = self._validate_output(name, spec.output_schema, output)
        usage = ToolCallUsage(
            tool=name, version=spec.version, cost_usd=est_cost, latency_ms=latency
        )
        if handle is not None:
            handle.record_tool_call(usage)
        else:
            self.ctx.usage.tool_calls.append(usage)
        self.ctx.emitter.emit(
            EventType.tool_called,
            {
                "tool": name,
                "version": spec.version,
                "side_effect": spec.side_effect.value,
                "action_id": aid if spec.side_effect != SideEffect.none else None,
                "latency_ms": latency,
                "cost_usd": est_cost,
            },
            step_id=step_id,
        )
        return ToolResult(output=output, usage=usage, raw=raw)

    def _run_bounded(
        self, name: str, tool: RegisteredTool, kwargs: dict[str, Any], wall_s: float | None
    ) -> Any:
        if wall_s is None:
            try:
                return tool.fn(**kwargs)
            except HawserError:
                raise
            except Exception as exc:
                raise ToolError(
                    f"{name} failed: {exc}",
                    code="tool_failed",
                    retryable=tool.spec.retry.value != "never",
                ) from exc
        pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"tool-{name}")
        future = pool.submit(tool.fn, **kwargs)
        try:
            return future.result(timeout=wall_s)
        except FutureTimeout as exc:
            raise ToolError(
                f"{name} exceeded its wall-time limit of {wall_s}s",
                code="tool_timeout",
                retryable=False,
            ) from exc
        except HawserError:
            raise
        except Exception as exc:
            raise ToolError(
                f"{name} failed: {exc}",
                code="tool_failed",
                retryable=tool.spec.retry.value != "never",
            ) from exc
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

    @staticmethod
    def _validate_output(name: str, schema: dict[str, Any], output: Any) -> Any:
        props = schema.get("properties")
        if not props or not isinstance(output, dict):
            return output
        if schema.get("additionalProperties") is False:
            output = {k: v for k, v in output.items() if k in props}  # type: ignore[union-attr]
        errors = list(jsonschema.Draft202012Validator(schema).iter_errors(output))
        if errors:
            raise ToolError(
                f"{name} returned invalid output: {errors[0].message}",
                code="tool_output_invalid",
                retryable=False,
            )
        return output


def _coerce(fn: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """Rebuild Pydantic-typed parameters from JSON dicts (tool inputs cross a JSON boundary)."""
    try:
        hints = typing.get_type_hints(fn)
    except Exception:
        return payload
    out = dict(payload)
    for name, value in payload.items():
        hint = hints.get(name)
        if isinstance(value, dict) and isinstance(hint, type) and issubclass(hint, BaseModel):
            out[name] = hint.model_validate(value)
    return out


def _redact(payload: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in payload.items():
        if any(s in k.lower() for s in ("token", "secret", "password", "authorization", "api_key")):
            out[k] = "***"
        elif isinstance(v, str) and len(v) > 2000:
            out[k] = v[:2000] + "…"
        else:
            out[k] = v
    return out


__all__ = ["Broker", "ToolResult"]
