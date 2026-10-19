# hawser-synth

Synthetic documents for Hawser: invoices and purchase orders rendered to PDF (native or
rasterised "scans") with exact ground truth, and the generator for the `invoice-po`
evaluation dataset.

```bash
python -m hawser_synth.dataset datasets/invoice-po/v1   # 60 pairs, deterministic
```

Used by Hawser's tests, the quickstart bundle and the reference benchmark.
