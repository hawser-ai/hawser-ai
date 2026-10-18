"""Review workflow over the metadata store.

* The machine result is immutable; it is stored as the ``machine`` revision.
* ``edit`` records field decisions on the open review (optimistic concurrency via ``version``).
* ``approve``/``reject`` create a ``reviewed`` revision and move the run on.
"""

from __future__ import annotations

import copy
from typing import Any

from hawser.clock import now
from hawser.errors import HawserError, PolicyViolation
from hawser.ids import new_id
from hawser.models import EventType, Revision, RunStatus
from hawser.models.review import Review, ReviewDecision, ReviewStatus
from hawser.runtime.state import transition
from hawser.storage import MetadataStore


class ReviewConflict(HawserError):
    default_code = "review_conflict"


def _tokens(ptr: str) -> list[str]:
    return [t.replace("~1", "/").replace("~0", "~") for t in ptr.split("/")[1:]]


def get_pointer(data: Any, ptr: str) -> Any:
    cur = data
    for tok in _tokens(ptr):
        try:
            cur = cur[int(tok)] if isinstance(cur, list) else cur[tok]
        except (KeyError, IndexError, TypeError, ValueError):
            return None
    return cur


def apply_pointer(data: Any, ptr: str, value: Any) -> Any:
    """Return a deep copy of ``data`` with ``ptr`` set to ``value`` (creating object keys)."""
    out = copy.deepcopy(data) if data is not None else {}
    toks = _tokens(ptr)
    if not toks:
        return value
    cur = out
    for tok in toks[:-1]:
        key: Any = int(tok) if isinstance(cur, list) else tok
        if isinstance(cur, dict) and key not in cur:
            cur[key] = {}
        cur = cur[key]
    last: Any = int(toks[-1]) if isinstance(cur, list) else toks[-1]
    cur[last] = value
    return out


class ReviewService:
    def __init__(self, meta: MetadataStore, tenant_id: str) -> None:
        self.meta = meta
        self.tenant_id = tenant_id

    # -- queries ---------------------------------------------------------------------------
    def get(self, run_id: str) -> Review | None:
        return self.meta.get_review(run_id)

    def list(self, *, status: ReviewStatus | None = None) -> list[Review]:
        return self.meta.list_reviews(self.tenant_id, status=status.value if status else None)

    def revisions(self, run_id: str) -> list[Revision]:
        return self.meta.list_revisions(run_id)

    def latest_revision(self, run_id: str) -> Revision | None:
        revs = self.revisions(run_id)
        return revs[-1] if revs else None

    # -- lifecycle -------------------------------------------------------------------------
    def open(self, run_id: str, *, assigned_to: str | None = None) -> Review:
        existing = self.meta.get_review(run_id)
        if existing is not None:
            return existing
        run = self.meta.get_run(run_id)
        if run is None:
            raise PolicyViolation(f"unknown run {run_id}", code="run_unknown")
        if run.status != RunStatus.needs_review:
            raise PolicyViolation(
                f"run {run_id} is {run.status.value}; only needs_review runs can be reviewed",
                code="review_not_applicable",
            )
        review = Review(
            id=new_id("rvw"),
            run_id=run_id,
            tenant_id=self.tenant_id,
            assigned_to=assigned_to,
            created_at=now(),
            updated_at=now(),
        )
        self.meta.put_review(review)
        self.meta.append_event(
            run_id=run_id,
            tenant_id=self.tenant_id,
            type=EventType.review_requested,
            payload={"review_id": review.id},
        )
        return review

    def _check(self, review: Review, expected_version: int | None) -> None:
        if review.status != ReviewStatus.open:
            raise PolicyViolation(
                f"review {review.id} is already {review.status.value}", code="review_closed"
            )
        if expected_version is not None and expected_version != review.version:
            raise ReviewConflict(
                f"review {review.id} is at version {review.version}, not {expected_version}",
                details={"current_version": review.version},
            )

    def edit(
        self,
        run_id: str,
        changes: dict[str, Any],
        *,
        reviewer: str = "local",
        comment: str | None = None,
        expected_version: int | None = None,
    ) -> Review:
        review = self.open(run_id)
        self._check(review, expected_version)
        current = self.current_result(run_id)
        for ptr, new in changes.items():
            review.decisions.append(
                ReviewDecision(
                    field=ptr,
                    old=get_pointer(current, ptr),
                    new=new,
                    comment=comment,
                    reviewer=reviewer,
                    at=now(),
                )
            )
        review.version += 1
        review.updated_at = now()
        self.meta.put_review(review)
        return review

    def current_result(self, run_id: str) -> Any:
        """The machine result with all recorded edits applied (not yet a revision)."""
        result = self.meta.get_result(run_id)
        data = result.result if result else None
        review = self.meta.get_review(run_id)
        if review:
            for d in review.decisions:
                data = apply_pointer(data, d.field, d.new)
        return data

    def approve(
        self,
        run_id: str,
        *,
        reviewer: str = "local",
        comment: str | None = None,
        expected_version: int | None = None,
    ) -> Revision:
        return self._decide(
            run_id,
            RunStatus.accepted,
            reviewer=reviewer,
            comment=comment,
            expected_version=expected_version,
        )

    def reject(
        self,
        run_id: str,
        *,
        reviewer: str = "local",
        comment: str | None = None,
        expected_version: int | None = None,
    ) -> Revision:
        return self._decide(
            run_id,
            RunStatus.rejected,
            reviewer=reviewer,
            comment=comment,
            expected_version=expected_version,
        )

    def _decide(
        self,
        run_id: str,
        status: RunStatus,
        *,
        reviewer: str,
        comment: str | None,
        expected_version: int | None,
    ) -> Revision:
        review = self.open(run_id)
        self._check(review, expected_version)
        run = self.meta.get_run(run_id)
        assert run is not None
        machine = self.meta.get_result(run_id)
        parent = self.latest_revision(run_id)
        revision = Revision(
            id=new_id("rev"),
            run_id=run_id,
            tenant_id=self.tenant_id,
            kind="reviewed",
            result=self.current_result(run_id),
            field_meta=machine.field_meta if machine else {},
            parent_revision_id=parent.id if parent else None,
            author=reviewer,
            comment=comment,
            created_at=now(),
        )
        self.meta.put_revision(revision)
        review.status = (
            ReviewStatus.approved if status == RunStatus.accepted else ReviewStatus.rejected
        )
        review.decided_by = reviewer
        review.decided_at = now()
        review.comment = comment or review.comment
        review.version += 1
        review.updated_at = now()
        self.meta.put_review(review)
        old = run.status
        run.status = transition(old, status, run_id=run_id)
        run.updated_at = now()
        self.meta.put_run(run)
        self.meta.append_event(
            run_id=run_id,
            tenant_id=self.tenant_id,
            type=EventType.review_decided,
            payload={
                "review_id": review.id,
                "decision": review.status.value,
                "revision_id": revision.id,
                "reviewer": reviewer,
                "edits": len(review.decisions),
            },
        )
        self.meta.append_event(
            run_id=run_id,
            tenant_id=self.tenant_id,
            type=EventType.run_status_changed,
            payload={"from": old.value, "to": status.value},
        )
        return revision


__all__ = ["ReviewConflict", "ReviewService", "apply_pointer", "get_pointer"]
