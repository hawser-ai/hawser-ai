"""Shared value types: bounding boxes, coordinate systems, transforms, producers."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import Field, model_validator

from hawser.models.base import HawserModel

#: A JSON Schema document (draft 2020-12). Kept loose; validated structurally where it is used.
JsonSchema = dict[str, Any]

#: Semantic version string.
SemVer = Annotated[str, Field(pattern=r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")]

#: Slug for skill, tool and validator names: ``document.extract``, ``invoice-po-reconciliation``.
Slug = Annotated[str, Field(pattern=r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$", max_length=120)]

#: JSON pointer (RFC 6901) addressing a field in a result, e.g. ``/line_items/3/unit_price``.
JsonPointer = Annotated[str, Field(pattern=r"^(?:/(?:[^/~]|~0|~1)*)*$")]


class CoordinateSystem(StrEnum):
    """Where a box/point lives. Evidence always records this so it can be mapped back."""

    pdf_points_tl = "pdf_points_tl"  # PDF user-space points, origin top-left (normalised)
    pixels = "pixels"  # raster pixels of a specific artifact, origin top-left
    normalized = "normalized"  # 0..1 relative to the artifact's width/height


class BBox(HawserModel):
    """Axis-aligned box; ``x0,y0`` is the top-left corner, ``x1,y1`` the bottom-right."""

    x0: float
    y0: float
    x1: float
    y1: float

    @model_validator(mode="after")
    def _ordered(self) -> BBox:
        if self.x1 < self.x0 or self.y1 < self.y0:
            raise ValueError("bbox must satisfy x0 <= x1 and y0 <= y1")
        return self

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0


class Point(HawserModel):
    x: float
    y: float


# --- Transforms --------------------------------------------------------------------------
# A transform describes how an output artifact's coordinate (or time) space relates to its
# input's. Chains are composed by the runtime (Phase 7) so evidence maps back to originals.


class CropTransform(HawserModel):
    kind: Literal["crop"] = "crop"
    x: float
    y: float
    width: float = Field(gt=0)
    height: float = Field(gt=0)


class ScaleTransform(HawserModel):
    kind: Literal["scale"] = "scale"
    sx: float = Field(gt=0)
    sy: float = Field(gt=0)


class RotateTransform(HawserModel):
    kind: Literal["rotate"] = "rotate"
    degrees: float
    about: Point | None = None  # None = about the origin


class ExifOrientTransform(HawserModel):
    """The EXIF orientation applied to make a ``width`` x ``height`` image upright."""

    kind: Literal["exif_orient"] = "exif_orient"
    orientation: int = Field(ge=1, le=8)
    width: float | None = Field(default=None, gt=0)
    height: float | None = Field(default=None, gt=0)


class PadTransform(HawserModel):
    kind: Literal["pad"] = "pad"
    left: float = Field(ge=0)
    top: float = Field(ge=0)
    right: float = Field(ge=0)
    bottom: float = Field(ge=0)


class DpiTransform(HawserModel):
    """PDF points → pixels at a given DPI (72 points per inch)."""

    kind: Literal["dpi"] = "dpi"
    dpi: float = Field(gt=0)


class TimeOffsetTransform(HawserModel):
    """Output time t maps to input time ``t + offset_ms`` (for clips and chunks)."""

    kind: Literal["time_offset"] = "time_offset"
    offset_ms: int


Transform = Annotated[
    CropTransform
    | ScaleTransform
    | RotateTransform
    | ExifOrientTransform
    | PadTransform
    | DpiTransform
    | TimeOffsetTransform,
    Field(discriminator="kind"),
]


class ProducerRef(HawserModel):
    """What produced an artifact, evidence item or result: a tool, a model or the runtime."""

    kind: Literal["tool", "model", "system"]
    name: str
    version: str | None = None
    #: Hash of the full configuration (prompt, parameters, adapter settings) for reproducibility.
    config_hash: str | None = None


__all__ = [
    "BBox",
    "CoordinateSystem",
    "CropTransform",
    "DpiTransform",
    "ExifOrientTransform",
    "JsonPointer",
    "JsonSchema",
    "PadTransform",
    "Point",
    "ProducerRef",
    "RotateTransform",
    "ScaleTransform",
    "SemVer",
    "Slug",
    "TimeOffsetTransform",
    "Transform",
]
