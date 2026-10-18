"""Validation issues and reports (blueprint §16)."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import Field

from hawser.ids import EvidenceId, IssueId
from hawser.models.base import HawserModel
from hawser.models.common import JsonPointer
from hawser.models.policy import Severity


class RepairKind(StrEnum):
    """What the runtime may try for a repairable issue (blueprint §16)."""

    retrieve = "retrieve"  # fetch a more relevant crop/page/segment and re-extract
    reocr = "reocr"  # re-run OCR on a region
    remodel = "remodel"  # select a different approved model
    ask_field = "ask_field"  # ask for the missing/invalid field only
    recalculate = "recalculate"  # recompute deterministic values
    reexecute = "reexecute"  # re-run an approved step
    review = "review"  # only a human can resolve this
    none = "none"  # not repairable


class Issue(HawserModel):
    id: IssueId
    #: Stable rule identifier, e.g. ``core.required_fields`` or ``skill.invoice_po.totals_match``.
    rule_id: str
    rule_version: str | None = None
    severity: Severity
    field: JsonPointer | None = None
    message: str
    evidence: list[EvidenceId] = []
    repair: RepairKind = RepairKind.none
    details: dict[str, Any] = {}


class RuleRun(HawserModel):
    rule_id: str
    version: str | None = None
    duration_ms: int = Field(ge=0)
    issues: int = Field(ge=0)


class ValidationReport(HawserModel):
    issues: list[Issue] = []
    rules_run: list[RuleRun] = []
    duration_ms: int = Field(default=0, ge=0)

    def count(self, severity: Severity) -> int:
        return sum(1 for i in self.issues if i.severity == severity)

    def count_errors(self) -> int:
        return self.count(Severity.error)

    def count_warnings(self) -> int:
        return self.count(Severity.warning)

    @property
    def has_errors(self) -> bool:
        return any(i.severity == Severity.error for i in self.issues)


__all__ = ["Issue", "RepairKind", "RuleRun", "ValidationReport"]
