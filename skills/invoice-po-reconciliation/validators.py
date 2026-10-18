"""Skill-local validators. Each object with ``id``, ``version`` and ``validate(ctx)`` is picked up."""

from __future__ import annotations

from hawser.models import Issue, RepairKind, Severity
from hawser.validation.core import ValidationContext, make_issue


class SummaryConsistent:
    """``summary_status`` must agree with the discrepancy count."""

    id = "invoice_po.summary_consistent"
    version = "1"

    def validate(self, ctx: ValidationContext) -> list[Issue]:
        rec = (ctx.result or {}).get("reconciliation") if isinstance(ctx.result, dict) else None
        if not isinstance(rec, dict):
            return []
        status, count = rec.get("summary_status"), rec.get("discrepancy_count", 0)
        if status == "match" and count:
            return [make_issue(self.id, Severity.error, f"summary says match but {count} discrepancies were found", field_ptr="/reconciliation/summary_status", repair=RepairKind.review)]
        if status == "discrepancies" and not count and not rec.get("total_delta"):
            return [make_issue(self.id, Severity.warning, "summary says discrepancies but none were listed", field_ptr="/reconciliation/summary_status")]
        return []


VALIDATORS = [SummaryConsistent()]
