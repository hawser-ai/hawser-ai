import io

import pytest
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

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


def _table_pdf(*, names_drawn_last: bool, two_column_header: bool = False) -> bytes:
    """A statement-like table; optionally the product names are drawn after everything else,
    the way a redaction or edit tool appends replacement text to the end of the stream."""
    rows = [(f"PRODUCT {chr(65 + i)}{i}", f"{i + 2}", f"{(i + 1) * 101.25:.2f}") for i in range(8)]
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4, invariant=True)
    c.setFont("Courier", 10)
    c.drawString(40, 800, "DEMO AGENCY")
    if two_column_header:
        c.drawString(40, 786, "Date: 2026-08-31")
        c.drawString(40, 772, "Ref: X-1")
        c.drawString(320, 800, "Customer:")  # right-hand block, stored after the left one
    c.drawString(40, 740, "DESCRIPTION            QTY      AMOUNT")
    for i, (name, qty, amount) in enumerate(rows):
        y = 720 - 16 * i
        if not names_drawn_last:
            c.drawString(40, y, name)
        c.drawString(200, y, qty)
        c.drawString(260, y, amount)
    if names_drawn_last:
        for i, (name, _, _) in enumerate(rows):
            c.drawString(40, 720 - 16 * i, name)
    c.showPage()
    c.save()
    return buf.getvalue()


def test_scrambled_page_is_read_in_layout_order() -> None:
    (page,) = pdf.extract_text(_table_pdf(names_drawn_last=True))
    assert page.reading_order == "layout"
    lines = page.text.splitlines()
    row = next(line for line in lines if "PRODUCT C2" in line)
    assert row.split() == ["PRODUCT", "C2", "4", "303.75"]  # the name is back on its row
    assert len(page.text) == len(page.chars)
    assert all(c.char == t for c, t in zip(page.chars, page.text, strict=True))
    start = page.text.index("PRODUCT C2")
    name_box = page.bbox_for_range(start, start + len("PRODUCT C2"))
    qty_start = page.text.index(" 4 ", start) + 1
    qty_box = page.bbox_for_range(qty_start, qty_start + 1)
    assert name_box is not None and qty_box is not None
    assert abs(name_box[1] - qty_box[1]) < 2  # same row on the page


def test_well_ordered_pages_keep_their_stored_text() -> None:
    for data in (
        _table_pdf(names_drawn_last=False),
        _table_pdf(names_drawn_last=False, two_column_header=True),
    ):
        (page,) = pdf.extract_text(data)
        assert page.reading_order == "content"
        assert "PRODUCT C2" in page.text
