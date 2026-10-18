"""Error taxonomy (Phase 0, WP0.4).

Every error carries a stable ``code`` (for programmatic handling and problem+json), a human
``message``, structured ``details``, a ``retryable`` flag, and the ``run_id``/``step_id`` it
occurred in when known. The mapping from error class to run status lives in
:mod:`hawser.runtime.state`.

Note that *validation failure* is not an error: it is a run outcome (``needs_review`` or
``rejected``) decided by policy. Only infrastructure, admission, policy and planning problems
raise.
"""

from __future__ import annotations

from typing import Any, ClassVar, Self


class HawserError(Exception):
    """Base class for all Hawser errors."""

    #: Default stable code for the class; instances may narrow it (e.g. ``pdf_encrypted``).
    default_code: ClassVar[str] = "hawser_error"
    #: Whether a retry of the same operation could plausibly succeed.
    retryable_default: ClassVar[bool] = False

    code: str
    message: str
    details: dict[str, Any]
    retryable: bool
    run_id: str | None
    step_id: str | None

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        details: dict[str, Any] | None = None,
        retryable: bool | None = None,
        run_id: str | None = None,
        step_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code or type(self).default_code
        self.details = dict(details or {})
        self.retryable = type(self).retryable_default if retryable is None else retryable
        self.run_id = run_id
        self.step_id = step_id

    def to_dict(self) -> dict[str, Any]:
        """Serialisable form used in events, API error bodies and traces."""
        return {
            "type": type(self).__name__,
            "code": self.code,
            "message": self.message,
            "details": self.details,
            "retryable": self.retryable,
            "run_id": self.run_id,
            "step_id": self.step_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HawserError:
        """Rebuild the most specific known error class from :meth:`to_dict` output."""
        klass = _BY_NAME.get(str(data.get("type", "")), HawserError)
        return klass(
            str(data.get("message", "")),
            code=data.get("code"),
            details=data.get("details"),
            retryable=data.get("retryable"),
            run_id=data.get("run_id"),
            step_id=data.get("step_id"),
        )

    def with_context(self, *, run_id: str | None = None, step_id: str | None = None) -> Self:
        """Attach run/step context without losing the original traceback."""
        if run_id is not None:
            self.run_id = run_id
        if step_id is not None:
            self.step_id = step_id
        return self

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"[{self.code}] {self.message}"


class AdmissionError(HawserError):
    """An input was rejected before entering the runtime (size, type, corruption, limits)."""

    default_code = "admission_rejected"


class PolicyViolation(HawserError):
    """The request or plan asks for something the effective policy forbids."""

    default_code = "policy_violation"


class BudgetExceeded(HawserError):
    """A cost, token, call, page, duration or step budget would be exceeded."""

    default_code = "budget_exceeded"


class PlanInvalid(HawserError):
    """A workflow or agent plan failed static validation."""

    default_code = "plan_invalid"


class ProviderError(HawserError):
    """A model provider call failed. ``retryable`` reflects the HTTP/transport class."""

    default_code = "provider_error"
    retryable_default = True


class ToolError(HawserError):
    """A tool invocation failed. Retry behaviour follows the ToolSpec's retry class."""

    default_code = "tool_error"


class SandboxDenied(HawserError):
    """Code mode was requested but no sandbox adapter passes the security requirements."""

    default_code = "sandbox_denied"


class ApprovalRequired(HawserError):
    """A side-effecting tool needs a human approval before it may run (run pauses)."""

    default_code = "approval_required"


class Cancelled(HawserError):
    """The run was cancelled by the caller."""

    default_code = "cancelled"


class UsageMismatch(HawserError):
    """Usage totals computed from events do not reconcile with step totals (internal bug)."""

    default_code = "usage_mismatch"


class InvalidTransition(HawserError):
    """An illegal run state transition was attempted (internal bug)."""

    default_code = "invalid_transition"


_BY_NAME: dict[str, type[HawserError]] = {
    cls.__name__: cls
    for cls in (
        HawserError,
        AdmissionError,
        PolicyViolation,
        BudgetExceeded,
        PlanInvalid,
        ProviderError,
        ToolError,
        SandboxDenied,
        ApprovalRequired,
        Cancelled,
        UsageMismatch,
        InvalidTransition,
    )
}

__all__ = [
    "AdmissionError",
    "ApprovalRequired",
    "BudgetExceeded",
    "Cancelled",
    "HawserError",
    "InvalidTransition",
    "PlanInvalid",
    "PolicyViolation",
    "ProviderError",
    "SandboxDenied",
    "ToolError",
    "UsageMismatch",
]
