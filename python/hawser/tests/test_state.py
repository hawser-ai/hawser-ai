import itertools

import pytest

from hawser.errors import (
    AdmissionError,
    BudgetExceeded,
    Cancelled,
    HawserError,
    InvalidTransition,
    ProviderError,
)
from hawser.models.run import PAUSED_STATUSES, RESTING_STATUSES, TERMINAL_STATUSES, RunStatus
from hawser.runtime.state import (
    ALLOWED_TRANSITIONS,
    can_transition,
    is_terminal,
    status_for_error,
    transition,
)

ALL = list(RunStatus)


def test_every_status_has_an_entry() -> None:
    assert set(ALLOWED_TRANSITIONS) == set(ALL)


def test_terminal_states_have_no_outgoing_edges() -> None:
    for s in TERMINAL_STATUSES:
        assert ALLOWED_TRANSITIONS[s] == frozenset(), s
        assert is_terminal(s)


def test_non_terminal_states_can_fail_or_be_cancelled() -> None:
    for s in ALL:
        if s in TERMINAL_STATUSES:
            continue
        assert RunStatus.cancelled in ALLOWED_TRANSITIONS[s], s
        if s not in RESTING_STATUSES:
            assert RunStatus.failed in ALLOWED_TRANSITIONS[s], s


def test_no_self_loops() -> None:
    for s, targets in ALLOWED_TRANSITIONS.items():
        assert s not in targets


def test_every_state_reaches_a_terminal_state() -> None:
    def reachable(start: RunStatus) -> set[RunStatus]:
        seen: set[RunStatus] = set()
        stack = [start]
        while stack:
            cur = stack.pop()
            for nxt in ALLOWED_TRANSITIONS[cur]:
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        return seen

    for s in ALL:
        if s in TERMINAL_STATUSES:
            continue
        assert reachable(s) & TERMINAL_STATUSES, s


def test_happy_path() -> None:
    path = [
        RunStatus.queued,
        RunStatus.admitting,
        RunStatus.planning,
        RunStatus.running,
        RunStatus.validating,
        RunStatus.accepted,
    ]
    for a, b in itertools.pairwise(path):
        assert transition(a, b) == b


def test_repair_and_review_paths() -> None:
    assert can_transition(RunStatus.validating, RunStatus.repairing)
    assert can_transition(RunStatus.repairing, RunStatus.running)
    assert can_transition(RunStatus.repairing, RunStatus.needs_review)
    assert can_transition(RunStatus.validating, RunStatus.needs_review)
    assert can_transition(RunStatus.needs_review, RunStatus.accepted)
    assert can_transition(RunStatus.needs_review, RunStatus.rejected)
    assert not can_transition(RunStatus.needs_review, RunStatus.running)


def test_approval_pause() -> None:
    for s in PAUSED_STATUSES:
        assert can_transition(RunStatus.running, s)
        assert can_transition(s, RunStatus.running)
        assert can_transition(s, RunStatus.rejected)


@pytest.mark.parametrize(
    ("a", "b"),
    [
        (RunStatus.accepted, RunStatus.running),
        (RunStatus.queued, RunStatus.accepted),
        (RunStatus.running, RunStatus.accepted),
        (RunStatus.failed, RunStatus.cancelled),
    ],
)
def test_invalid_transition_raises(a: RunStatus, b: RunStatus) -> None:
    with pytest.raises(InvalidTransition) as exc:
        transition(a, b, run_id="run_01J9X3K4N7Q2M8R5T6V7W8X9Y0")
    assert exc.value.details == {"from": a.value, "to": b.value}
    assert exc.value.run_id == "run_01J9X3K4N7Q2M8R5T6V7W8X9Y0"


def test_status_for_error() -> None:
    assert status_for_error(Cancelled("x"), review_on_failure=True) == RunStatus.cancelled
    assert status_for_error(BudgetExceeded("x"), review_on_failure=True) == RunStatus.needs_review
    assert status_for_error(BudgetExceeded("x"), review_on_failure=False) == RunStatus.failed
    assert status_for_error(AdmissionError("x"), review_on_failure=True) == RunStatus.failed
    assert status_for_error(ProviderError("x"), review_on_failure=True) == RunStatus.failed
    assert status_for_error(HawserError("x"), review_on_failure=True) == RunStatus.failed
