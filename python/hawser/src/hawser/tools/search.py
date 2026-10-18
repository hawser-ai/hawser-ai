"""Local text retrieval over document indexes (BM25). Used by workflows now and by the
agent-mode context manager in Phase 4."""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any

from hawser.evidence.anchor import normalise
from hawser.evidence.index import DocumentIndex
from hawser.runtime.context import RunContext
from hawser.tools.registry import tool

_TOKEN = re.compile(r"[a-z0-9]+(?:[.\-][a-z0-9]+)*")


def tokenize(text: str) -> list[str]:
    return _TOKEN.findall(normalise(text)[0])


class BM25:
    def __init__(self, docs: list[list[str]], *, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1, self.b = k1, b
        self.docs = docs
        self.n = len(docs)
        self.avg = (sum(len(d) for d in docs) / self.n) if self.n else 0.0
        self.tf = [Counter(d) for d in docs]
        df: Counter[str] = Counter()
        for d in docs:
            df.update(set(d))
        self.idf = {t: math.log(1 + (self.n - n + 0.5) / (n + 0.5)) for t, n in df.items()}

    def score(self, query: list[str], i: int) -> float:
        tf, dl = self.tf[i], len(self.docs[i])
        s = 0.0
        for t in query:
            if t not in tf:
                continue
            f = tf[t]
            s += (
                self.idf.get(t, 0.0)
                * (f * (self.k1 + 1))
                / (f + self.k1 * (1 - self.b + self.b * dl / (self.avg or 1)))
            )
        return s

    def top(self, query: list[str], k: int) -> list[tuple[int, float]]:
        scored = [(i, self.score(query, i)) for i in range(self.n)]
        return sorted([x for x in scored if x[1] > 0], key=lambda x: (-x[1], x[0]))[:k]


def search_indexes(
    indexes: list[DocumentIndex], query: str, *, top_k: int = 5
) -> list[dict[str, Any]]:
    """Rank pages across indexes; each hit carries asset, page, score and a snippet."""
    pages = [(idx, p) for idx in indexes for p in idx.pages]
    bm = BM25([tokenize(p.text) for _, p in pages])
    q = tokenize(query)
    hits: list[dict[str, Any]] = []
    for i, score in bm.top(q, top_k):
        idx, page = pages[i]
        hits.append(
            {
                "asset_id": idx.asset_id,
                "name": idx.name,
                "page": page.page,
                "score": round(score, 4),
                "snippet": _snippet(page.text, q),
            }
        )
    return hits


def _snippet(text: str, query: list[str], width: int = 160) -> str:
    low = text.lower()
    pos = min((low.find(t) for t in query if low.find(t) >= 0), default=0)
    start = max(0, pos - width // 3)
    return " ".join(text[start : start + width].split())


@tool(
    "search.text_index",
    description="BM25 search over the run's document indexes; best pages with snippets.",
    modalities=["document"],
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "asset_ids": {"type": "array", "items": {"type": "string"}},
            "top_k": {"type": "integer", "minimum": 1},
        },
        "required": ["query"],
    },
)
def text_index(
    ctx: RunContext, query: str, asset_ids: list[str] | None = None, top_k: int = 5
) -> dict[str, Any]:
    indexes: list[DocumentIndex] = []
    for ref in ctx.artifact_refs:
        art = ctx.meta.get_artifact(ref.artifact_id)
        if art is None or art.meta.kind.value != "index":
            continue
        idx = DocumentIndex.model_validate(ctx.artifacts.get_json(art.id))
        if asset_ids and idx.asset_id not in asset_ids:
            continue
        indexes.append(idx)
    return {"hits": search_indexes(indexes, query, top_k=top_k)}


__all__ = ["BM25", "search_indexes", "text_index", "tokenize"]
