"""Anchoring: locate the model's quoted ``source_text`` in page text and map it to boxes.

Model-supplied coordinates are never trusted (ADR-0005). The model quotes; the runtime locates.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

from pydantic import BaseModel, Field
from rapidfuzz import fuzz

from hawser.evidence.index import DocumentIndex, PageIndex
from hawser.ids import new_id
from hawser.models import BBox, DocumentRegionEvidence, ProducerRef
from hawser.structured.unwrap import Anchor

FUZZY_THRESHOLD = 88.0
ANCHOR_VERSION = "1"

_REPLACEMENTS = str.maketrans(
    {
        "‘": "'",
        "’": "'",
        "“": '"',
        "”": '"',
        "–": "-",
        "—": "-",
        "−": "-",
        " ": " ",
    }
)


def normalise(text: str) -> tuple[str, list[int]]:
    """Return ``(normalised, index_map)``; ``index_map[i]`` is the original offset of char i."""
    out: list[str] = []
    mapping: list[int] = []
    pending_space = False
    for i, raw in enumerate(text):
        ch = unicodedata.normalize("NFKC", raw).translate(_REPLACEMENTS)
        if ch.isspace() or ch == "":
            pending_space = True
            continue
        if pending_space and out:
            out.append(" ")
            mapping.append(i)
        pending_space = False
        for sub in ch.casefold():
            out.append(sub)
            mapping.append(i)
    return "".join(out), mapping


@dataclass
class Match:
    start: int  # original offsets in page.text
    end: int
    score: float  # 0..1


def _bounded(pn: str, start: int, end: int) -> bool:
    before = pn[start - 1] if start > 0 else " "
    after = pn[end] if end < len(pn) else " "
    return not before.isalnum() and not after.isalnum()


def locate_all(page: PageIndex, query: str) -> list[Match]:  # noqa: PLR0911
    """All exact (normalised) matches, word-bounded ones first; else the best fuzzy match."""
    qn, _ = normalise(query)
    if not qn:
        return []
    pn, pmap = normalise(page.text)
    if not pn:
        return []
    exact: list[Match] = []
    loose: list[Match] = []
    idx = pn.find(qn)
    while idx >= 0:
        m = Match(start=pmap[idx], end=pmap[idx + len(qn) - 1] + 1, score=1.0)
        (exact if _bounded(pn, idx, idx + len(qn)) else loose).append(m)
        idx = pn.find(qn, idx + 1)
    if exact:
        return exact
    if loose:
        return loose
    if len(qn) < 3:
        return []
    aligned = fuzz.partial_ratio_alignment(qn, pn)
    if aligned is None or aligned.score < FUZZY_THRESHOLD:
        return []
    s, e = aligned.dest_start, aligned.dest_end
    if e <= s:
        return []
    return [Match(start=pmap[s], end=pmap[e - 1] + 1, score=aligned.score / 100.0)]


def locate(page: PageIndex, query: str) -> Match | None:
    matches = locate_all(page, query)
    return matches[0] if matches else None


def _parent(pointer: str) -> str:
    return pointer.rsplit("/", 1)[0] if "/" in pointer else ""


def _row_centre(page: PageIndex, m: Match) -> float | None:
    box = page.bbox_for_range(m.start, m.end)
    return None if box is None else (box[1] + box[3]) / 2


class AnchorResult(BaseModel):
    evidence: list[DocumentRegionEvidence] = Field(default_factory=list)
    #: pointer -> evidence id
    by_pointer: dict[str, str] = Field(default_factory=dict)
    #: pointer -> anchor confidence
    confidence: dict[str, float] = Field(default_factory=dict)
    unanchored: list[str] = Field(default_factory=list)


def anchor_all(
    indexes: list[DocumentIndex], anchors: list[Anchor], *, step_id: str | None = None
) -> AnchorResult:
    result = AnchorResult()
    producer = ProducerRef(kind="tool", name="evidence.anchor", version=ANCHOR_VERSION)
    #: parent pointer -> (page number, mean row centre) of siblings anchored so far
    rows: dict[str, list[tuple[int, float]]] = {}
    # Long, distinctive values first so short ones (quantities, codes) can lean on their row.
    ordered = sorted(anchors, key=lambda a: (_parent(a.pointer), -len(a.source_text)))
    for a in ordered:
        found: list[tuple[DocumentIndex, PageIndex, Match]] = []
        for doc in indexes:
            candidates = [p for p in doc.pages if a.page is None or p.page == a.page] or doc.pages
            for page in candidates:
                found.extend((doc, page, m) for m in locate_all(page, a.source_text))
        if not found:
            result.unanchored.append(a.pointer)
            continue
        top = max(m.score for _, _, m in found)
        found = [f for f in found if f[2].score >= top]
        siblings = rows.get(_parent(a.pointer), [])
        if len(found) > 1 and siblings:
            same_page = [f for f in found if any(f[1].page == p for p, _ in siblings)]
            pool = same_page or found
            target = sum(c for _, c in siblings) / len(siblings)

            def distance(f: tuple[DocumentIndex, PageIndex, Match], t: float = target) -> float:
                c = _row_centre(f[1], f[2])
                return abs(c - t) if c is not None else float("inf")

            doc, page, m = min(pool, key=distance)
        else:
            doc, page, m = found[0]
        centre = _row_centre(page, m)
        if centre is not None:
            rows.setdefault(_parent(a.pointer), []).append((page.page, centre))
        box = page.bbox_for_range(m.start, m.end)
        ev = DocumentRegionEvidence(
            id=new_id("ev"),
            asset_id=doc.asset_id,
            step_id=step_id,
            producer=producer,
            page=page.page,
            bbox=BBox(x0=box[0], y0=box[1], x1=box[2], y1=box[3]) if box else None,
            text=page.text[m.start : m.end],
            anchor_confidence=round(m.score, 4),
            confidence=round(m.score, 4),
        )
        result.evidence.append(ev)
        result.by_pointer[a.pointer] = ev.id
        result.confidence[a.pointer] = ev.anchor_confidence or 0.0
    return result


__all__ = [
    "ANCHOR_VERSION",
    "FUZZY_THRESHOLD",
    "AnchorResult",
    "Match",
    "anchor_all",
    "locate",
    "locate_all",
    "normalise",
]
