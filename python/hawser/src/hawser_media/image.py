"""Image probing with decompression-bomb protection. Decoding is deferred until needed."""

from __future__ import annotations

import io

from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel

from hawser_media.errors import MediaError

# We enforce pixel limits ourselves (admission policy), so Pillow's global guard is disabled.
Image.MAX_IMAGE_PIXELS = None

_EXIF_ORIENTATION = 0x0112

#: Images sent to a model are kept within the strictest provider limits: Anthropic accepts at
#: most 5 MB per image *after* base64 encoding (3.75 MB raw), and downscales anything longer
#: than about 1,568 px anyway, so larger images only cost bandwidth.
MODEL_IMAGE_MAX_SIDE = 2000
MODEL_IMAGE_MAX_BYTES = 3_700_000


class ImageInfo(BaseModel):
    width: int
    height: int
    mode: str
    format: str | None = None
    exif_orientation: int | None = None
    has_icc: bool = False
    frames: int = 1

    @property
    def pixels(self) -> int:
        return self.width * self.height


def probe(data: bytes, *, max_pixels: int | None = None) -> ImageInfo:
    """Read the header only. Raises ``MediaError('image_too_large')`` before any decode."""
    try:
        img = Image.open(io.BytesIO(data))
    except UnidentifiedImageError as exc:
        raise MediaError("image_corrupt", "cannot identify image") from exc
    except Exception as exc:
        raise MediaError("image_corrupt", f"cannot open image: {exc}") from exc
    width, height = img.size
    if max_pixels is not None and width * height > max_pixels:
        raise MediaError(
            "image_too_large", f"{width}x{height} exceeds the pixel limit of {max_pixels}"
        )
    orientation: int | None = None
    try:
        raw = img.getexif().get(_EXIF_ORIENTATION)
        orientation = int(raw) if raw is not None and 1 <= int(raw) <= 8 else None
    except Exception:
        orientation = None
    frames = int(getattr(img, "n_frames", 1))
    return ImageInfo(
        width=width,
        height=height,
        mode=img.mode,
        format=img.format,
        exif_orientation=orientation,
        has_icc="icc_profile" in img.info,
        frames=frames,
    )


def fit_for_model(
    data: bytes,
    media_type: str,
    *,
    max_side: int = MODEL_IMAGE_MAX_SIDE,
    max_bytes: int = MODEL_IMAGE_MAX_BYTES,
) -> tuple[bytes, str]:
    """Return ``(data, media_type)`` small enough to send to any model provider.

    An image already within ``max_side`` pixels and ``max_bytes`` is returned unchanged, byte
    for byte (recordings keyed on its hash keep matching). Otherwise it is scaled to fit
    ``max_side`` and re-encoded, PNG first (sharp text) and then JPEG at falling quality and
    size, until it fits. Only the copy sent to the model changes; stored artifacts and the
    evidence drawn on them keep their full resolution.
    """
    try:
        img = Image.open(io.BytesIO(data))
        size = img.size
    except (UnidentifiedImageError, OSError) as exc:
        raise MediaError("image_unreadable", f"cannot read image for the model: {exc}") from exc
    if len(data) <= max_bytes and max(size) <= max_side:
        return data, media_type

    img = ImageOps.exif_transpose(img)
    img = img.convert("RGB") if img.mode not in ("RGB", "L") else img
    scale = min(1.0, max_side / max(img.size))
    for attempt in range(6):
        w, h = max(1, round(img.width * scale)), max(1, round(img.height * scale))
        resized = img.resize((w, h), Image.Resampling.LANCZOS) if (w, h) != img.size else img
        if attempt == 0:
            out = io.BytesIO()
            resized.save(out, format="PNG", optimize=True)
            if out.tell() <= max_bytes:
                return out.getvalue(), "image/png"
        for quality in (85, 70):
            out = io.BytesIO()
            resized.save(out, format="JPEG", quality=quality, optimize=True)
            if out.tell() <= max_bytes:
                return out.getvalue(), "image/jpeg"
        scale *= 0.75
    raise MediaError("image_too_large", "image could not be reduced below the model size limit")


__all__ = ["MODEL_IMAGE_MAX_BYTES", "MODEL_IMAGE_MAX_SIDE", "ImageInfo", "fit_for_model", "probe"]
