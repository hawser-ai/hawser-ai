import io

import pytest
from PIL import Image

from hawser_media import image, text
from hawser_media.errors import MediaError


def _png(w: int, h: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), "white").save(buf, format="PNG")
    return buf.getvalue()


def test_image_probe() -> None:
    info = image.probe(_png(30, 20))
    assert (info.width, info.height, info.mode, info.format, info.frames) == (
        30,
        20,
        "RGB",
        "PNG",
        1,
    )
    assert info.pixels == 600


def test_image_pixel_limit_before_decode() -> None:
    with pytest.raises(MediaError) as exc:
        image.probe(_png(100, 100), max_pixels=5000)
    assert exc.value.code == "image_too_large"


def test_image_corrupt() -> None:
    with pytest.raises(MediaError) as exc:
        image.probe(b"\x89PNG\r\n\x1a\nnope")
    assert exc.value.code == "image_corrupt"


def test_text_probe() -> None:
    info = text.probe(b"a\nb\n")
    assert (info.chars, info.lines, info.valid_json) == (4, 3, None)
    assert text.probe(b'{"x": 1}', expect_json=True).valid_json is True
    with pytest.raises(MediaError) as exc:
        text.probe(b"{", expect_json=True)
    assert exc.value.code == "json_invalid"
    with pytest.raises(MediaError) as exc:
        text.probe(b"\xff\xfe")
    assert exc.value.code == "text_not_utf8"
