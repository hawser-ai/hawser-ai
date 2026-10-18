"""Runs, steps, results and revisions (blueprint §2, §10.2, §14, §17)."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, Field, model_validator

from hawser.clock import UtcDatetime
from hawser.ids import (
    ArtifactId,
    EvidenceId,
    IssueId,
    PlanId,
    RevisionId,
    RunId,
    StepId,
    TenantId,
)
from hawser.models.artifact import ArtifactRef
from hawser.models.asset import InputRef
from hawser.models.base import HawserModel
from hawser.models.common import JsonPointer, JsonSchema, SemVer, Slug
from hawser.models.evidence import Evidence
from hawser.models.policy import ExecutionMode, Policy
from hawser.models.usage import Usage
from hawser.models.validation import ValidationReport

ModelT = TypeVar("ModelT", bound=BaseModel)


class RunStatus(StrEnum):
    """Run lifecycle. See :mod:`hawser.runtime.state` for the allowed transitions."""

    queued = "queued"
    admitting = "admitting"
    planning = "planning"
    running = "running"
    awaiting_approval = "awaiting_approval"  # paused: a side-effecting tool needs approval
    validating = "validating"
    repairing = "repairing"
    # Resting: finished machine work, waiting for a reviewer. A decision moves it on.
    needs_review = "needs_review"
    # Terminal.
    accepted = "accepted"
    rejected = "rejected"
    failed = "failed"
    cancelled = "cancelled"


TERMINAL_STATUSES: frozenset[RunStatus] = frozenset(
    {RunStatus.accepted, RunStatus.rejected, RunStatus.failed, RunStatus.cancelled}
)
RESTING_STATUSES: frozenset[RunStatus] = frozenset({RunStatus.needs_review})
PAUSED_STATUSES: frozenset[RunStatus] = frozenset({RunStatus.awaiting_approval})


class SkillRef(HawserModel):
    name: Slug
    version: SemVer | None = None  # None = resolve from the lockfile / latest installed


class ModelPreference(HawserModel):
    pinned: str | None = None
    candidates: list[str] | None = None
    strategy: Literal["cheapest", "fastest", "best_quality", "balanced"] = "balanced"


class RunRequest(HawserModel):
    """What a caller submits. Either ``instruction`` or ``skill`` (or both) must be present."""

    instruction: str | None = None
    inputs: list[InputRef] = []
    output_schema: JsonSchema | None = None
    skill: SkillRef | None = None
    mode: ExecutionMode | None = None
    policy: Policy = Policy()
    #: Permitted tool names. May only *narrow* what the skill and policy allow.
    tools: list[str] | None = None
    models: ModelPreference | None = None
    metadata: dict[str, str] = {}
    idempotency_key: str | None = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def _instruction_or_skill(self) -> RunRequest:
        if self.instruction is None and self.skill is None:
            raise ValueError("a run needs an instruction, a skill, or both")
        return self


class FieldStatus(StrEnum):
    found = "found"
    unknown = "unknown"
    not_applicable = "not_applicable"
    derived = "derived"  # computed deterministically from other fields


class FieldMeta(HawserModel):
    """Per-field sidecar: evidence, issues and extraction status (ADR-0005)."""

    status: FieldStatus = FieldStatus.found
    evidence: list[EvidenceId] = []
    issues: list[IssueId] = []
    anchor_confidence: float | None = Field(default=None, ge=0, le=1)


class RunWarning(HawserModel):
    code: str
    message: str
    details: dict[str, Any] = {}


class RunResult(HawserModel):
    """The response contract (blueprint §2)."""

    run_id: RunId
    status: RunStatus
    mode: ExecutionMode | None = None
    #: Conforms to the request's ``output_schema`` when one was given.
    result: Any | None = None
    #: Keyed by JSON pointer into ``result``.
    field_meta: dict[JsonPointer, FieldMeta] = {}
    evidence: list[Evidence] = []
    validation: ValidationReport = ValidationReport()
    artifacts: list[ArtifactRef] = []
    usage: Usage = Usage()
    trace_id: str
    #: The machine revision. Reviewed revisions are separate objects.
    revision_id: RevisionId | None = None
    plan_id: PlanId | None = None
    error: dict[str, Any] | None = None
    warnings: list[RunWarning] = []
    created_at: UtcDatetime
    completed_at: UtcDatetime | None = None

    @model_validator(mode="after")
    def _consistency(self) -> RunResult:
        if self.status == RunStatus.failed and self.error is None:
            raise ValueError("a failed run must carry an error")
        if self.status == RunStatus.accepted and self.validation.has_errors:
            raise ValueError("an accepted run cannot carry error-severity validation issues")
        return self

    def parse_as(self, model: type[ModelT]) -> ModelT:
        """Validate ``result`` into the caller's Pydantic model."""
        return model.model_validate(self.result)

    def evidence_for(self, field_pointer: JsonPointer) -> list[Evidence]:
        meta = self.field_meta.get(field_pointer)
        if meta is None:
            return []
        by_id = {e.id: e for e in self.evidence}
        return [by_id[i] for i in meta.evidence if i in by_id]


class StepKind(StrEnum):
    admit = "admit"
    plan = "plan"
    model = "model"
    tool = "tool"
    compute = "compute"
    validate = "validate"
    repair = "repair"
    review = "review"
    approval = "approval"
    sandbox = "sandbox"
    workflow = "workflow"


class StepStatus(StrEnum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"
    skipped = "skipped"
    cancelled = "cancelled"


class Step(HawserModel):
    id: StepId
    run_id: RunId
    #: Idempotency key, unique per run (``plan_id/node_id[/iteration]``).
    key: str = Field(max_length=512)
    name: str
    kind: StepKind
    status: StepStatus = StepStatus.pending
    attempt: int = Field(default=1, ge=1)
    parent_step_id: StepId | None = None
    input_ref: ArtifactRef | None = None
    output_ref: ArtifactRef | None = None
    usage: Usage = Usage()
    error: dict[str, Any] | None = None
    started_at: UtcDatetime | None = None
    completed_at: UtcDatetime | None = None


class Revision(HawserModel):
    """A result snapshot. Machine revisions are immutable; reviews create new ones."""

    id: RevisionId
    run_id: RunId
    tenant_id: TenantId
    kind: Literal["machine", "reviewed"]
    result: Any | None
    field_meta: dict[JsonPointer, FieldMeta] = {}
    parent_revision_id: RevisionId | None = None
    #: ``system`` for machine revisions; a reviewer identity otherwise.
    author: str
    comment: str | None = None
    created_at: UtcDatetime


class RunSummary(HawserModel):
    """List-view projection of a run."""

    id: RunId
    tenant_id: TenantId
    status: RunStatus
    mode: ExecutionMode | None = None
    skill: SkillRef | None = None
    cost_usd: float = Field(default=0, ge=0)
    created_at: UtcDatetime
    completed_at: UtcDatetime | None = None
    metadata: dict[str, str] = {}
    artifact_count: int = Field(default=0, ge=0)
    output_artifact_ids: list[ArtifactId] = []


__all__ = [
    "PAUSED_STATUSES",
    "RESTING_STATUSES",
    "TERMINAL_STATUSES",
    "FieldMeta",
    "FieldStatus",
    "ModelPreference",
    "Revision",
    "RunRequest",
    "RunResult",
    "RunStatus",
    "RunSummary",
    "RunWarning",
    "SkillRef",
    "Step",
    "StepKind",
    "StepStatus",
]
