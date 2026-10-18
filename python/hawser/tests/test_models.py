from datetime import datetime
from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from hawser.clock import now
from hawser.ids import new_id
from hawser.models import (
    BBox,
    DocumentRegionEvidence,
    Evidence,
    Issue,
    Limits,
    ModelCallUsage,
    Policy,
    ProducerRef,
    RunRequest,
    RunResult,
    RunStatus,
    Severity,
    SkillRef,
    Usage,
    ValidationReport,
)
from hawser.models.usage import UsageTotals


def test_bbox_ordering() -> None:
    assert BBox(x0=0, y0=0, x1=1, y1=1).width == 1
    with pytest.raises(ValidationError):
        BBox(x0=2, y0=0, x1=1, y1=1)


def test_run_request_needs_instruction_or_skill() -> None:
    with pytest.raises(ValidationError, match="instruction, a skill, or both"):
        RunRequest()
    RunRequest(instruction="x")
    RunRequest(skill=SkillRef(name="invoice-po-reconciliation"))


def test_unknown_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        Policy(limits=Limits(max_cost_usd=1), bogus=True)  # type: ignore[call-arg]


def test_limits_non_negative() -> None:
    with pytest.raises(ValidationError):
        Limits(max_cost_usd=-1)
    with pytest.raises(ValidationError):
        Limits(max_parallel=0)


def _result(
    status: RunStatus,
    *,
    error: dict[str, Any] | None = None,
    validation: ValidationReport | None = None,
) -> RunResult:
    return RunResult(
        run_id=new_id("run"),
        status=status,
        trace_id="t",
        created_at=now(),
        error=error,
        validation=validation or ValidationReport(),
    )


def test_run_result_consistency_rules() -> None:
    with pytest.raises(ValidationError, match="failed run must carry an error"):
        _result(RunStatus.failed)
    _result(RunStatus.failed, error={"code": "x"})

    issue = Issue(
        id=new_id("iss"), rule_id="core.required_fields", severity=Severity.error, message="m"
    )
    with pytest.raises(ValidationError, match="cannot carry error-severity"):
        _result(RunStatus.accepted, validation=ValidationReport(issues=[issue]))
    _result(RunStatus.needs_review, validation=ValidationReport(issues=[issue]))


def test_evidence_discriminated_union_round_trip() -> None:
    ev = DocumentRegionEvidence(
        id=new_id("ev"),
        asset_id=new_id("asset"),
        producer=ProducerRef(kind="tool", name="evidence.anchor", version="1"),
        page=1,
        bbox=BBox(x0=120, y0=80, x1=260, y1=112),
        text="Invoice No: INV-1042",
        anchor_confidence=1.0,
    )
    adapter = TypeAdapter(Evidence)
    back = adapter.validate_json(adapter.dump_json(ev))
    assert back == ev
    assert back.kind == "document_region"


def test_usage_totals_are_derived() -> None:
    usage = Usage(
        model_calls=[
            ModelCallUsage(
                provider="anthropic",
                model="claude-sonnet-5-5",
                input_tokens=1000,
                output_tokens=200,
                cost_usd=0.01,
                latency_ms=900,
            ),
            ModelCallUsage(
                provider="openai",
                model="gpt-x",
                input_tokens=500,
                output_tokens=50,
                cost_usd=0.002,
                latency_ms=400,
                replayed=True,
            ),
        ]
    )
    totals = usage.recompute_totals(pages_processed=3)
    assert totals.cost_usd == pytest.approx(0.012)
    assert totals.model_copy(update={"cost_usd": 0.0}) == UsageTotals(
        model_calls=2, input_tokens=1500, output_tokens=250, pages_processed=3
    )
    assert usage.totals is totals
    assert usage.models_used == ["anthropic/claude-sonnet-5-5", "openai/gpt-x"]


def test_timestamps_must_be_aware() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        RunResult(
            run_id=new_id("run"),
            status=RunStatus.queued,
            trace_id="t",
            created_at=datetime(2026, 1, 1),
        )
