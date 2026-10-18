"""problem+json responses mapped from the error taxonomy (blueprint §22)."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from hawser.errors import (
    AdmissionError,
    BudgetExceeded,
    HawserError,
    PlanInvalid,
    PolicyViolation,
    ProviderError,
)

PROBLEM = "application/problem+json"


class NotFound(HawserError):
    default_code = "not_found"


class Unauthorized(HawserError):
    default_code = "unauthorized"


class BadRequest(HawserError):
    default_code = "bad_request"


class Conflict(HawserError):
    default_code = "conflict"


class PayloadTooLarge(HawserError):
    default_code = "payload_too_large"


_NOT_FOUND_CODES = {"run_unknown", "approval_unknown", "asset_not_found", "artifact_not_found"}
_CONFLICT_CODES = {
    "review_conflict",
    "review_closed",
    "review_not_applicable",
    "approval_closed",
    "resume_not_applicable",
}


def status_for(error: HawserError) -> int:  # noqa: PLR0911 - one branch per class
    if isinstance(error, NotFound) or error.code in _NOT_FOUND_CODES:
        return 404
    if isinstance(error, Unauthorized):
        return 401
    if isinstance(error, Conflict) or error.code in _CONFLICT_CODES:
        return 409
    if isinstance(error, PayloadTooLarge):
        return 413
    if isinstance(error, BadRequest | AdmissionError | PlanInvalid | BudgetExceeded):
        return 400
    if isinstance(error, PolicyViolation):
        return 403
    if isinstance(error, ProviderError):
        return 502
    return 500


def problem(
    status: int,
    code: str,
    detail: str,
    *,
    request_id: str | None = None,
    extra: dict[str, Any] | None = None,
) -> JSONResponse:
    body: dict[str, Any] = {
        "type": f"https://hawser.dev/errors/{code}",
        "title": code.replace("_", " "),
        "status": status,
        "code": code,
        "detail": detail,
    }
    if request_id:
        body["request_id"] = request_id
    if extra:
        body.update(extra)
    return JSONResponse(body, status_code=status, media_type=PROBLEM)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(HawserError)
    async def _hawser(request: Request, exc: HawserError) -> JSONResponse:
        rid = getattr(request.state, "request_id", None)
        extra: dict[str, Any] = {"error_type": type(exc).__name__}
        if exc.details:
            extra["details"] = exc.details
        return problem(status_for(exc), exc.code, exc.message, request_id=rid, extra=extra)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        rid = getattr(request.state, "request_id", None)
        return problem(
            422,
            "invalid_request",
            "the request body or parameters are invalid",
            request_id=rid,
            extra={"errors": jsonable_encoder(exc.errors())},
        )


__all__ = [
    "PROBLEM",
    "BadRequest",
    "Conflict",
    "NotFound",
    "PayloadTooLarge",
    "Unauthorized",
    "install_error_handlers",
    "problem",
    "status_for",
]
