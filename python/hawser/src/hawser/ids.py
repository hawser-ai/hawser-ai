"""Prefixed ULID identifiers (ADR-0006).

Every stored object has an id of the form ``<prefix>_<ULID>``, for example
``run_01J9X3K4N7Q2M8R5T6V7W8X9Y0``. The prefix makes ids self-describing in logs and API
payloads; the ULID keeps them sortable by creation time. Ids are validated at every boundary —
a malformed id is rejected, never coerced.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Annotated, Final

from pydantic import AfterValidator
from ulid import ULID

PREFIXES: Final[dict[str, str]] = {
    "ten": "tenant",
    "prj": "project",
    "key": "api key",
    "asset": "asset",
    "art": "artifact",
    "run": "run",
    "step": "step",
    "evt": "event",
    "ev": "evidence",
    "iss": "validation issue",
    "rev": "revision",
    "skl": "skill",
    "tool": "tool",
    "mdl": "model",
    "ds": "dataset",
    "eval": "evaluation run",
    "rvw": "review",
    "apr": "approval",
    "whk": "webhook",
    "dlv": "webhook delivery",
    "plan": "plan",
}

_ID_RE: Final = re.compile(r"^(?P<prefix>[a-z]{2,6})_(?P<ulid>[0-7][0-9A-HJKMNP-TV-Z]{25})$")


class InvalidId(ValueError):
    """Raised when a string is not a well-formed Hawser id (or has the wrong prefix)."""


def new_id(prefix: str) -> str:
    """Create a new id with the given registered prefix."""
    if prefix not in PREFIXES:
        raise InvalidId(f"unknown id prefix {prefix!r}")
    return f"{prefix}_{ULID()}"


def parse_id(value: str, *, prefix: str | None = None) -> tuple[str, ULID]:
    """Split an id into ``(prefix, ulid)``; raise :class:`InvalidId` if malformed.

    When ``prefix`` is given, the id must carry exactly that prefix.
    """
    if not isinstance(value, str):  # pyright: ignore[reportUnnecessaryIsInstance]
        raise InvalidId(f"id must be a string, got {type(value).__name__}")
    m = _ID_RE.match(value)
    if m is None:
        raise InvalidId(f"malformed id {value!r}")
    found = m.group("prefix")
    if found not in PREFIXES:
        raise InvalidId(f"unknown id prefix {found!r} in {value!r}")
    if prefix is not None and found != prefix:
        raise InvalidId(f"expected a {prefix!r} id, got {value!r}")
    return found, ULID.from_str(m.group("ulid"))


def validate_id(value: str, *, prefix: str | None = None) -> str:
    """Return ``value`` unchanged if it is a well-formed id; otherwise raise :class:`InvalidId`."""
    parse_id(value, prefix=prefix)
    return value


def _validator(prefix: str) -> Callable[[str], str]:
    def check(value: str) -> str:
        return validate_id(value, prefix=prefix)

    check.__name__ = f"validate_{prefix}_id"
    return check


# Typed aliases for use in contract models. Pydantic validates the prefix on construction.
AnyId = Annotated[str, AfterValidator(validate_id)]
TenantId = Annotated[str, AfterValidator(_validator("ten"))]
ProjectId = Annotated[str, AfterValidator(_validator("prj"))]
AssetId = Annotated[str, AfterValidator(_validator("asset"))]
ArtifactId = Annotated[str, AfterValidator(_validator("art"))]
RunId = Annotated[str, AfterValidator(_validator("run"))]
StepId = Annotated[str, AfterValidator(_validator("step"))]
EventId = Annotated[str, AfterValidator(_validator("evt"))]
EvidenceId = Annotated[str, AfterValidator(_validator("ev"))]
IssueId = Annotated[str, AfterValidator(_validator("iss"))]
RevisionId = Annotated[str, AfterValidator(_validator("rev"))]
SkillId = Annotated[str, AfterValidator(_validator("skl"))]
ToolId = Annotated[str, AfterValidator(_validator("tool"))]
ModelId = Annotated[str, AfterValidator(_validator("mdl"))]
DatasetId = Annotated[str, AfterValidator(_validator("ds"))]
EvalId = Annotated[str, AfterValidator(_validator("eval"))]
ReviewId = Annotated[str, AfterValidator(_validator("rvw"))]
ApprovalId = Annotated[str, AfterValidator(_validator("apr"))]
WebhookId = Annotated[str, AfterValidator(_validator("whk"))]
DeliveryId = Annotated[str, AfterValidator(_validator("dlv"))]
PlanId = Annotated[str, AfterValidator(_validator("plan"))]

__all__ = [
    "PREFIXES",
    "AnyId",
    "ApprovalId",
    "ArtifactId",
    "AssetId",
    "DatasetId",
    "DeliveryId",
    "EvalId",
    "EventId",
    "EvidenceId",
    "InvalidId",
    "IssueId",
    "ModelId",
    "PlanId",
    "ProjectId",
    "ReviewId",
    "RevisionId",
    "RunId",
    "SkillId",
    "StepId",
    "TenantId",
    "ToolId",
    "WebhookId",
    "new_id",
    "parse_id",
    "validate_id",
]
