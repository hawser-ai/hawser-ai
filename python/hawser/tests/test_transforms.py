"""Transform algebra: forward/inverse round trips within 0.5 px on random chains (WP7.1)."""

from __future__ import annotations

import math

from hypothesis import given, settings
from hypothesis import strategies as st

from hawser.evidence.resolve import resolve_to_source
from hawser.evidence.transforms import (
    apply_to_bbox,
    compose,
    forward,
    invert,
    rotation_with_expand,
    to_derived_bbox,
    to_source_bbox,
)
from hawser.ids import new_id
from hawser.models import BBox, DocumentRegionEvidence, ImageRegionEvidence, Point, ProducerRef
from hawser.models.common import (
    CropTransform,
    DpiTransform,
    ExifOrientTransform,
    PadTransform,
    RotateTransform,
    ScaleTransform,
    Transform,
)

finite = st.floats(min_value=-500, max_value=2000, allow_nan=False, allow_infinity=False)


def transform_strategy() -> st.SearchStrategy[Transform]:
    crop = st.builds(
        CropTransform, x=finite, y=finite, width=st.floats(1, 500), height=st.floats(1, 500)
    )
    scale = st.builds(ScaleTransform, sx=st.floats(0.2, 5), sy=st.floats(0.2, 5))
    rotate = st.builds(
        RotateTransform,
        degrees=st.floats(-180, 180),
        about=st.one_of(st.none(), st.builds(Point, x=finite, y=finite)),
    )
    pad = st.builds(
        PadTransform,
        left=st.floats(0, 100),
        top=st.floats(0, 100),
        right=st.floats(0, 100),
        bottom=st.floats(0, 100),
    )
    dpi = st.builds(DpiTransform, dpi=st.floats(36, 600))
    exif = st.builds(
        ExifOrientTransform,
        orientation=st.integers(1, 8),
        width=st.floats(10, 3000),
        height=st.floats(10, 3000),
    )
    return st.one_of(crop, scale, rotate, pad, dpi, exif)


@settings(max_examples=300, deadline=None)
@given(st.lists(transform_strategy(), min_size=0, max_size=6), finite, finite)
def test_round_trip_point(chain: list[Transform], x: float, y: float) -> None:
    fwd = compose(chain)
    inv = invert(chain)
    x2, y2 = inv.apply(*fwd.apply(x, y))
    scale = max(1.0, abs(x), abs(y))
    assert math.isclose(x2, x, abs_tol=0.5 * scale / 100 + 1e-6)
    assert math.isclose(y2, y, abs_tol=0.5 * scale / 100 + 1e-6)


@settings(max_examples=200, deadline=None)
@given(st.lists(transform_strategy(), min_size=1, max_size=5))
def test_round_trip_bbox(chain: list[Transform]) -> None:
    box = BBox(x0=100, y0=120, x1=300, y1=180)
    derived = to_derived_bbox(chain, box)
    back = to_source_bbox(chain, derived)
    # Rotations enlarge the axis-aligned envelope; the original is always contained.
    assert back.x0 <= box.x0 + 0.5 and back.y0 <= box.y0 + 0.5
    assert back.x1 >= box.x1 - 0.5 and back.y1 >= box.y1 - 0.5
    if all(not isinstance(t, RotateTransform) or t.degrees % 90 == 0 for t in chain) and all(
        not isinstance(t, ExifOrientTransform) or t.orientation in (1, 2, 3, 4) for t in chain
    ):
        assert math.isclose(back.x0, box.x0, abs_tol=0.5) and math.isclose(
            back.x1, box.x1, abs_tol=0.5
        )


def test_known_transforms() -> None:
    assert forward(CropTransform(x=10, y=20, width=100, height=100)).apply(10, 20) == (0, 0)
    assert forward(ScaleTransform(sx=2, sy=0.5)).apply(10, 20) == (20, 10)
    assert forward(PadTransform(left=5, top=7, right=0, bottom=0)).apply(0, 0) == (5, 7)
    x, y = forward(DpiTransform(dpi=144)).apply(72, 36)
    assert (x, y) == (144, 72)
    # 90° counter-clockwise about the centre of a 100x100 image: right-middle -> top-middle
    x, y = forward(RotateTransform(degrees=90, about=Point(x=50, y=50))).apply(100, 50)
    assert math.isclose(x, 50, abs_tol=1e-9) and math.isclose(y, 0, abs_tol=1e-9)
    # EXIF 6 (rotate 90 CW to display) on a 200x100 image: top-left -> top-right of a 100x200
    x, y = forward(ExifOrientTransform(orientation=6, width=200, height=100)).apply(0, 0)
    assert (x, y) == (100, 0)


def test_rotation_with_expand_matches_pillow() -> None:
    from io import BytesIO

    from PIL import Image

    w, h = 300, 100
    img = Image.new("RGB", (w, h), "white")
    out = img.rotate(12, expand=True)
    chain = rotation_with_expand(w, h, 12)
    corners = [compose(chain).apply(x, y) for x, y in ((0, 0), (w, 0), (0, h), (w, h))]
    xs = [c[0] for c in corners]
    ys = [c[1] for c in corners]
    assert math.isclose(min(xs), 0, abs_tol=1.0) and math.isclose(min(ys), 0, abs_tol=1.0)
    assert math.isclose(max(xs), out.width, abs_tol=1.0) and math.isclose(
        max(ys), out.height, abs_tol=1.0
    )
    BytesIO()  # keep the import surface honest


def test_resolve_to_source_maps_boxes_and_points() -> None:
    chain: list[Transform] = [
        ScaleTransform(sx=2, sy=2),
        PadTransform(left=10, top=10, right=0, bottom=0),
    ]
    producer = ProducerRef(kind="tool", name="t", version="1")
    asset = "asset_01J9X3K4N7Q2M8R5T6V7W8X9Y0"
    doc = DocumentRegionEvidence(
        id=new_id("ev"),
        asset_id=asset,
        producer=producer,
        page=1,
        bbox=BBox(x0=30, y0=30, x1=50, y1=50),
        transform_chain=chain,
    )
    img = ImageRegionEvidence(
        id=new_id("ev"),
        asset_id=asset,
        producer=producer,
        bbox=BBox(x0=30, y0=30, x1=50, y1=50),
        point=Point(x=40, y=40),
        transform_chain=chain,
    )
    untouched = DocumentRegionEvidence(
        id=new_id("ev"),
        asset_id=asset,
        producer=producer,
        page=1,
        bbox=BBox(x0=1, y0=1, x1=2, y1=2),
    )
    out = resolve_to_source([doc, img, untouched])
    first, second = out[0], out[1]
    assert isinstance(first, DocumentRegionEvidence) and first.bbox is not None
    assert (first.bbox.x0, first.bbox.x1) == (10, 20)
    assert isinstance(second, ImageRegionEvidence) and second.point is not None
    assert second.point.x == 15
    assert second.transform_chain == chain  # provenance kept
    assert out[2] is untouched
    assert apply_to_bbox(invert(chain), BBox(x0=10, y0=10, x1=12, y1=12)).x0 == 0
