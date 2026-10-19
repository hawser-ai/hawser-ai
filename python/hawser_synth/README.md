# hawser-synth

Synthetic documents for [Hawser](https://pypi.org/project/hawser-ai/): invoices and purchase
orders rendered to PDF (with a text layer, or rasterised to look like scans), each with its exact
ground truth. It also generates the `invoice-po` evaluation dataset.

```bash
pip install hawser-synth
python -m hawser_synth.dataset datasets/invoice-po/v1   # 60 pairs, byte-for-byte reproducible
```

Used by Hawser's tests, the quickstart example and the reference benchmark.

Source: https://github.com/hawser-ai/hawser-ai
