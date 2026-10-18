"""Inputs and admitted assets (blueprint §4)."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import Field

from hawser.clock import UtcDatetime
from hawser.ids import ArtifactId, AssetId, TenantId
from hawser.models.base import HawserModel


class MediaClass(StrEnum):
    text = "text"
    json = "json"
    pdf = "pdf"
    office = "office"
    image = "image"
    audio = "audio"
    video = "video"


# --- How a caller refers to an input --------------------------------------------------------


class PathInput(HawserModel):
    """A local file path. Resolved and uploaded by the SDK before submission."""

    kind: Literal["path"] = "path"
    path: str
    name: str | None = None


class InlineInput(HawserModel):
    """Small text or JSON given inline."""

    kind: Literal["inline"] = "inline"
    name: str | None = None
    text: str | None = None
    json_value: Any | None = Field(default=None, alias="json")


class AssetInput(HawserModel):
    """An already-admitted asset."""

    kind: Literal["asset"] = "asset"
    asset_id: AssetId
    name: str | None = None


class ArtifactInput(HawserModel):
    """A stored Hawser artifact (e.g. the output of a previous run)."""

    kind: Literal["artifact"] = "artifact"
    artifact_id: ArtifactId
    name: str | None = None


class StorageInput(HawserModel):
    """An authenticated object-storage reference (``s3://bucket/key``).

    Public URLs are not accepted (blueprint §4.1).
    """

    kind: Literal["storage"] = "storage"
    uri: Annotated[str, Field(pattern=r"^(s3|gs|az)://")]
    name: str | None = None


InputRef = Annotated[
    PathInput | InlineInput | AssetInput | ArtifactInput | StorageInput,
    Field(discriminator="kind"),
]


# --- Admission results ----------------------------------------------------------------------


class PdfProbe(HawserModel):
    page_count: int = Field(ge=0)
    encrypted: bool = False
    has_forms: bool = False
    #: Per-page character count from the native text layer; 0 means "needs OCR".
    chars_per_page: list[int] = []
    #: Per-page (width, height) in PDF points.
    page_sizes_pt: list[tuple[float, float]] = []
    estimated_render_pixels: int = Field(default=0, ge=0)


class ImageProbe(HawserModel):
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    mode: str
    exif_orientation: int | None = Field(default=None, ge=1, le=8)
    has_icc: bool = False
    frames: int = Field(default=1, ge=1)


class AudioProbe(HawserModel):
    codec: str
    container: str
    duration_ms: int = Field(ge=0)
    channels: int = Field(ge=1)
    sample_rate_hz: int = Field(gt=0)


class VideoProbe(HawserModel):
    codec: str
    container: str
    duration_ms: int = Field(ge=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    fps: float = Field(gt=0)
    audio_tracks: int = Field(ge=0)
    estimated_decoded_pixels: int = Field(ge=0)


class TextProbe(HawserModel):
    chars: int = Field(ge=0)
    valid_utf8: bool = True
    valid_json: bool | None = None


class LimitsApplied(HawserModel):
    """The admission limits in force when the asset was admitted (for reproducibility)."""

    max_bytes: int
    max_pages: int | None = None
    max_pixels: int | None = None
    max_duration_ms: int | None = None


class AssetManifest(HawserModel):
    """Everything admission learned about an input before any model saw it."""

    media_class: MediaClass
    media_type: str
    declared_media_type: str | None = None
    size_bytes: int = Field(ge=0)
    pdf: PdfProbe | None = None
    image: ImageProbe | None = None
    audio: AudioProbe | None = None
    video: VideoProbe | None = None
    text: TextProbe | None = None
    limits: LimitsApplied
    #: Execution classes judged feasible, e.g. ``document.native_text``, ``document.needs_ocr``.
    execution_classes: list[str] = []
    estimated_cost_usd: float | None = Field(default=None, ge=0)
    warnings: list[str] = []


class Asset(HawserModel):
    id: AssetId
    tenant_id: TenantId
    name: str | None = None
    media_class: MediaClass
    media_type: str
    size_bytes: int = Field(ge=0)
    #: Plain sha256 of the bytes; never exposed across tenants.
    content_hash: str
    #: Tenant-scoped hash; the dedup/artifact key.
    scoped_hash: str
    original_artifact_id: ArtifactId
    manifest: AssetManifest
    created_at: UtcDatetime


__all__ = [
    "ArtifactInput",
    "Asset",
    "AssetInput",
    "AssetManifest",
    "AudioProbe",
    "ImageProbe",
    "InlineInput",
    "InputRef",
    "LimitsApplied",
    "MediaClass",
    "PathInput",
    "PdfProbe",
    "StorageInput",
    "TextProbe",
    "VideoProbe",
]
