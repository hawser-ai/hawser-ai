"""Run state machine (Phase 0, WP0.5).

::

    queued ─► admitting ─► planning ─► running ─► validating ─► accepted
                                         ▲  │          │  │
                                         │  ▼          │  ├──► needs_review ─► accepted | rejected
                                 awaiting_approval     │  │
                                                       ▼  └──► rejected
                                                    repairing ─► running

Any non-terminal state may move to ``failed`` or ``cancelled``. ``needs_review`` is a resting
state: machine work is finished, a reviewer decision moves it to ``accepted`` or ``rejected``.
The transition table is the single source of truth; it is enumerated by tests and will be
mirrored by the Go control plane in Phase 5 (generated from the exported edge list).
"""

from __future__ import annotations

from typing import Final

from hawser.errors import (
    AdmissionError,
    ApprovalRequired,
    BudgetExceeded,
    Cancelled,
    HawserError,
    InvalidTransition,
    PlanInvalid,
    PolicyViolation,
    ProviderError,
    SandboxDenied,
    ToolError,
)
from hawser.models.run import (
    PAUSED_STATUSES,
    RESTING_STATUSES,
    TERMINAL_STATUSES,
    RunStatus,
)

S = RunStatus

_FAIL_OR_CANCEL: frozenset[RunStatus] = frozenset({S.failed, S.cancelled})

ALLOWED_TRANSITIONS: Final[dict[RunStatus, frozenset[RunStatus]]] = {
    S.queued: frozenset({S.admitting}) | _FAIL_OR_CANCEL,
    S.admitting: frozenset({S.planning}) | _FAIL_OR_CANCEL,
    S.planning: frozenset({S.running}) | _FAIL_OR_CANCEL,
    S.running: frozenset({S.validating, S.awaiting_approval}) | _FAIL_OR_CANCEL,
    S.awaiting_approval: frozenset({S.running, S.rejected}) | _FAIL_OR_CANCEL,
    S.validating: frozenset({S.accepted, S.repairing, S.needs_review, S.rejected})
    | _FAIL_OR_CANCEL,
    # Repair may give up straight to review when budget or attempts are exhausted.
    S.repairing: frozenset({S.running, S.needs_review}) | _FAIL_OR_CANCEL,
    S.needs_review: frozenset({S.accepted, S.rejected, S.cancelled}),
    S.accepted: frozenset(),
    S.rejected: frozenset(),
    S.failed: frozenset(),
    S.cancelled: frozenset(),
}


def can_transition(current: RunStatus, new: RunStatus) -> bool:
    return new in ALLOWED_TRANSITIONS[current]


def transition(current: RunStatus, new: RunStatus, *, run_id: str | None = None) -> RunStatus:
    """Return ``new`` if the edge is allowed; otherwise raise :class:`InvalidTransition`."""
    if not can_transition(current, new):
        raise InvalidTransition(
            f"cannot move run from {current.value} to {new.value}",
            details={"from": current.value, "to": new.value},
            run_id=run_id,
        )
    return new


def status_for_error(error: HawserError, *, review_on_failure: bool) -> RunStatus:
    """Map an error raised during execution to the run status it should end in.

    Budget exhaustion can route to review when the policy asks for it (a partial result
    with a human decision beats a hard failure). Everything else fails.
    """
    if isinstance(error, Cancelled):
        return S.cancelled
    if isinstance(error, ApprovalRequired):
        return S.awaiting_approval
    if isinstance(error, BudgetExceeded) and review_on_failure:
        return S.needs_review
    if isinstance(
        error,
        AdmissionError
        | PolicyViolation
        | BudgetExceeded
        | PlanInvalid
        | ProviderError
        | ToolError
        | SandboxDenied,
    ):
        return S.failed
    return S.failed


def is_terminal(status: RunStatus) -> bool:
    return status in TERMINAL_STATUSES


#: Statuses a run comes to rest on: terminal, needs_review and awaiting_approval. They are
#: stored and announced only together with the run's result (``Engine._persist``), so no reader
#: ever sees a finished run whose result is not stored yet.
OUTCOME_STATUSES: frozenset[RunStatus] = TERMINAL_STATUSES | RESTING_STATUSES | PAUSED_STATUSES


def status_path(current: RunStatus, target: RunStatus) -> list[RunStatus]:
    """The shortest legal sequence of statuses from ``current`` to ``target`` (inclusive of
    ``target``, exclusive of ``current``). Raises :class:`InvalidTransition` if none exists."""
    if current == target:
        return []
    previous: dict[RunStatus, RunStatus] = {}
    frontier = [current]
    seen = {current}
    while frontier:
        nxt: list[RunStatus] = []
        for s in frontier:
            for n in ALLOWED_TRANSITIONS[s]:
                if n in seen:
                    continue
                seen.add(n)
                previous[n] = s
                if n == target:
                    path = [n]
                    while path[-1] != current:
                        path.append(previous[path[-1]])
                    return list(reversed(path[:-1]))
                if n not in TERMINAL_STATUSES:
                    nxt.append(n)
        frontier = nxt
    raise InvalidTransition(
        f"no path from {current.value} to {target.value}",
        details={"from": current.value, "to": target.value},
    )


__all__ = [
    "ALLOWED_TRANSITIONS",
    "OUTCOME_STATUSES",
    "can_transition",
    "is_terminal",
    "status_for_error",
    "status_path",
    "transition",
]
