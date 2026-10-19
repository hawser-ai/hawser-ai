"""Evidence and provenance (blueprint §15, ADR-0005).

Evidence is a first-class object. Every item records the original asset, the coordinate or
time system it is expressed in, the transform chain back to the original, the producer and
a confidence. Field-level evidence is attached to results through the ``field_meta`` sidecar.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from hawser.ids import ArtifactId, AssetId, EvidenceId, StepId
from hawser.models.base import HawserModel
from hawser.models.common import BBox, CoordinateSystem, Point, ProducerRef, Transform


class _EvidenceBase(HawserModel):
    id: EvidenceId
    asset_id: AssetId
    #: The derived artifact the evidence was located in (rendered page, crop, transcript ...).
    artifact_id: ArtifactId | None = None
    step_id: StepId | None = None
    producer: ProducerRef
    confidence: float | None = Field(default=None, ge=0, le=1)
    #: The transforms that produced ``artifact_id`` from the original asset, in order. The
    #: runtime inverts them when it assembles the result, so ``bbox``/``point`` are always in
    #: the original asset's coordinate system; the chain stays for provenance.
    transform_chain: list[Transform] = []


class DocumentRegionEvidence(_EvidenceBase):
    kind: Literal["document_region"] = "document_region"
    page: int = Field(ge=1)
    bbox: BBox | None = None  # None = page-level evidence only
    coordinate_system: CoordinateSystem = CoordinateSystem.pdf_points_tl
    text: str | None = None
    #: How well the quoted text matched the page text (anchoring score); 1.0 = exact.
    anchor_confidence: float | None = Field(default=None, ge=0, le=1)


class ImageRegionEvidence(_EvidenceBase):
    kind: Literal["image_region"] = "image_region"
    bbox: BBox | None = None
    point: Point | None = None
    mask_artifact_id: ArtifactId | None = None
    coordinate_system: CoordinateSystem = CoordinateSystem.pixels
    label: str | None = None


class AudioSpanEvidence(_EvidenceBase):
    kind: Literal["audio_span"] = "audio_span"
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    segment_id: str | None = None
    speaker: str | None = None
    text: str | None = None


class VideoSpanEvidence(_EvidenceBase):
    kind: Literal["video_span"] = "video_span"
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    scene_id: str | None = None
    frame_artifact_ids: list[ArtifactId] = []
    track_id: str | None = None
    text: str | None = None


class TextSpanEvidence(_EvidenceBase):
    """A character range in a text/JSON input or in a derived text artifact."""

    kind: Literal["text_span"] = "text_span"
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    text: str | None = None


class CalculationEvidence(_EvidenceBase):
    """A deterministic computation: the expression and the evidence ids of its inputs."""

    kind: Literal["calculation"] = "calculation"
    expression: str
    inputs: list[EvidenceId] = []
    value: float | int | str | bool | None = None


class LineageEvidence(_EvidenceBase):
    """Points at a parent artifact and the transformation that produced the current one."""

    kind: Literal["lineage"] = "lineage"
    parent_artifact_id: ArtifactId


Evidence = Annotated[
    DocumentRegionEvidence
    | ImageRegionEvidence
    | AudioSpanEvidence
    | VideoSpanEvidence
    | TextSpanEvidence
    | CalculationEvidence
    | LineageEvidence,
    Field(discriminator="kind"),
]

__all__ = [
    "AudioSpanEvidence",
    "CalculationEvidence",
    "DocumentRegionEvidence",
    "Evidence",
    "ImageRegionEvidence",
    "LineageEvidence",
    "TextSpanEvidence",
    "VideoSpanEvidence",
]
