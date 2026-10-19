# hawser-media

Media primitives for Hawser: PDF probing, text with per-glyph boxes and page rendering
(pypdfium2), image probing with decompression-bomb limits, image operations that record their
coordinate transforms, OCR engines behind one contract (RapidOCR with the `ocr` extra,
Tesseract, Azure Document Intelligence), and heuristic layout analysis.

```bash
pip install "hawser-media[ocr]"
```
