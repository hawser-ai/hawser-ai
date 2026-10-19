"""Heuristic layout analysis over positioned words (plan WP7.2 ``document.layout``).

Works on any ``PageText`` (native text layer or OCR): lines → blocks by vertical gaps and
horizontal overlap; header/footer by position; tables where several consecutive lines share
column boundaries; key-value pairs from ``Label: value`` lines and label/value pairs on one
row. Deterministic and dependency-free, so it is testable on synthetic pages. A learned
detector can replace ``segment_blocks`` behind the same output shape.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from statistics import median
from typing import Literal

from pydantic import BaseModel, Field

from hawser_media.pdf import Line, PageText, Word

BlockKind = Literal["text", "table", "header", "footer", "figure"]


class LayoutBox(BaseModel):
    x0: float
    y0: float
    x1: float
    y1: float


class TableCell(BaseModel):
    row: int
    col: int
    text: str
    box: LayoutBox


class Table(BaseModel):
    box: LayoutBox
    rows: int
    cols: int
    cells: list[TableCell] = Field(default_factory=list)
    #: Column x-boundaries in page coordinates.
    columns: list[float] = Field(default_factory=list)

    def grid(self) -> list[list[str]]:
        out = [["" for _ in range(self.cols)] for _ in range(self.rows)]
        for c in self.cells:
            out[c.row][c.col] = c.text
        return out


class KeyValue(BaseModel):
    key: str
    value: str
    key_box: LayoutBox
    value_box: LayoutBox
    #: ``colon`` (``Label: value`` in one line) or ``row`` (label then value on one row).
    kind: Literal["colon", "row"]


class Block(BaseModel):
    kind: BlockKind
    box: LayoutBox
    text: str
    #: Line offsets into the page text (start, end) for evidence anchoring.
    spans: list[tuple[int, int]] = Field(default_factory=list)
    reading_order: int = 0
    table: Table | None = None


class PageLayout(BaseModel):
    page: int
    width: float
    height: float
    blocks: list[Block] = Field(default_factory=list)
    key_values: list[KeyValue] = Field(default_factory=list)

    @property
    def tables(self) -> list[Table]:
        return [b.table for b in self.blocks if b.table is not None]


@dataclass
class _Cluster:
    lines: list[Line] = field(default_factory=list)

    @property
    def box(self) -> LayoutBox:
        return LayoutBox(
            x0=min(ln.x0 for ln in self.lines),
            y0=min(ln.y0 for ln in self.lines),
            x1=max(ln.x1 for ln in self.lines),
            y1=max(ln.y1 for ln in self.lines),
        )


def _line_height(lines: list[Line]) -> float:
    hs = [ln.y1 - ln.y0 for ln in lines if ln.y1 > ln.y0]
    return median(hs) if hs else 10.0


def _overlap_x(a: Line, b: LayoutBox) -> bool:
    return min(a.x1, b.x1) - max(a.x0, b.x0) > 0


def segment_blocks(lines: list[Line], *, gap_factor: float = 1.6) -> list[list[Line]]:
    """Group lines into blocks: a new block starts after a vertical gap larger than
    ``gap_factor`` line heights, or when horizontal extents stop overlapping."""
    if not lines:
        return []
    lh = _line_height(lines)
    ordered = sorted(lines, key=lambda ln: (round(ln.y0 / max(lh, 1)), ln.x0))
    clusters: list[_Cluster] = []
    for ln in ordered:
        placed = False
        for cl in clusters:
            box = cl.box
            gap = ln.y0 - box.y1
            same_row = (
                abs(((ln.y0 + ln.y1) / 2) - ((cl.lines[-1].y0 + cl.lines[-1].y1) / 2)) < lh / 2
            )
            if (gap <= gap_factor * lh and _overlap_x(ln, box)) or (
                same_row and (ln.x0 - box.x1) < 2 * lh
            ):
                cl.lines.append(ln)
                placed = True
                break
        if not placed:
            clusters.append(_Cluster(lines=[ln]))
    return [cl.lines for cl in clusters]


def _cluster(values: list[float], tol: float, min_size: int) -> list[float]:
    if not values:
        return []
    values = sorted(values)
    groups: list[list[float]] = [[values[0]]]
    for v in values[1:]:
        if v - groups[-1][-1] <= tol:
            groups[-1].append(v)
        else:
            groups.append([v])
    return [sum(g) / len(g) for g in groups if len(g) >= min_size]


def _column_boundaries(rows: list[Line], *, min_rows: int = 3) -> list[float] | None:
    """Column anchors shared by at least ``min_rows`` rows: left-aligned columns cluster on
    word starts, right-aligned ones (amounts) on word ends. Returns the merged anchors."""
    if len(rows) < min_rows:
        return None
    lh = _line_height(rows)
    starts = _cluster([w.x0 for ln in rows for w in ln.words], lh * 0.8, min_rows)
    ends = _cluster([w.x1 for ln in rows for w in ln.words], lh * 0.8, min_rows)
    anchors = sorted(starts + ends)
    merged: list[float] = []
    for a in anchors:
        if merged and a - merged[-1] <= lh * 1.5:
            merged[-1] = (merged[-1] + a) / 2
        else:
            merged.append(a)
    return merged if len(merged) >= 2 else None


def detect_table(rows: list[Line]) -> Table | None:
    """A table when ≥3 consecutive lines share ≥2 column boundaries; cells by column."""
    cols = _column_boundaries(rows)
    if cols is None:
        return None
    multi = [ln for ln in rows if len(ln.words) >= 2]
    if len(multi) < 3:
        return None
    cells: list[TableCell] = []
    for r, ln in enumerate(rows):
        buckets: dict[int, list[Word]] = {}
        for w in ln.words:
            centre = (w.x0 + w.x1) / 2
            idx = min(
                range(len(cols)),
                key=lambda k, w=w, c=centre: min(
                    abs(cols[k] - w.x0), abs(cols[k] - w.x1), abs(cols[k] - c)
                ),
            )
            buckets.setdefault(idx, []).append(w)
        for ci, words in buckets.items():
            cells.append(
                TableCell(
                    row=r,
                    col=ci,
                    text=" ".join(w.text for w in words),
                    box=LayoutBox(
                        x0=min(w.x0 for w in words),
                        y0=min(w.y0 for w in words),
                        x1=max(w.x1 for w in words),
                        y1=max(w.y1 for w in words),
                    ),
                )
            )
    box = LayoutBox(
        x0=min(ln.x0 for ln in rows),
        y0=min(ln.y0 for ln in rows),
        x1=max(ln.x1 for ln in rows),
        y1=max(ln.y1 for ln in rows),
    )
    return Table(box=box, rows=len(rows), cols=len(cols), cells=cells, columns=cols)


def key_values(lines: list[Line]) -> list[KeyValue]:
    out: list[KeyValue] = []
    for ln in lines:
        words = ln.words
        if not words:
            continue
        # "Label: value" within one line
        for i, wd in enumerate(words):
            if wd.text.endswith(":") and i + 1 < len(words) and i < 4:
                key_words, val_words = words[: i + 1], words[i + 1 :]
                out.append(
                    KeyValue(
                        key=" ".join(x.text for x in key_words).rstrip(":"),
                        value=" ".join(x.text for x in val_words),
                        key_box=LayoutBox(
                            x0=key_words[0].x0,
                            y0=min(x.y0 for x in key_words),
                            x1=key_words[-1].x1,
                            y1=max(x.y1 for x in key_words),
                        ),
                        value_box=LayoutBox(
                            x0=val_words[0].x0,
                            y0=min(x.y0 for x in val_words),
                            x1=val_words[-1].x1,
                            y1=max(x.y1 for x in val_words),
                        ),
                        kind="colon",
                    )
                )
                break
        else:
            # label words then a wide gap then the value (same row)
            lh = max(1.0, ln.y1 - ln.y0)
            for j in range(1, len(words)):
                if words[j].x0 - words[j - 1].x1 > 3 * lh and j <= 4:
                    key_words, val_words = words[:j], words[j:]
                    out.append(
                        KeyValue(
                            key=" ".join(x.text for x in key_words).rstrip(":"),
                            value=" ".join(x.text for x in val_words),
                            key_box=LayoutBox(
                                x0=key_words[0].x0,
                                y0=min(x.y0 for x in key_words),
                                x1=key_words[-1].x1,
                                y1=max(x.y1 for x in key_words),
                            ),
                            value_box=LayoutBox(
                                x0=val_words[0].x0,
                                y0=min(x.y0 for x in val_words),
                                x1=val_words[-1].x1,
                                y1=max(x.y1 for x in val_words),
                            ),
                            kind="row",
                        )
                    )
                    break
    return out


def analyse(
    page: PageText, *, header_fraction: float = 0.08, footer_fraction: float = 0.08
) -> PageLayout:
    lines = [ln for ln in page.lines if ln.words]
    blocks: list[Block] = []
    for group in segment_blocks(lines):
        box = LayoutBox(
            x0=min(ln.x0 for ln in group),
            y0=min(ln.y0 for ln in group),
            x1=max(ln.x1 for ln in group),
            y1=max(ln.y1 for ln in group),
        )
        kind: BlockKind = "text"
        table = detect_table(sorted(group, key=lambda ln: ln.y0))
        if table is not None:
            kind = "table"
        elif box.y1 <= page.height_pt * header_fraction:
            kind = "header"
        elif box.y0 >= page.height_pt * (1 - footer_fraction):
            kind = "footer"
        blocks.append(
            Block(
                kind=kind,
                box=box,
                text="\n".join(ln.text for ln in sorted(group, key=lambda ln: (ln.y0, ln.x0))),
                spans=[(ln.start, ln.end) for ln in group],
                table=table,
            )
        )
    # Reading order: top-to-bottom; blocks on the same band left-to-right.
    lh = _line_height(lines)
    blocks.sort(key=lambda b: (round(b.box.y0 / max(lh * 2, 1)), b.box.x0))
    for i, b in enumerate(blocks):
        b.reading_order = i
    return PageLayout(
        page=page.page,
        width=page.width_pt,
        height=page.height_pt,
        blocks=blocks,
        key_values=key_values(lines),
    )


__all__ = [
    "Block",
    "BlockKind",
    "KeyValue",
    "LayoutBox",
    "PageLayout",
    "Table",
    "TableCell",
    "analyse",
    "detect_table",
    "key_values",
    "segment_blocks",
]
