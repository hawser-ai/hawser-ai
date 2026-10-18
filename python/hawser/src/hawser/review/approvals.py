"""Approval records for side-effecting tool actions (blueprint §11, §17)."""

from __future__ import annotations

from typing import Any

from hawser.clock import now
from hawser.errors import PolicyViolation
from hawser.ids import new_id
from hawser.models import Approval, ApprovalStatus, EventType
from hawser.storage import MetadataStore


class ApprovalService:
    def __init__(self, meta: MetadataStore, tenant_id: str) -> None:
        self.meta = meta
        self.tenant_id = tenant_id

    def find(self, run_id: str, tool: str, action_id: str) -> Approval | None:
        for a in self.meta.list_approvals(self.tenant_id, run_id=run_id):
            if a.tool == tool and a.action_id == action_id:
                return a
        return None

    def request(
        self,
        run_id: str,
        *,
        tool: str,
        action_id: str,
        payload: dict[str, Any],
        step_id: str | None = None,
    ) -> Approval:
        existing = self.find(run_id, tool, action_id)
        if existing is not None:
            return existing
        approval = Approval(
            id=new_id("apr"),
            run_id=run_id,
            tenant_id=self.tenant_id,
            step_id=step_id,
            tool=tool,
            action_id=action_id,
            payload=payload,
            requested_at=now(),
        )
        self.meta.put_approval(approval)
        self.meta.append_event(
            run_id=run_id,
            tenant_id=self.tenant_id,
            type=EventType.approval_requested,
            payload={"approval_id": approval.id, "tool": tool, "action_id": action_id},
            step_id=step_id,
        )
        return approval

    def list(
        self, *, run_id: str | None = None, status: ApprovalStatus | None = None
    ) -> list[Approval]:
        return self.meta.list_approvals(
            self.tenant_id, run_id=run_id, status=status.value if status else None
        )

    def decide(
        self, approval_id: str, *, approve: bool, by: str = "local", comment: str | None = None
    ) -> Approval:
        approval = self.meta.get_approval(approval_id)
        if approval is None:
            raise PolicyViolation(f"unknown approval {approval_id}", code="approval_unknown")
        if approval.status != ApprovalStatus.pending:
            raise PolicyViolation(
                f"approval {approval_id} is already {approval.status.value}", code="approval_closed"
            )
        approval.status = ApprovalStatus.approved if approve else ApprovalStatus.rejected
        approval.decided_at = now()
        approval.decided_by = by
        approval.comment = comment
        self.meta.put_approval(approval)
        self.meta.append_event(
            run_id=approval.run_id,
            tenant_id=self.tenant_id,
            type=EventType.approval_decided,
            payload={
                "approval_id": approval.id,
                "decision": approval.status.value,
                "by": by,
                "tool": approval.tool,
                "action_id": approval.action_id,
            },
        )
        return approval


__all__ = ["ApprovalService"]
