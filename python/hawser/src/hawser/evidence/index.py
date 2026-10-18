"""The document text index: per-page text with per-character boxes. Stored as an artifact."""

from __future__ import annotations

from pydantic import BaseModel, Field

from hawser_media.pdf import NEEDS_OCR_CHAR_THRESHOLD, PageText

Box = tuple[float, float, float, float]


class PageIndex(BaseModel):
    page: int
    width_pt: float
    height_pt: float
    text: str
    #: Parallel to ``text``: ``[x0, y0, x1, y1]`` in top-left-origin points, or ``None``.
    boxes: list[Box | None] = Field(default_factory=list)
    needs_ocr: bool = False
    #: Artifact id of a rendered page image, if one was produced.
    render_artifact_id: str | None = None
    render_dpi: float | None = None

    def bbox_for_range(self, start: int, end: int) -> Box | None:
        xs0: list[float] = []
        ys0: list[float] = []
        xs1: list[float] = []
        ys1: list[float] = []
        for i in range(max(start, 0), min(end, len(self.boxes))):
            b = self.boxes[i]
            if b is None or self.text[i].isspace():
                continue
            xs0.append(b[0])
            ys0.append(b[1])
            xs1.append(b[2])
            ys1.append(b[3])
        if not xs0:
            return None
        return (min(xs0), min(ys0), max(xs1), max(ys1))

    @property
    def visible_chars(self) -> int:
        return sum(
            1 for i, b in enumerate(self.boxes) if b is not None and not self.text[i].isspace()
        )


class DocumentIndex(BaseModel):
    asset_id: str
    name: str | None = None
    pages: list[PageIndex] = Field(default_factory=list)

    def page(self, number: int) -> PageIndex | None:
        for p in self.pages:
            if p.page == number:
                return p
        return None


def build_document_index(
    asset_id: str, pages: list[PageText], *, name: str | None = None
) -> DocumentIndex:
    out: list[PageIndex] = []
    for pt in pages:
        boxes: list[Box | None] = []
        for c in pt.chars:
            if c.has_box:
                boxes.append((round(c.x0, 2), round(c.y0, 2), round(c.x1, 2), round(c.y1, 2)))  # type: ignore[arg-type]
            else:
                boxes.append(None)
        pi = PageIndex(
            page=pt.page, width_pt=pt.width_pt, height_pt=pt.height_pt, text=pt.text, boxes=boxes
        )
        pi.needs_ocr = pi.visible_chars < NEEDS_OCR_CHAR_THRESHOLD
        out.append(pi)
    return DocumentIndex(asset_id=asset_id, name=name, pages=out)


__all__ = ["Box", "DocumentIndex", "PageIndex", "build_document_index"]
