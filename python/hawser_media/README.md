# hawser-media

Document and image building blocks for [Hawser](https://pypi.org/project/hawser-ai/):

- PDF text with a bounding box for every character, and page rendering (pypdfium2)
- image probing with limits against decompression bombs
- image operations (crop, rotate, deskew, resize) that record their coordinate transforms, so
  positions found on a derived image map back to the original
- OCR behind one interface: RapidOCR (the `ocr` extra), Tesseract, Azure Document Intelligence
- layout analysis: text blocks, tables and key-value pairs

```bash
pip install "hawser-media[ocr]"
```

You normally get this package through `hawser-ai`. On Linux, the OCR engine also needs
`sudo apt-get install libgl1`.

Source and documentation: https://github.com/hawser-ai/hawser-ai
