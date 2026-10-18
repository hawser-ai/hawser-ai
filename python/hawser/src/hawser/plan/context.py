"""Context manager v2 (blueprint §13): choose the pages a model call sees.

``all`` keeps pages in order and drops the tail when over budget; ``matching`` ranks pages by
BM25 against the node's query; an explicit list selects pages. Prior node outputs are included
as compact, deterministic JSON summaries. The selection is recorded as an event by the caller.
"""

from __future__ import annotations

import json
from typing import Any

from hawser.evidence.index import DocumentIndex
from hawser.plan.ir import ContextSpec
from hawser.runtime.context import DocumentContext, estimate_text_tokens
from hawser.tools.search import BM25, tokenize

SUMMARY_TOKENS = 200


def summarize_output(node_id: str, value: Any, *, max_tokens: int = SUMMARY_TOKENS) -> str:
    """A compact JSON rendering of a prior output, truncated deterministically."""
    text = json.dumps(value, separators=(",", ":"), default=str, ensure_ascii=False)
    limit = max_tokens * 4
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return f'<node id="{node_id}">{text}</node>'


def select_documents(
    docs: list[DocumentContext], spec: ContextSpec, *, budget_tokens: int
) -> tuple[list[DocumentContext], dict[str, Any]]:
    """Return narrowed document contexts (page subsets) and a report of what was chosen."""
    report: dict[str, Any] = {"pages": spec.pages, "query": spec.query, "kept": [], "dropped": []}
    if spec.pages == "all":
        kept: list[DocumentContext] = []
        used = 0
        for d in docs:
            if d.index is None:
                kept.append(d)
                continue
            pages = []
            for p in d.index.pages:
                tokens = 1_600 if p.needs_ocr else estimate_text_tokens(p.text)
                if used + tokens > budget_tokens:
                    report["dropped"].append({"asset": d.asset.id, "page": p.page})
                    continue
                used += tokens
                pages.append(p)
                report["kept"].append({"asset": d.asset.id, "page": p.page})
            kept.append(
                DocumentContext(
                    asset=d.asset,
                    index=DocumentIndex(asset_id=d.index.asset_id, name=d.index.name, pages=pages),
                )
            )
        return kept, report

    if isinstance(spec.pages, list):
        wanted = set(spec.pages)
        kept = []
        for d in docs:
            if d.index is None:
                kept.append(d)
                continue
            pages = [p for p in d.index.pages if p.page in wanted]
            report["kept"] += [{"asset": d.asset.id, "page": p.page} for p in pages]
            kept.append(
                DocumentContext(
                    asset=d.asset,
                    index=DocumentIndex(asset_id=d.index.asset_id, name=d.index.name, pages=pages),
                )
            )
        return kept, report

    # matching: rank every page across documents by BM25 and keep the best within budget
    entries = [(d, p) for d in docs if d.index is not None for p in d.index.pages]
    bm = BM25([tokenize(p.text) for _, p in entries])
    ranked = bm.top(tokenize(spec.query or ""), len(entries)) or [
        (i, 0.0) for i in range(len(entries))
    ]
    chosen: dict[str, list[Any]] = {}
    used = 0
    for i, score in ranked:
        d, p = entries[i]
        tokens = 1_600 if p.needs_ocr else estimate_text_tokens(p.text)
        if used + tokens > budget_tokens:
            report["dropped"].append(
                {"asset": d.asset.id, "page": p.page, "score": round(score, 3)}
            )
            continue
        used += tokens
        chosen.setdefault(d.asset.id, []).append(p)
        report["kept"].append({"asset": d.asset.id, "page": p.page, "score": round(score, 3)})
    kept = []
    for d in docs:
        if d.index is None:
            kept.append(d)
            continue
        pages = sorted(chosen.get(d.asset.id, []), key=lambda p: p.page)
        kept.append(
            DocumentContext(
                asset=d.asset,
                index=DocumentIndex(asset_id=d.index.asset_id, name=d.index.name, pages=pages),
            )
        )
    return kept, report


__all__ = ["SUMMARY_TOKENS", "select_documents", "summarize_output"]
