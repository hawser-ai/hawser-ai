"""Coordinate-transform algebra (blueprint §6, §15; plan WP7.1).

A *chain* is the ordered list of transforms that produced a derived artifact from its source:
``[crop, scale, rotate, pad, ...]``. Each transform is an affine map from source coordinates
to derived coordinates (``forward``); the runtime inverts the chain to carry evidence found on
a derived image back to the original asset. Rotation uses Pillow's convention: positive degrees
rotate the displayed image counter-clockwise about ``about`` (default: the origin).

Everything is pure Python (2x3 affine matrices) so it runs anywhere the models do.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from hawser.models.common import (
    BBox,
    CropTransform,
    DpiTransform,
    ExifOrientTransform,
    PadTransform,
    Point,
    RotateTransform,
    ScaleTransform,
    Transform,
)


@dataclass(frozen=True)
class Affine:
    """``x' = a*x + b*y + c``, ``y' = d*x + e*y + f``."""

    a: float = 1.0
    b: float = 0.0
    c: float = 0.0
    d: float = 0.0
    e: float = 1.0
    f: float = 0.0

    def apply(self, x: float, y: float) -> tuple[float, float]:
        return self.a * x + self.b * y + self.c, self.d * x + self.e * y + self.f

    def then(self, other: Affine) -> Affine:
        """``self`` first, then ``other``."""
        return Affine(
            a=other.a * self.a + other.b * self.d,
            b=other.a * self.b + other.b * self.e,
            c=other.a * self.c + other.b * self.f + other.c,
            d=other.d * self.a + other.e * self.d,
            e=other.d * self.b + other.e * self.e,
            f=other.d * self.c + other.e * self.f + other.f,
        )

    def inverse(self) -> Affine:
        det = self.a * self.e - self.b * self.d
        if abs(det) < 1e-12:
            raise ValueError("transform is not invertible")
        ia, ib, id_, ie = self.e / det, -self.b / det, -self.d / det, self.a / det
        return Affine(
            a=ia, b=ib, c=-(ia * self.c + ib * self.f), d=id_, e=ie, f=-(id_ * self.c + ie * self.f)
        )

    @property
    def is_identity(self) -> bool:
        return all(
            abs(u - v) < 1e-9
            for u, v in zip(
                (self.a, self.b, self.c, self.d, self.e, self.f), (1, 0, 0, 0, 1, 0), strict=True
            )
        )


IDENTITY = Affine()


def forward(t: Transform) -> Affine:  # noqa: PLR0911 - one branch per transform kind
    """Source → derived coordinates for one transform."""
    if isinstance(t, CropTransform):
        return Affine(c=-t.x, f=-t.y)
    if isinstance(t, ScaleTransform):
        return Affine(a=t.sx, e=t.sy)
    if isinstance(t, PadTransform):
        return Affine(c=t.left, f=t.top)
    if isinstance(t, DpiTransform):
        s = t.dpi / 72.0
        return Affine(a=s, e=s)
    if isinstance(t, RotateTransform):
        cx, cy = (t.about.x, t.about.y) if t.about else (0.0, 0.0)
        rad = math.radians(t.degrees)
        cos, sin = math.cos(rad), math.sin(rad)
        # y points down: a counter-clockwise rotation on screen.
        rot = Affine(a=cos, b=sin, d=-sin, e=cos)
        return Affine(c=-cx, f=-cy).then(rot).then(Affine(c=cx, f=cy))
    if isinstance(t, ExifOrientTransform):
        return _exif_forward(t)
    # TimeOffsetTransform: temporal only, spatial identity.
    return IDENTITY


def _exif_forward(t: ExifOrientTransform) -> Affine:  # noqa: PLR0911 - one case per value
    """Applying EXIF orientation ``n`` to a ``width`` x ``height`` image (the upright result)."""
    w, h = float(t.width or 0), float(t.height or 0)
    match t.orientation:
        case 1:
            return IDENTITY
        case 2:  # mirror horizontal
            return Affine(a=-1, c=w)
        case 3:  # rotate 180
            return Affine(a=-1, c=w, e=-1, f=h)
        case 4:  # mirror vertical
            return Affine(e=-1, f=h)
        case 5:  # transpose (mirror horizontal + rotate 270 CW)
            return Affine(a=0, b=1, d=1, e=0)
        case 6:  # rotate 90 CW to display: (x, y) -> (h - y, x)
            return Affine(a=0, b=-1, c=h, d=1, e=0)
        case 7:  # transverse
            return Affine(a=0, b=-1, c=h, d=-1, e=0, f=w)
        case 8:  # rotate 90 CCW to display: (x, y) -> (y, w - x)
            return Affine(a=0, b=1, d=-1, e=0, f=w)
        case _:
            raise ValueError(f"bad EXIF orientation {t.orientation}")


def compose(chain: Sequence[Transform]) -> Affine:
    """Source → derived for the whole chain (applied in order)."""
    out = IDENTITY
    for t in chain:
        out = out.then(forward(t))
    return out


def invert(chain: Sequence[Transform]) -> Affine:
    """Derived → source for the whole chain."""
    return compose(chain).inverse()


def apply_to_point(aff: Affine, p: Point) -> Point:
    x, y = aff.apply(p.x, p.y)
    return Point(x=x, y=y)


def apply_to_bbox(aff: Affine, bbox: BBox) -> BBox:
    """Map the four corners and return their axis-aligned envelope."""
    corners = [
        aff.apply(x, y)
        for x, y in ((bbox.x0, bbox.y0), (bbox.x1, bbox.y0), (bbox.x0, bbox.y1), (bbox.x1, bbox.y1))
    ]
    xs = [c[0] for c in corners]
    ys = [c[1] for c in corners]
    return BBox(x0=min(xs), y0=min(ys), x1=max(xs), y1=max(ys))


def to_source_bbox(chain: Sequence[Transform], bbox: BBox) -> BBox:
    """A box on the derived artifact → the same region on the chain's source."""
    return apply_to_bbox(invert(chain), bbox)


def to_derived_bbox(chain: Sequence[Transform], bbox: BBox) -> BBox:
    return apply_to_bbox(compose(chain), bbox)


def to_source_point(chain: Sequence[Transform], p: Point) -> Point:
    return apply_to_point(invert(chain), p)


def rotation_with_expand(width: int, height: int, degrees: float) -> list[Transform]:
    """The chain Pillow's ``rotate(degrees, expand=True)`` applies: a rotation about the
    centre followed by the pad that keeps the enlarged canvas at non-negative coordinates."""
    rot = RotateTransform(degrees=degrees, about=Point(x=width / 2, y=height / 2))
    aff = forward(rot)
    corners = [aff.apply(x, y) for x, y in ((0, 0), (width, 0), (0, height), (width, height))]
    xs = [c[0] for c in corners]
    ys = [c[1] for c in corners]
    # Pillow: the new canvas is ceil(max) - floor(min) and the image is re-centred in it.
    new_w = math.ceil(max(xs)) - math.floor(min(xs))
    new_h = math.ceil(max(ys)) - math.floor(min(ys))
    pad = PadTransform(left=(new_w - width) / 2, top=(new_h - height) / 2, right=0.0, bottom=0.0)
    return [rot, pad]


__all__ = [
    "IDENTITY",
    "Affine",
    "apply_to_bbox",
    "apply_to_point",
    "compose",
    "forward",
    "invert",
    "rotation_with_expand",
    "to_derived_bbox",
    "to_source_bbox",
    "to_source_point",
]
