import io

import pytest
from PIL import Image

from hawser_media import MediaKind, sniff
from hawser_synth.invoice import GeneratedInvoice


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), "white").save(buf, format="PNG")
    return buf.getvalue()


def test_pdf(invoice: GeneratedInvoice) -> None:
    s = sniff(invoice.pdf)
    assert s is not None
    assert (s.media_type, s.kind) == ("application/pdf", MediaKind.pdf)


def test_png() -> None:
    s = sniff(_png(), filename="not-a-png.pdf")  # extension is ignored
    assert s is not None
    assert (s.media_type, s.kind) == ("image/png", MediaKind.image)


@pytest.mark.parametrize(
    ("data", "filename", "media_type", "kind"),
    [
        (b'{"a": 1}', None, "application/json", MediaKind.json),
        (b"[1, 2]", "x.txt", "application/json", MediaKind.json),
        (b"{not json", None, "text/plain", MediaKind.text),
        (b"# Title\n\nbody", "notes.md", "text/markdown", MediaKind.text),
        (b"plain words", "notes.md.bak", "text/plain", MediaKind.text),
    ],
)
def test_text_family(data: bytes, filename: str | None, media_type: str, kind: MediaKind) -> None:
    s = sniff(data, filename=filename)
    assert s is not None
    assert (s.media_type, s.kind) == (media_type, kind)


def test_unsupported() -> None:
    assert sniff(b"\x00\x01\x02\xff\xfe garbage") is None
    assert sniff("héllo".encode("latin-1")) is None
