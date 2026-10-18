"""PDF primitives on pypdfium2 (ADR-0007): probing, text with character boxes, page rendering.

Coordinates returned here are PDF points with the origin at the **top-left** of the page
(pdfium's native origin is bottom-left; it is flipped at this boundary and never leaks out).
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field

import pypdfium2 as pdfium
from pydantic import BaseModel, Field

from hawser_media.errors import MediaError

#: Pages with fewer visible characters than this are treated as "needs OCR" (scanned).
NEEDS_OCR_CHAR_THRESHOLD = 20


class PdfInfo(BaseModel):
    page_count: int
    encrypted: bool = False
    has_forms: bool = False
    chars_per_page: list[int] = []
    page_sizes_pt: list[tuple[float, float]] = []

    def needs_ocr(self, page: int) -> bool:
        """``page`` is 1-based."""
        return self.chars_per_page[page - 1] < NEEDS_OCR_CHAR_THRESHOLD


class CharBox(BaseModel):
    """One character and its box in top-left-origin points. No box = no glyph (e.g. newline)."""

    char: str
    x0: float | None = None
    y0: float | None = None
    x1: float | None = None
    y1: float | None = None

    @property
    def has_box(self) -> bool:
        return self.x0 is not None


class Word(BaseModel):
    text: str
    start: int  # char offset in PageText.text
    end: int  # exclusive
    x0: float
    y0: float
    x1: float
    y1: float


class Line(BaseModel):
    text: str
    start: int
    end: int
    x0: float
    y0: float
    x1: float
    y1: float
    words: list[Word] = Field(default_factory=list)


class PageText(BaseModel):
    """Text of one page. ``text[i]`` corresponds to ``chars[i]``: the invariant anchoring needs."""

    page: int  # 1-based
    width_pt: float
    height_pt: float
    text: str
    chars: list[CharBox]
    lines: list[Line] = Field(default_factory=list)

    @property
    def visible_char_count(self) -> int:
        return sum(1 for c in self.chars if c.has_box and not c.char.isspace())

    def bbox_for_range(self, start: int, end: int) -> tuple[float, float, float, float] | None:
        """Union box of all glyphs in ``text[start:end]``; ``None`` if no glyph has a box."""
        xs0: list[float] = []
        ys0: list[float] = []
        xs1: list[float] = []
        ys1: list[float] = []
        for c in self.chars[start:end]:
            if c.has_box and not c.char.isspace():
                xs0.append(c.x0)  # type: ignore[arg-type]
                ys0.append(c.y0)  # type: ignore[arg-type]
                xs1.append(c.x1)  # type: ignore[arg-type]
                ys1.append(c.y1)  # type: ignore[arg-type]
        if not xs0:
            return None
        return (min(xs0), min(ys0), max(xs1), max(ys1))


@dataclass
class RenderedPage:
    page: int
    png: bytes
    width_px: int
    height_px: int
    dpi: float
    #: points -> pixels scale factor (same on both axes)
    scale: float = field(default=1.0)


def _open(data: bytes) -> pdfium.PdfDocument:
    try:
        return pdfium.PdfDocument(data)
    except pdfium.PdfiumError as exc:  # pragma: no cover - message text depends on pdfium
        msg = str(exc).lower()
        code = "pdf_encrypted" if "password" in msg else "pdf_corrupt"
        raise MediaError(code, f"cannot open PDF: {exc}") from exc
    except Exception as exc:
        raise MediaError("pdf_corrupt", f"cannot open PDF: {exc}") from exc


def probe(data: bytes) -> PdfInfo:
    """Cheap inspection: page count, sizes, text-layer character counts, forms, encryption."""
    doc = _open(data)
    try:
        chars: list[int] = []
        sizes: list[tuple[float, float]] = []
        for i in range(len(doc)):
            page = doc[i]
            sizes.append(tuple(float(v) for v in page.get_size()))  # type: ignore[arg-type]
            tp = page.get_textpage()
            text = tp.get_text_range()
            chars.append(sum(1 for ch in text if not ch.isspace()))
            tp.close()
            page.close()
        has_forms = False
        try:
            has_forms = doc.get_formtype() != 0
        except Exception:
            has_forms = False
        return PdfInfo(
            page_count=len(doc), chars_per_page=chars, page_sizes_pt=sizes, has_forms=has_forms
        )
    finally:
        doc.close()


def extract_text(data: bytes, *, pages: list[int] | None = None) -> list[PageText]:
    """Extract text with per-character boxes for the given 1-based pages (default: all)."""
    doc = _open(data)
    out: list[PageText] = []
    try:
        wanted = pages or list(range(1, len(doc) + 1))
        for page_no in wanted:
            if page_no < 1 or page_no > len(doc):
                raise MediaError("pdf_page_out_of_range", f"page {page_no} of {len(doc)}")
            page = doc[page_no - 1]
            width, height = (float(v) for v in page.get_size())
            tp = page.get_textpage()
            try:
                n = tp.count_chars()
                text = tp.get_text_range(0, n) if n else ""
                chars: list[CharBox] = []
                # pdfium reports one box per index of the text range; indexes line up with
                # the string it returns, which is the invariant PageText relies on.
                for i, ch in enumerate(text):
                    box = None
                    if not ch.isspace():
                        try:
                            box = tp.get_charbox(i, loose=False)
                        except pdfium.PdfiumError:
                            box = None
                    if box is None:
                        chars.append(CharBox(char=ch))
                        continue
                    left, bottom, right, top = (float(v) for v in box)
                    if right <= left or top <= bottom:
                        chars.append(CharBox(char=ch))
                        continue
                    chars.append(
                        CharBox(char=ch, x0=left, y0=height - top, x1=right, y1=height - bottom)
                    )
            finally:
                tp.close()
                page.close()
            pt = PageText(page=page_no, width_pt=width, height_pt=height, text=text, chars=chars)
            pt.lines = _group_lines(pt)
            out.append(pt)
    finally:
        doc.close()
    return out


def _group_lines(pt: PageText) -> list[Line]:
    """Words = maximal runs of non-space glyphs; lines = runs of words on the same baseline."""
    words: list[Word] = []
    i, n = 0, len(pt.text)
    while i < n:
        if pt.text[i].isspace() or not pt.chars[i].has_box:
            i += 1
            continue
        j = i
        while j < n and not pt.text[j].isspace() and pt.chars[j].has_box:
            j += 1
        box = pt.bbox_for_range(i, j)
        if box is not None:
            words.append(
                Word(text=pt.text[i:j], start=i, end=j, x0=box[0], y0=box[1], x1=box[2], y1=box[3])
            )
        i = j

    lines: list[Line] = []
    current: list[Word] = []

    def flush() -> None:
        if not current:
            return
        start, end = current[0].start, current[-1].end
        lines.append(
            Line(
                text=pt.text[start:end],
                start=start,
                end=end,
                x0=min(w.x0 for w in current),
                y0=min(w.y0 for w in current),
                x1=max(w.x1 for w in current),
                y1=max(w.y1 for w in current),
                words=list(current),
            )
        )
        current.clear()

    for w in words:
        if current:
            prev = current[-1]
            prev_mid = (prev.y0 + prev.y1) / 2
            height = max(prev.y1 - prev.y0, 1.0)
            same_line = abs(((w.y0 + w.y1) / 2) - prev_mid) < 0.5 * height and w.x0 >= prev.x0
            if not same_line:
                flush()
        current.append(w)
    flush()
    return lines


def render_page(data: bytes, page_no: int, *, dpi: float = 150.0) -> RenderedPage:
    """Render one 1-based page to PNG."""
    doc = _open(data)
    try:
        if page_no < 1 or page_no > len(doc):
            raise MediaError("pdf_page_out_of_range", f"page {page_no} of {len(doc)}")
        page = doc[page_no - 1]
        try:
            scale = dpi / 72.0
            bitmap = page.render(scale=scale)  # type: ignore[arg-type]  # stub says int
            image = bitmap.to_pil()
        finally:
            page.close()
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        return RenderedPage(
            page=page_no,
            png=buf.getvalue(),
            width_px=image.width,
            height_px=image.height,
            dpi=dpi,
            scale=scale,
        )
    finally:
        doc.close()


__all__ = [
    "NEEDS_OCR_CHAR_THRESHOLD",
    "CharBox",
    "Line",
    "PageText",
    "PdfInfo",
    "RenderedPage",
    "Word",
    "extract_text",
    "probe",
    "render_page",
]
