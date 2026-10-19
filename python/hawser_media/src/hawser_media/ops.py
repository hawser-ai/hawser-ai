"""Image operations that report how they moved pixels (plan WP7.3 ``image.ops``).

Every function returns the new PNG plus a *transform chain*: the ordered list of transforms
(as plain dicts matching ``hawser.models.common.Transform``) that map source coordinates to
the result. The runtime inverts them to carry evidence back to the original.
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageOps

from hawser_media.errors import MediaError

TransformDict = dict[str, Any]


@dataclass
class OpResult:
    png: bytes
    width: int
    height: int
    chain: list[TransformDict] = field(default_factory=list)
    info: dict[str, Any] = field(default_factory=dict)


def _open(data: bytes) -> Image.Image:
    try:
        return Image.open(io.BytesIO(data))
    except Exception as exc:
        raise MediaError("image_corrupt", f"cannot open image: {exc}") from exc


def _png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=False)
    return buf.getvalue()


def exif_orient(data: bytes) -> OpResult:
    """Apply the EXIF orientation so the pixels are upright; records ``exif_orient``."""
    img = _open(data)
    orientation = 1
    try:
        orientation = int(img.getexif().get(0x0112, 1) or 1)
    except Exception:
        orientation = 1
    width, height = img.size
    upright = ImageOps.exif_transpose(img) if orientation != 1 else img
    chain: list[TransformDict] = []
    if orientation != 1:
        chain.append(
            {"kind": "exif_orient", "orientation": orientation, "width": width, "height": height}
        )
    out = upright.convert("RGB") if upright.mode not in ("RGB", "RGBA", "L") else upright
    return OpResult(
        png=_png(out),
        width=out.width,
        height=out.height,
        chain=chain,
        info={"orientation": orientation},
    )


def crop(data: bytes, x: float, y: float, w: float, h: float) -> OpResult:
    img = _open(data)
    x0, y0 = max(0, round(x)), max(0, round(y))
    x1, y1 = min(img.width, round(x + w)), min(img.height, round(y + h))
    if x1 <= x0 or y1 <= y0:
        raise MediaError("crop_empty", "crop box is empty or outside the image")
    out = img.crop((x0, y0, x1, y1))
    return OpResult(
        png=_png(out),
        width=out.width,
        height=out.height,
        chain=[{"kind": "crop", "x": x0, "y": y0, "width": x1 - x0, "height": y1 - y0}],
    )


def resize(
    data: bytes, *, width: int | None = None, height: int | None = None, scale: float | None = None
) -> OpResult:
    img = _open(data)
    if scale is not None:
        sx = sy = scale
    elif width and height:
        sx, sy = width / img.width, height / img.height
    elif width:
        sx = sy = width / img.width
    elif height:
        sx = sy = height / img.height
    else:
        raise MediaError("resize_args", "give width, height or scale")
    out = img.resize(
        (max(1, round(img.width * sx)), max(1, round(img.height * sy))),
        Image.Resampling.LANCZOS,
    )
    return OpResult(
        png=_png(out),
        width=out.width,
        height=out.height,
        chain=[{"kind": "scale", "sx": out.width / img.width, "sy": out.height / img.height}],
    )


def rotate(
    data: bytes, degrees: float, *, fill: tuple[int, int, int] = (255, 255, 255)
) -> OpResult:
    """Rotate counter-clockwise by ``degrees`` with an expanded canvas; records rotate + pad."""
    img = _open(data).convert("RGB")
    width, height = img.size
    out = img.rotate(degrees, resample=Image.Resampling.BICUBIC, expand=True, fillcolor=fill)
    chain = rotation_chain(width, height, degrees, out.width, out.height)
    return OpResult(
        png=_png(out), width=out.width, height=out.height, chain=chain, info={"degrees": degrees}
    )


def rotation_chain(
    width: int, height: int, degrees: float, out_w: int, out_h: int
) -> list[TransformDict]:
    """``rotate`` about the centre, then the ``pad`` Pillow applies to centre the result."""
    return [
        {"kind": "rotate", "degrees": degrees, "about": {"x": width / 2, "y": height / 2}},
        {
            "kind": "pad",
            "left": (out_w - width) / 2,
            "top": (out_h - height) / 2,
            "right": 0.0,
            "bottom": 0.0,
        },
    ]


def estimate_skew(data: bytes, *, max_degrees: float = 15.0, step: float = 0.5) -> float:
    """Projection-profile skew estimate: the angle whose row sums have the highest variance.
    Positive means the text runs upward to the right (rotate by -angle... see ``deskew``)."""
    img = _open(data).convert("L")
    # Work on a downscaled, binarised copy for speed.
    scale = min(1.0, 1000 / max(img.size))
    small = img.resize((max(8, int(img.width * scale)), max(8, int(img.height * scale))))
    arr = np.asarray(small, dtype=np.float32)
    ink = (arr < 128).astype(np.float32)
    if ink.sum() < 10:
        return 0.0
    best_angle, best_score = 0.0, -1.0
    angle = -max_degrees
    while angle <= max_degrees + 1e-9:
        rotated = Image.fromarray((ink * 255).astype(np.uint8)).rotate(
            angle, resample=Image.Resampling.BILINEAR, expand=False, fillcolor=0
        )
        rows = np.asarray(rotated, dtype=np.float32).sum(axis=1)
        score = float(rows.var())
        if score > best_score:
            best_score, best_angle = score, angle
        angle += step
    # Refine around the best coarse angle.
    fine = best_angle
    for delta in (-0.3, -0.2, -0.1, 0.1, 0.2, 0.3):
        a = best_angle + delta
        rotated = Image.fromarray((ink * 255).astype(np.uint8)).rotate(
            a, resample=Image.Resampling.BILINEAR, expand=False, fillcolor=0
        )
        score = float(np.asarray(rotated, dtype=np.float32).sum(axis=1).var())
        if score > best_score:
            best_score, fine = score, a
    return round(fine, 2)


def deskew(data: bytes, *, threshold_degrees: float = 0.4, max_degrees: float = 15.0) -> OpResult:
    """Straighten text lines. The chain is empty when the image was already straight."""
    angle = estimate_skew(data, max_degrees=max_degrees)
    img = _open(data)
    if abs(angle) < threshold_degrees:
        out = img.convert("RGB")
        return OpResult(
            png=_png(out),
            width=out.width,
            height=out.height,
            chain=[],
            info={"skew_degrees": angle, "applied": 0.0},
        )
    res = rotate(data, angle)
    res.info = {"skew_degrees": angle, "applied": angle}
    return res


def normalise(data: bytes, *, max_side: int = 2000) -> OpResult:
    """Upright, RGB, at most ``max_side`` pixels on the long edge; records the chain."""
    first = exif_orient(data)
    img = _open(first.png)
    chain = list(first.chain)
    if max(img.size) > max_side:
        second = resize(first.png, scale=max_side / max(img.size))
        chain += second.chain
        return OpResult(png=second.png, width=second.width, height=second.height, chain=chain)
    return OpResult(png=first.png, width=first.width, height=first.height, chain=chain)


def redact(
    data: bytes,
    boxes: list[tuple[float, float, float, float]],
    *,
    fill: tuple[int, int, int] = (0, 0, 0),
) -> OpResult:
    """Fill regions. Coordinates are unchanged (identity chain)."""
    img = _open(data).convert("RGB")
    draw = ImageDraw.Draw(img)
    for x0, y0, x1, y1 in boxes:
        draw.rectangle((x0, y0, x1, y1), fill=fill)
    return OpResult(
        png=_png(img), width=img.width, height=img.height, chain=[], info={"regions": len(boxes)}
    )


def annotate(
    data: bytes,
    boxes: list[tuple[float, float, float, float, str | None]],
    *,
    colour: tuple[int, int, int] = (255, 140, 0),
    width: int = 3,
) -> OpResult:
    """Draw labelled boxes (evidence overlays for reports). Identity chain."""
    img = _open(data).convert("RGB")
    draw = ImageDraw.Draw(img)
    for x0, y0, x1, y1, label in boxes:
        draw.rectangle((x0, y0, x1, y1), outline=colour, width=width)
        if label:
            draw.rectangle((x0, max(0, y0 - 14), x0 + 7 * len(label) + 6, y0), fill=colour)
            draw.text((x0 + 3, max(0, y0 - 13)), label, fill=(255, 255, 255))
    return OpResult(
        png=_png(img), width=img.width, height=img.height, chain=[], info={"regions": len(boxes)}
    )


def mask_png(width: int, height: int, boxes: list[tuple[float, float, float, float]]) -> bytes:
    """A binary mask (255 inside the boxes) as a PNG."""
    m = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(m)
    for x0, y0, x1, y1 in boxes:
        draw.rectangle((x0, y0, x1, y1), fill=255)
    return _png(m)


def rle_encode(mask_png_bytes: bytes) -> dict[str, Any]:
    """COCO-style uncompressed RLE (row-major counts starting with background)."""
    arr = (np.asarray(_open(mask_png_bytes).convert("L")) > 127).astype(np.uint8).flatten(order="C")
    counts: list[int] = []
    current, run = 0, 0
    for v in arr:
        if v == current:
            run += 1
        else:
            counts.append(run)
            current, run = int(v), 1
    counts.append(run)
    h, w = _open(mask_png_bytes).size[1], _open(mask_png_bytes).size[0]
    return {"size": [h, w], "counts": counts}


def perceptual_embedding(data: bytes, *, side: int = 16) -> list[float]:
    """A tiny, deterministic image embedding (normalised grayscale thumbnail). Good enough for
    near-duplicate similarity; a CLIP/SigLIP adapter is the upgrade path."""
    img = _open(data).convert("L").resize((side, side), Image.Resampling.LANCZOS)
    arr = np.asarray(img, dtype=np.float32).flatten()
    arr = arr - arr.mean()
    norm = float(np.linalg.norm(arr)) or 1.0
    return [round(float(v / norm), 6) for v in arr]


def cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    num = sum(x * y for x, y in zip(a, b, strict=True))
    den = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return float(num / den) if den else 0.0


__all__ = [
    "OpResult",
    "annotate",
    "cosine",
    "crop",
    "deskew",
    "estimate_skew",
    "exif_orient",
    "mask_png",
    "normalise",
    "perceptual_embedding",
    "redact",
    "resize",
    "rle_encode",
    "rotate",
    "rotation_chain",
]
