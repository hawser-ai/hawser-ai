from __future__ import annotations

from fastapi import APIRouter

from hawser.models import Approval, ApprovalStatus, RunStatus
from hawser.review.approvals import ApprovalService
from hawser_server.deps import PrincipalDep, ServicesDep, owned_run
from hawser_server.errors import NotFound
from hawser_server.schemas import ApprovalDecided, ApprovalDecideRequest

router = APIRouter(prefix="/v1/approvals", tags=["approvals"])


@router.get("", response_model=list[Approval])
def list_approvals(
    svc: ServicesDep,
    who: PrincipalDep,
    run_id: str | None = None,
    status: ApprovalStatus | None = ApprovalStatus.pending,
) -> list[Approval]:
    if run_id is not None:
        owned_run(svc, who, run_id)
    return ApprovalService(svc.meta, who.tenant_id).list(run_id=run_id, status=status)


@router.get("/{approval_id}", response_model=Approval)
def get_approval(svc: ServicesDep, who: PrincipalDep, approval_id: str) -> Approval:
    approval = svc.meta.get_approval(approval_id)
    if approval is None or approval.tenant_id != who.tenant_id:
        raise NotFound(f"unknown approval {approval_id}", code="approval_unknown")
    return approval


@router.post("/{approval_id}/decide", response_model=ApprovalDecided)
def decide_approval(
    svc: ServicesDep, who: PrincipalDep, approval_id: str, body: ApprovalDecideRequest
) -> ApprovalDecided:
    """Approve or reject; when nothing else is pending the run is re-queued (``resume``)."""
    approval = get_approval(svc, who, approval_id)
    service = ApprovalService(svc.meta, who.tenant_id)
    decided = service.decide(approval.id, approve=body.approve, by=body.by, comment=body.comment)
    resumed = False
    if body.resume:
        run = svc.meta.get_run(decided.run_id)
        pending = service.list(run_id=decided.run_id, status=ApprovalStatus.pending)
        if run is not None and run.status == RunStatus.awaiting_approval and not pending:
            run = svc.engine.begin_resume(run.id)
            svc.queue.enqueue(run.id, run.tenant_id, resume=True)
            resumed = True
    return ApprovalDecided(approval=decided, resumed=resumed)
