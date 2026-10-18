# Evaluations

Evaluation is a developer feature (blueprint §21): versioned datasets, field-level scoring,
cost/latency/review-rate metrics, failure clusters, comparison and CI gates.

## Dataset layout

```
datasets/invoice-po/v1/
  dataset.yaml                     id, version, skill, scoring config, cases[]
  cases/<id>/inputs/*.pdf          named after the skill's inputs
  cases/<id>/expected.json         conforms to the skill's output schema
  cassettes/<model>/*.json         recorded model responses (kept in git; replay is free)
```

`dataset.yaml` scoring config:

```yaml
scoring:
  list_keys: { /invoice/line_items: sku }     # align list items by a key instead of index
  ignore: [/reconciliation/matched_items/*/invoice_index]
  numeric_tolerance: 0.011
```

Each case may set `expected_status` (`accepted` / `needs_review` / …) and `tags`.

The reference dataset is generated: `python -m hawser_synth.dataset datasets/invoice-po/v1`
(60 synthetic invoice/PO pairs, 4 layouts, 3 currencies, injected discrepancies, 20% rasterised
"scans"). Expected outputs are computed from the ground truth with the same deterministic
alignment/reconciliation code the skill uses, so they are exact by construction. A rebuild never
touches `cassettes/`.

## Running

```bash
hawser eval run --dataset datasets/invoice-po/v1 --tag smoke                # replay, free
hawser eval run --dataset datasets/invoice-po/v1 --live --model openrouter/anthropic/claude-haiku-5.5 --budget 2
hawser eval gate .hawser/evals/eval_…/ --thresholds evals/thresholds.yaml   # CI exit code
hawser compare <baseline-report> <candidate-report>                          # deltas + regressions
```

Reports are written as `report.json` (machine) and `report.md` (human) with a configuration
snapshot: skill version and bundle hash, model, registry version, record mode, policy, hawser
version and git SHA.

## Scoring

Expected and actual documents are walked together; lists align by `list_keys` or index. Each
leaf is `correct`, `mismatch`, `missed` (expected a value, got unknown), `hallucinated`
(expected unknown, got a value) or `extra` (unexpected list item). Numbers compare within
tolerance; strings after normalisation.

Metrics: `field_accuracy`, `doc_pass_rate`, `status_accuracy`, `review_rate`, `failed_rate`,
`unknown_precision/recall`, `evidence_coverage`, `cost_*`, `latency_p50/p95`,
`model_calls_mean`, `repair_attempts_mean`. Failure clusters group wrong leaves by field
template (`/invoice/line_items/*/quantity`) and outcome.

See [`../benchmarks/invoice-po.md`](../benchmarks/invoice-po.md) for published results.
