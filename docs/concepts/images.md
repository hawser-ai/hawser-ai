# Images and scanned documents

Phase 7 makes scanned pages and photos first-class: they are OCR'd, straightened when needed,
and every value extracted from them carries box evidence that points at the **original**
pixels, however many crops, rotations or rescales happened in between (blueprint §5, §6, §15).

## What happens to an input

| Input | Index step | Evidence |
|---|---|---|
| PDF page with a text layer | native text + per-glyph boxes (as before) | PDF points on the page |
| PDF page without a text layer | rendered at `ocr_dpi` (300) → OCR → words with boxes, converted to PDF points | PDF points on the page, `source: ocr`, per-character confidence |
| Image (photo, scan) | `normalise` (EXIF upright, size cap) → `deskew` (projection-profile angle) → OCR on the straightened image | pixels of the **original** image; the chain `exif_orient → scale → rotate → pad` is kept on the evidence for provenance |

The model still sees the page image (for pages without a text layer and for photos) *and* the
OCR text, so text-only models work and anchoring has something to match. Anchoring tolerates
what OCR engines do: dropped currency marks (`€61.12` ↔ `61.12`) and collapsed spaces
(`Lithium grease 400 g` ↔ `Lithiumgrease400g`).

## OCR engines

| Engine | How | When |
|---|---|---|
| `rapidocr` (default) | PaddleOCR v4 models through ONNX Runtime; ships in the wheel (`hawser-ai[ocr]`), no system packages | local, ~0.4 s per page after a 1.5 s warm-up |
| `tesseract` | the `tesseract` binary when installed | local alternative; used by `reocr` as the second opinion |
| `azure-document-intelligence` | `prebuilt-read` over REST (`AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT` / `_KEY`) | hosted, `external_read`; the reference adapter for Google/Textract-style services |

`HAWSER_OCR_ENGINE=auto|rapidocr|tesseract|azure-document-intelligence|none`,
`HAWSER_OCR_DPI=300`, `HAWSER_DESKEW=true`. Tools: `ocr.recognize`, `ocr.engines`.

## Transforms

Every image-producing tool records the transforms that produced its output from its input
(`crop`, `scale`, `rotate` about a point, `exif_orient`, `pad`, `dpi`). The algebra in
`hawser.evidence.transforms` composes and inverts chains; a property test checks that applying
a random chain and inverting it returns the original box within half a pixel. When the result
is assembled, `resolve_to_source` maps every box found on a derived artifact back to the
original asset, so the web viewer draws it on the photo you uploaded.

## Repair: `reocr`

When evidence is required (`validation.escalate: {core.evidence_missing: error}`) and a value
on an OCR'd page could not be anchored, the first repair attempt re-OCRs at a higher DPI with a
different engine when one is available and re-anchors the values the model already quoted. It
makes no model call. Only if that fails does the loop fall back to re-extraction.

## Layout

`document.layout` groups lines into blocks (text, table, header, footer) in reading order,
detects tables where several rows share column anchors (left- or right-aligned), returns the
cell grid with boxes, and lists key-value pairs (`Label: value`, or label and value on one row).
It works on native text and on OCR output. A learned detector can replace the heuristics behind
the same output shape.

## Vision tools

All take an image reference: an image asset id, or an artifact id produced by another image
tool (so chains like rotate → crop → detect keep their provenance).

- `image.caption`, `image.vqa`, `image.classify`, `image.tag`: any VLM through the gateway,
  strict output schemas, recorded and replayable like every model call.
- `image.detect`, `image.ground`, `image.ui_parse`: boxes requested on a 0-1000 grid,
  validated (malformed boxes are dropped and counted), converted to pixels, and returned
  together with `image_region` evidence whose chain reaches the original asset.
- `image.crop|rotate|resize|deskew|normalise`: pixel operations with recorded transforms.
- `image.redact`, `image.annotate`: filled regions and labelled overlays as new artifacts.
- `image.segment`: masks (PNG artifact + RLE) from boxes; SAM-family adapters are optional
  extras not bundled here.
- `image.embed`, `image.similarity`: a deterministic perceptual embedding for near-duplicate
  checks; CLIP/SigLIP adapters are the upgrade path.
- `image.generate`, `image.edit`: an OpenAI-compatible images API (`HAWSER_IMAGE_API_BASE`,
  `HAWSER_IMAGE_API_KEY`), only when `policy.generation = enabled`; outputs are artifacts with
  producer metadata and **no** evidence claims.

## Results on the reference dataset

The scanned subset of `invoice-po/v1` (12 pairs rasterised at 150 dpi, no text layer) is now
judged like the native pairs; see `docs/benchmarks/invoice-po.md` for the numbers.
