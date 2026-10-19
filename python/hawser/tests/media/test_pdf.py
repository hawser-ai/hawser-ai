import pytest

from hawser_media import pdf
from hawser_media.errors import MediaError
from hawser_synth.invoice import GeneratedInvoice


def test_probe(invoice: GeneratedInvoice) -> None:
    info = pdf.probe(invoice.pdf)
    assert info.page_count == 1
    assert not info.encrypted
    assert info.chars_per_page[0] > 200
    assert not info.needs_ocr(1)
    w, h = info.page_sizes_pt[0]
    assert (round(w), round(h)) == (612, 792)  # LETTER


def test_scanned_needs_ocr(scanned_invoice: GeneratedInvoice) -> None:
    info = pdf.probe(scanned_invoice.pdf)
    assert info.page_count == 1
    assert info.needs_ocr(1)


def test_corrupt() -> None:
    with pytest.raises(MediaError) as exc:
        pdf.probe(b"%PDF-1.7 this is not really a pdf")
    assert exc.value.code == "pdf_corrupt"


def test_extract_text_has_aligned_boxes(invoice: GeneratedInvoice) -> None:
    (page,) = pdf.extract_text(invoice.pdf)
    assert page.page == 1
    assert len(page.text) == len(page.chars)
    number = invoice.truth.invoice_number
    start = page.text.index(number)
    box = page.bbox_for_range(start, start + len(number))
    assert box is not None
    x0, y0, x1, y1 = box
    assert 0 <= x0 < x1 <= page.width_pt
    assert 0 <= y0 < y1 <= page.height_pt
    # The invoice number is in the header; top-left origin means a small y.
    assert y1 < page.height_pt / 2
    # Every glyph that is not whitespace has a box.
    assert all(c.has_box for c in page.chars if not c.char.isspace())


def test_lines_and_words(invoice: GeneratedInvoice) -> None:
    (page,) = pdf.extract_text(invoice.pdf)
    assert page.lines
    texts = [ln.text for ln in page.lines]
    assert any(invoice.truth.invoice_number in t for t in texts)
    for ln in page.lines:
        assert ln.words
        assert ln.text == page.text[ln.start : ln.end]
        assert ln.x0 <= ln.x1
        assert ln.y0 <= ln.y1


def test_render_page(invoice: GeneratedInvoice) -> None:
    r = pdf.render_page(invoice.pdf, 1, dpi=72)
    assert r.png[:8] == b"\x89PNG\r\n\x1a\n"
    assert (r.width_px, r.height_px) == (612, 792)
    r2 = pdf.render_page(invoice.pdf, 1, dpi=144)
    assert (r2.width_px, r2.height_px) == (1224, 1584)
    assert r2.scale == pytest.approx(2.0)


def test_page_out_of_range(invoice: GeneratedInvoice) -> None:
    with pytest.raises(MediaError) as exc:
        pdf.render_page(invoice.pdf, 2)
    assert exc.value.code == "pdf_page_out_of_range"
