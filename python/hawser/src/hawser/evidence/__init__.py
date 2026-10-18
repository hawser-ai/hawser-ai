"""Evidence (blueprint §15): document indexes and anchoring of quoted text to page regions."""

from hawser.evidence.anchor import AnchorResult, anchor_all, locate, normalise
from hawser.evidence.index import DocumentIndex, PageIndex, build_document_index

__all__ = [
    "AnchorResult",
    "DocumentIndex",
    "PageIndex",
    "anchor_all",
    "build_document_index",
    "locate",
    "normalise",
]
