"""Image ops, OCR engines and layout analysis (WP7.2/7.3)."""

from __future__ import annotations

import io
import json
import math

import httpx
import pytest
from PIL import Image, ImageDraw, ImageFont

from hawser_media import layout, ocr, ops, pdf
from hawser_synth import generate_invoice


def _text_image(
    lines: list[str], *, size: tuple[int, int] = (900, 400), rotate: float = 0.0
) -> bytes:
    img = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default(size=28)
    y = 40
    for ln in lines:
        draw.text((40, y), ln, fill="black", font=font)
        y += 48
    if rotate:
        img = img.rotate(rotate, expand=True, fillcolor="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_ops_record_chains_and_round_trip() -> None:
    png = _text_image(["hello"])
    c = ops.crop(png, 10, 20, 200, 100)
    assert c.chain == [{"kind": "crop", "x": 10, "y": 20, "width": 200, "height": 100}] and (
        c.width,
        c.height,
    ) == (200, 100)
    r = ops.resize(png, scale=0.5)
    assert r.chain[0]["kind"] == "scale" and math.isclose(r.chain[0]["sx"], 0.5, abs_tol=0.01)
    rot = ops.rotate(png, 12)
    assert [t["kind"] for t in rot.chain] == ["rotate", "pad"] and rot.width > 900
    n = ops.normalise(png, max_side=450)
    assert n.chain[-1]["kind"] == "scale" and max(n.width, n.height) == 450
    red = ops.redact(png, [(0, 0, 50, 50)])
    assert red.chain == [] and Image.open(io.BytesIO(red.png)).getpixel((10, 10)) == (0, 0, 0)
    ann = ops.annotate(png, [(100, 100, 300, 200, "total")])
    assert ann.info["regions"] == 1
    mask = ops.mask_png(100, 50, [(10, 10, 20, 20)])
    rle = ops.rle_encode(mask)
    assert rle["size"] == [50, 100] and sum(rle["counts"]) == 5000
    emb = ops.perceptual_embedding(png)
    assert len(emb) == 256 and math.isclose(ops.cosine(emb, emb), 1.0, abs_tol=1e-6)
    assert (
        ops.cosine(
            emb, ops.perceptual_embedding(_text_image(["completely different", "text here"]))
        )
        < 0.99
    )


def test_deskew_recovers_rotation() -> None:
    skewed = _text_image(
        ["Invoice INV-2026-00011", "Total 3,569.76 EUR", "Vendor Northwind Components"], rotate=-6.0
    )
    angle = ops.estimate_skew(skewed)
    assert math.isclose(angle, 6.0, abs_tol=1.0)
    out = ops.deskew(skewed)
    assert (
        out.chain
        and out.chain[0]["kind"] == "rotate"
        and math.isclose(out.chain[0]["degrees"], 6.0, abs_tol=1.0)
    )
    straight = ops.deskew(_text_image(["straight text"]))
    assert straight.chain == []


def test_page_text_from_ocr_builds_char_boxes() -> None:
    result = ocr.OcrResult(
        engine="fake",
        width=100,
        height=50,
        lines=[
            ocr.OcrLine(
                text="Total 12",
                x0=10,
                y0=10,
                x1=90,
                y1=20,
                confidence=0.9,
                words=[
                    ocr.OcrWord(text="Total", x0=10, y0=10, x1=50, y1=20, confidence=0.9),
                    ocr.OcrWord(text="12", x0=60, y0=10, x1=90, y1=20, confidence=0.8),
                ],
            ),
            ocr.OcrLine(
                text="Line two",
                x0=10,
                y0=30,
                x1=90,
                y1=40,
                confidence=0.7,
                words=ocr.split_line_into_words("Line two", (10, 30, 90, 40), 0.7),
            ),
        ],
    )
    pt = ocr.page_text_from_ocr(result, page=1, scale=0.5)
    assert pt.text == "Total 12\nLine two\n"
    assert len(pt.chars) == len(pt.text)
    assert pt.chars[0].x0 == 5.0 and pt.chars[5].x0 is None  # the space has no box
    assert pt.lines[0].words[1].confidence == 0.8
    assert pt.bbox_for_range(6, 8) == (30.0, 5.0, 45.0, 10.0)


@pytest.mark.skipif(not ocr.RapidOcrEngine.available(), reason="rapidocr not installed")
def test_rapidocr_golden_on_rasterised_synthetic_page() -> None:
    """Word recall ≥ 0.98 at 300 dpi on a rasterised synthetic invoice (plan WP7 tests)."""
    inv = generate_invoice(3, layout=0)
    native = pdf.extract_text(inv.pdf)[0]
    rendered = pdf.render_page(inv.pdf, 1, dpi=300)
    result = ocr.RapidOcrEngine().recognize(rendered.png)
    assert result.engine == "rapidocr" and result.lines

    def norm(s: str) -> str:
        return s.strip(",.:;").replace("€", "").replace("$", "").replace("£", "")

    expected = {
        norm(w.text)
        for ln in native.lines
        for w in ln.words
        if len(norm(w.text)) >= 3 and norm(w.text).isascii()  # the bundled rec model is Latin
    }
    got = " ".join(result.text.split()).replace("€", "")
    found = sum(1 for w in expected if w in got)
    recall = found / len(expected)
    assert recall >= 0.98, (
        f"recall {recall:.3f}: missing {sorted(w for w in expected if w not in got)[:10]}"
    )
    # boxes come back in page points when scaled
    pt = ocr.page_text_from_ocr(result, page=1, scale=72 / rendered.dpi)
    assert pt.width_pt == pytest.approx(native.width_pt, abs=1) and all(
        c.x1 is None or c.x1 <= pt.width_pt + 1 for c in pt.chars
    )


def test_azure_adapter_with_mock_transport() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}")
        if request.method == "POST":
            return httpx.Response(202, headers={"Operation-Location": "https://x.test/op/1"})
        if len(calls) == 2:
            return httpx.Response(200, json={"status": "running"})
        return httpx.Response(
            200,
            json={
                "status": "succeeded",
                "analyzeResult": {
                    "pages": [
                        {
                            "unit": "pixel",
                            "width": 100,
                            "height": 50,
                            "words": [
                                {
                                    "content": "Total",
                                    "polygon": [10, 10, 50, 10, 50, 20, 10, 20],
                                    "confidence": 0.95,
                                },
                                {
                                    "content": "12",
                                    "polygon": [60, 10, 90, 10, 90, 20, 60, 20],
                                    "confidence": 0.9,
                                },
                            ],
                            "lines": [
                                {"content": "Total 12", "polygon": [10, 10, 90, 10, 90, 20, 10, 20]}
                            ],
                        }
                    ]
                },
            },
        )

    png = _text_image(["Total 12"], size=(100, 50))
    engine = ocr.AzureDocumentIntelligenceEngine(
        endpoint="https://x.test",
        api_key="k",
        poll_interval_s=0,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    result = engine.recognize(png)
    assert result.engine == "azure-document-intelligence" and result.lines[0].text == "Total 12"
    assert [w.text for w in result.words] == ["Total", "12"] and result.words[1].confidence == 0.9
    assert calls[0].startswith("POST") and len(calls) == 3


def test_tesseract_or_skip() -> None:
    if not ocr.TesseractEngine.available():
        assert "tesseract" not in ocr.available_engines()
        pytest.skip("tesseract not installed")
    result = ocr.TesseractEngine().recognize(_text_image(["Hello world"]))
    assert "Hello" in result.text


def test_layout_on_native_invoice() -> None:
    inv = generate_invoice(5, layout=0)
    page = pdf.extract_text(inv.pdf)[0]
    out = layout.analyse(page)
    kinds = [b.kind for b in out.blocks]
    assert "table" in kinds, kinds
    table = max(out.tables, key=lambda t: (t.cols, t.rows))
    assert table.rows >= 3 and table.cols >= 3, (table.rows, table.cols)
    grid = table.grid()
    assert any(any(cell for cell in row) for row in grid)
    joined = " ".join(f"{kv.key}={kv.value}" for kv in out.key_values)
    assert "Invoice" in joined or "Total" in joined or len(out.key_values) >= 2
    assert [b.reading_order for b in out.blocks] == list(range(len(out.blocks)))
    assert json.loads(out.model_dump_json())["page"] == 1
