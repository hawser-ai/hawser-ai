"""Carry evidence found on derived artifacts back to the original asset (plan WP7.1)."""

from __future__ import annotations

from collections.abc import Sequence

from hawser.evidence.transforms import to_source_bbox, to_source_point
from hawser.models import DocumentRegionEvidence, Evidence, ImageRegionEvidence


def resolve_to_source(evidence: Sequence[Evidence]) -> list[Evidence]:
    """Return copies whose boxes/points are in the original asset's coordinates.

    Evidence with an empty ``transform_chain`` is already in source coordinates and is
    returned unchanged. The chain is kept on the copy for provenance.
    """
    out: list[Evidence] = []
    for e in evidence:
        if not e.transform_chain or not isinstance(e, DocumentRegionEvidence | ImageRegionEvidence):
            out.append(e)
            continue
        update: dict[str, object] = {}
        if e.bbox is not None:
            update["bbox"] = to_source_bbox(e.transform_chain, e.bbox)
        if isinstance(e, ImageRegionEvidence) and e.point is not None:
            update["point"] = to_source_point(e.transform_chain, e.point)
        out.append(e.model_copy(update=update) if update else e)
    return out


__all__ = ["resolve_to_source"]
