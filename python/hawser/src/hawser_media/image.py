"""Image probing with decompression-bomb protection. Decoding is deferred until needed."""

from __future__ import annotations

import io

from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel

from hawser_media.errors import MediaError

# We enforce pixel limits ourselves (admission policy), so Pillow's global guard is disabled.
Image.MAX_IMAGE_PIXELS = None

_EXIF_ORIENTATION = 0x0112


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


__all__ = ["ImageInfo", "probe"]
