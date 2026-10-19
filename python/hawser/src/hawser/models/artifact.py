"""Artifacts and lineage (blueprint §19)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from hawser.clock import UtcDatetime
from hawser.ids import ArtifactId, RunId, StepId, TenantId
from hawser.models.base import HawserModel
from hawser.models.common import ProducerRef, Transform


class ArtifactKind(StrEnum):
    original = "original"  # an admitted input, byte-for-byte
    rendered_page = "rendered_page"
    crop = "crop"
    mask = "mask"
    annotation = "annotation"
    transcript = "transcript"
    document_index = "index"  # text/temporal indexes (JSON)
    table = "table"
    report = "report"
    redacted = "redacted"
    clip = "clip"
    generated = "generated"
    sandbox_output = "sandbox_output"
    step_output = "step_output"  # checkpointed step result (JSON)
    eval_report = "eval_report"  # a published evaluation report (JSON)
    other = "other"


class ArtifactRef(HawserModel):
    """A lightweight pointer carried in results, events and RPC messages."""

    artifact_id: ArtifactId
    media_type: str | None = None


class ArtifactMeta(HawserModel):
    kind: ArtifactKind
    media_type: str
    size_bytes: int = Field(ge=0)
    content_hash: str
    scoped_hash: str
    #: Direct parents in the lineage graph.
    parents: list[ArtifactId] = []
    producer: ProducerRef
    #: Transform from this artifact's coordinate/time space to its (first) parent's.
    transform: Transform | None = None
    #: Media-specific metadata (dimensions, dpi, duration, page number ...).
    media: dict[str, int | float | str | bool] = {}
    retain_until: UtcDatetime | None = None


class Artifact(HawserModel):
    id: ArtifactId
    tenant_id: TenantId
    run_id: RunId | None = None
    step_id: StepId | None = None
    meta: ArtifactMeta
    created_at: UtcDatetime

    def ref(self) -> ArtifactRef:
        return ArtifactRef(artifact_id=self.id, media_type=self.meta.media_type)


__all__ = ["Artifact", "ArtifactKind", "ArtifactMeta", "ArtifactRef"]
