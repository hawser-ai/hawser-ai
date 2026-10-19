# hawser-synth

Synthetic documents for Hawser's tests, the quickstart example and the reference benchmark:
invoices and purchase orders rendered to PDF (with a text layer, or rasterised to look like
scans), each with its exact ground truth. It also generates the `invoice-po` evaluation dataset.

This package lives in the repository only and is not published to PyPI.

```bash
uv run python -m hawser_synth.dataset datasets/invoice-po/v1   # 60 pairs, byte-for-byte reproducible
```
