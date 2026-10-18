# ADR-0007: PDF engine is pypdfium2, not PyMuPDF

- **Status:** accepted
- **Date:** 2026-10-18
- **Phase:** 0 (applies from Phase 1)

## Context

Document primitives need text extraction with character-level boxes, page rendering and
probing. PyMuPDF (fitz) is excellent but AGPL-3.0 (commercial licence otherwise). Hawser's
open-source edition is Apache-2.0 and is embedded in users' products and in Hawser Cloud.

## Decision

Use **pypdfium2** (Apache-2.0 / BSD-3, wraps Chromium's PDFium) for:

- probing (page count, sizes, encryption, text-layer presence),
- native text extraction with character boxes (`get_charbox`),
- page rendering to raster.

Normalise coordinates to top-left origin PDF points (`CoordinateSystem.pdf_points_tl`) at the
boundary. Layout analysis, OCR and table extraction (Phase 7) are separate tools layered on top.

CI runs a licence check that fails on GPL/AGPL/LGPL/SSPL dependencies anywhere in the
workspace.

## Consequences

- Reading-order reconstruction and multi-column handling are ours to implement (Phase 7);
  pypdfium2 gives characters and positions, not layout.
- No AGPL exposure for self-hosters or for the managed service.

## Alternatives considered

- PyMuPDF: AGPL. Rejected for the core; an optional adapter could exist for users who hold a
  commercial licence, but nothing in Hawser may depend on it.
- pdfplumber/pdfminer.six: MIT, pure Python; slower, no rendering. May be used for
  cross-checking text extraction in evals.
