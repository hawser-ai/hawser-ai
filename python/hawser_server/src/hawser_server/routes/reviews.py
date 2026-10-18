from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header, Response

from hawser.models import Revision
from hawser.models.review import Review, ReviewStatus
from hawser.review import ReviewService
from hawser_server.deps import PrincipalDep, ServicesDep, owned_run
from hawser_server.errors import BadRequest, NotFound
from hawser_server.schemas import (
    CurrentResult,
    ReviewDecideRequest,
    ReviewEditRequest,
    ReviewOpenRequest,
)

router = APIRouter(prefix="/v1/reviews", tags=["reviews"])


def _service(svc: ServicesDep, who: PrincipalDep) -> ReviewService:
    return ReviewService(svc.meta, who.tenant_id)


def _version(if_match: str | None) -> int | None:
    if if_match is None:
        return None
    raw = if_match.strip().removeprefix("W/").strip('"')
    if not raw.isdigit():
        raise BadRequest("If-Match must be the review version", code="bad_if_match")
    return int(raw)


@router.get("", response_model=list[Review])
def list_reviews(
    svc: ServicesDep, who: PrincipalDep, status: ReviewStatus | None = None
) -> list[Review]:
    return _service(svc, who).list(status=status)


@router.get("/{run_id}", response_model=Review)
def get_review(svc: ServicesDep, who: PrincipalDep, run_id: str, response: Response) -> Review:
    owned_run(svc, who, run_id)
    review = _service(svc, who).get(run_id)
    if review is None:
        raise NotFound(f"no review opened for {run_id}", code="review_not_found")
    response.headers["ETag"] = review.etag
    return review


@router.get("/{run_id}/current", response_model=CurrentResult)
def current_result(svc: ServicesDep, who: PrincipalDep, run_id: str) -> CurrentResult:
    """The machine result with the review's edits applied (not yet a revision)."""
    owned_run(svc, who, run_id)
    return CurrentResult(run_id=run_id, result=_service(svc, who).current_result(run_id))


@router.post("/{run_id}/open", response_model=Review)
def open_review(
    svc: ServicesDep,
    who: PrincipalDep,
    run_id: str,
    body: ReviewOpenRequest,
    response: Response,
) -> Review:
    owned_run(svc, who, run_id)
    review = _service(svc, who).open(run_id, assigned_to=body.assigned_to)
    response.headers["ETag"] = review.etag
    return review


@router.post("/{run_id}/edit", response_model=Review)
def edit_review(  # noqa: PLR0917 - FastAPI injects each parameter
    svc: ServicesDep,
    who: PrincipalDep,
    run_id: str,
    body: ReviewEditRequest,
    response: Response,
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> Review:
    owned_run(svc, who, run_id)
    review = _service(svc, who).edit(
        run_id,
        body.changes,
        reviewer=body.reviewer,
        comment=body.comment,
        expected_version=_version(if_match),
    )
    response.headers["ETag"] = review.etag
    return review


def _decide(
    svc: ServicesDep,
    who: PrincipalDep,
    run_id: str,
    body: ReviewDecideRequest,
    if_match: str | None,
    *,
    approve: bool,
) -> Revision:
    owned_run(svc, who, run_id)
    service = _service(svc, who)
    fn = service.approve if approve else service.reject
    revision = fn(
        run_id, reviewer=body.reviewer, comment=body.comment, expected_version=_version(if_match)
    )
    run = svc.meta.get_run(run_id)
    svc.webhooks.fan_out(
        who.tenant_id,
        "review.decided",
        {
            "run_id": run_id,
            "status": run.status.value if run else None,
            "revision_id": revision.id,
            "decision": "approved" if approve else "rejected",
            "reviewer": body.reviewer,
        },
        run_id=run_id,
    )
    return revision


@router.post("/{run_id}/approve", response_model=Revision)
def approve_review(
    svc: ServicesDep,
    who: PrincipalDep,
    run_id: str,
    body: ReviewDecideRequest,
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> Revision:
    return _decide(svc, who, run_id, body, if_match, approve=True)


@router.post("/{run_id}/reject", response_model=Revision)
def reject_review(
    svc: ServicesDep,
    who: PrincipalDep,
    run_id: str,
    body: ReviewDecideRequest,
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> Revision:
    return _decide(svc, who, run_id, body, if_match, approve=False)
