from __future__ import annotations

import pytest

from hawser.evidence import DocumentIndex, anchor_all, build_document_index, locate, normalise
from hawser.evidence.anchor import Match, locate_all
from hawser.structured import Anchor
from hawser_media import pdf
from hawser_synth.invoice import GeneratedInvoice


def test_normalise_maps_back_to_original_offsets() -> None:
    text = "Invoice\r\n  No: “INV-1”"
    norm, mapping = normalise(text)
    assert norm == 'invoice no: "inv-1"'
    assert len(norm) == len(mapping)
    # the quote char maps to the original curly quote position
    assert text[mapping[norm.index('"')]] == "“"


@pytest.fixture(scope="module")
def index(invoice: GeneratedInvoice) -> DocumentIndex:
    return build_document_index("asset_01J9X3K4N7Q2M8R5T6V7W8X9Y0", pdf.extract_text(invoice.pdf))


def test_locate_exact_and_fuzzy(index: DocumentIndex, invoice: GeneratedInvoice) -> None:
    page = index.pages[0]
    m = locate(page, invoice.truth.invoice_number)
    assert m is not None
    assert m.score == 1.0
    assert page.text[m.start : m.end] == invoice.truth.invoice_number
    # whitespace and case drift still match exactly after normalisation
    m2 = locate(page, f"  {invoice.truth.vendor_name.upper()} ")
    assert m2 is not None and m2.score == 1.0
    # a typo is fuzzy-matched
    typo = invoice.truth.customer_name.replace("o", "0", 1)
    m3 = locate(page, typo)
    assert m3 is not None
    assert 0.85 <= m3.score < 1.0
    assert locate(page, "this text is nowhere on the page at all") is None
    assert locate(page, "") is None


def test_anchor_all_produces_boxes(index: DocumentIndex, invoice: GeneratedInvoice) -> None:
    anchors = [Anchor(pointer=p, source_text=t, page=1) for p, t in invoice.source_texts.items()]
    anchors.append(Anchor(pointer="/nope", source_text="zzz not present qqq", page=1))
    res = anchor_all([index], anchors)
    assert res.unanchored == ["/nope"]
    assert set(res.by_pointer) == set(invoice.source_texts)
    page = index.pages[0]
    for ev in res.evidence:
        assert ev.page == 1
        assert ev.bbox is not None
        assert 0 <= ev.bbox.x0 < ev.bbox.x1 <= page.width_pt
        assert 0 <= ev.bbox.y0 < ev.bbox.y1 <= page.height_pt
        assert ev.anchor_confidence == 1.0
    # the total is below the line items (larger y in top-left coordinates)
    total_ev = next(e for e in res.evidence if e.id == res.by_pointer["/total"])
    first_item_ev = next(
        e for e in res.evidence if e.id == res.by_pointer["/line_items/0/description"]
    )
    assert total_ev.bbox is not None and first_item_ev.bbox is not None
    assert total_ev.bbox.y0 > first_item_ev.bbox.y1


def test_short_values_anchor_to_their_own_row(
    index: DocumentIndex, invoice: GeneratedInvoice
) -> None:
    """Quantities like "50" appear many times; each must land on its line item's row."""
    anchors = [Anchor(pointer=p, source_text=t, page=1) for p, t in invoice.source_texts.items()]
    res = anchor_all([index], anchors)
    by_id = {e.id: e for e in res.evidence}
    for i in range(len(invoice.truth.line_items)):
        desc = by_id[res.by_pointer[f"/line_items/{i}/description"]]
        qty = by_id[res.by_pointer[f"/line_items/{i}/quantity"]]
        price = by_id[res.by_pointer[f"/line_items/{i}/unit_price"]]
        assert desc.bbox is not None
        assert qty.bbox is not None
        assert price.bbox is not None
        row = (desc.bbox.y0 + desc.bbox.y1) / 2
        assert abs((qty.bbox.y0 + qty.bbox.y1) / 2 - row) < 4, f"item {i} quantity off its row"
        assert abs((price.bbox.y0 + price.bbox.y1) / 2 - row) < 4, f"item {i} price off its row"
        # the quantity box is a whole token, not digits inside a SKU
        assert qty.text == invoice.source_texts[f"/line_items/{i}/quantity"]


def test_word_boundaries_preferred(index: DocumentIndex, invoice: GeneratedInvoice) -> None:
    page = index.pages[0]

    def bounded(m: Match) -> bool:
        before = page.text[m.start - 1] if m.start > 0 else " "
        after = page.text[m.end] if m.end < len(page.text) else " "
        return not before.isalnum() and not after.isalnum()

    # a quantity that is printed as a whole token: only bounded matches come back
    qty = invoice.source_texts["/line_items/1/quantity"]
    matches = locate_all(page, qty)
    assert matches
    assert all(bounded(m) for m in matches)
    # a string that only occurs inside other tokens falls back to loose matches
    assert "10" not in {t for t in invoice.source_texts.values()}
    loose = locate_all(page, "10")
    assert loose
    assert not any(bounded(m) for m in loose)
