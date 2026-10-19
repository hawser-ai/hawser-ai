# Benchmark: invoice ↔ purchase-order reconciliation

Skill `invoice-po-reconciliation@1.0.0`, dataset `invoice-po/v1` (60 synthetic pairs: 2 invoice
layouts × 2 PO layouts, GBP/EUR/USD, 60% with injected discrepancies — quantity, price,
missing/extra items, rounding, currency — and 12 rasterised "scans" without a text layer).
Expected outputs are exact by construction. Recorded on 2026-10-18 through OpenRouter; the
pairs that name the customer "Larkspur Software Ltd" were re-recorded on 2026-10-19 after a
sample name changed. Every response is in `datasets/invoice-po/v1/cassettes/`, so these runs
replay for free. Costs and latencies are from the live recordings.

| | Claude Haiku 5.5 (60 pairs) | Claude Opus 5.5 (8-pair smoke) |
|---|---|---|
| Field accuracy | **100.0%** (7,295 fields)¹ | **100.0%** (1,017 fields) |
| Document pass rate | 100.0% | 100.0% |
| Status accuracy (accepted vs needs_review) | 100.0% | 100.0% |
| Review rate | 48.3% (= cases with discrepancies; scans no longer need review) | 50.0% |
| Unknown precision / recall | 1.00 / 1.00 | 1.00 / 1.00 |
| Evidence coverage — native PDFs | 97.2% | 97%+ |
| Evidence coverage — scanned pages | **97.2%** with OCR (Phase 7; was 7.8% before) | — |
| Model calls per pair | 2 | 2 |
| Repair attempts per pair | 0 | 0 |
| Cost per pair (mean / p95) | $0.0023 / $0.0028 | $0.0816 / $0.093 |
| Latency per pair (p50 / p95) | 12.6 s / 16.3 s | 29.3 s / — |
| Total cost of the run | $0.14 | $0.65 |

¹ An earlier Haiku pass over the same 60 pairs misread one quantity (`/invoice/line_items/0/quantity`
on `pair-01045`; still routed to review), i.e. 99.95% field accuracy. The model is not
deterministic; treat 99.9–100% as the band.

## Four providers, one skill (native smoke subset, 7 pairs, 904 fields)

Same bundle, same prompts, same policy; only the model id changes. All routed through
OpenRouter via the OpenAI-compatible adapter; every entry passed `hawser models check`.

| model | field accuracy | doc pass | status accuracy | evidence coverage | cost / pair | p50 latency |
|---|---|---|---|---|---|---|
| Claude Haiku 5.5 | 100.0% | 7/7 | 7/7 | 97.3% | $0.0023 | 14.1 s |
| Claude Opus 5.5 | 100.0% | 7/7 | 7/7 | 97.3% | $0.0799 | 27.2 s |
| Gemini 2.5 Flash | 100.0% | 7/7 | 7/7 | 97.3% | $0.0109 | 16.9 s |
| GPT-4.1 mini | 99.0% | 6/7 | 6/7 | 97.3% | $0.0040 | 13.7 s |

GPT-4.1 mini's miss is a column shift on one purchase-order row (description absorbed the
quantity, quantity took the unit price, unit price took the line total). The arithmetic and
reconciliation rules caught the inconsistency and the case ended `needs_review` instead of
`accepted` — the runtime's safety net working as designed, and a reason the `status accuracy`
column matters as much as field accuracy.

## Direct vs workflow vs agent (native smoke subset, 7 pairs, Claude Haiku 5.5)

Same skill contract (inputs, output schema, validators), three ways of getting there:

| mode | what runs | field accuracy | status accuracy | evidence coverage | model calls / pair | cost / pair |
|---|---|---|---|---|---|---|
| direct | one extraction straight into the full reconciliation schema | 99.3% | **4/7** | 78.5% | 1.0 | $0.0046 |
| workflow | `workflow.py`: two extractions + deterministic align/arithmetic/reconcile | 100% | 7/7 | 97.3% | 2.0 | $0.0023 |
| agent | planner-built plan from the skill's contract (cached after the first case) | 100% | 7/7 | 100% | 2.14 | $0.0032 |

Direct mode is the control: the model reads both documents and *writes the reconciliation
itself*. It gets the extracted fields right (99.3%) but **accepts all three discrepant pairs**
— it reports zero discrepancies, so the expression validators that depend on the deterministic
`compute.*` outputs never fire, and the run has no evidence for computed fields. Orchestration
is what makes the result verifiable: the workflow and the planner both route the comparison
through deterministic tools, and both catch every discrepancy.

The planner, given only the goal, inputs, tool catalogue and limits, produced a plan with the
same shape as the hand-written workflow and two extra checks of its own: extract invoice,
extract PO, `validate` both, `compute.check_arithmetic`, `compute.align_line_items`,
`compute.reconcile`, `validate` the reconciliation, exit — on its first valid attempt. An earlier attempt was rejected
statically for inventing a tool argument name, which is why tool input schemas are now inferred
from signatures and checked before any plan runs.

## Reading the numbers

- **Why review rate is "high":** it equals the share of cases that *should* be reviewed — every
  injected discrepancy was detected (`rules.expressions.no_discrepancies`). Since Phase 7 the 12
  scanned pairs are OCR'd (RapidOCR, 300 dpi) and judged exactly like native pairs: on the
  2026-10-19 re-recording they score 100% field accuracy, 100% status accuracy and 97.2%
  evidence coverage, the same as the 48 native pairs, at $0.0026 per pair. Review rate fell from
  63.3% to 48.3% (= the discrepancy rate). No clean pair was sent to review, and no discrepant
  pair was accepted.
- **Evidence coverage** counts found fields with a page-anchored region (or a calculation
  trace). On native PDFs the few unanchored fields are values the model quoted with a label
  that spans two lines.
- **Haiku vs Opus:** identical quality on this synthetic set at 35× the price difference. Real
  scans, multi-page invoices and unseen layouts are where the gap would show; this dataset
  does not exercise those yet.

## Reproduce

```bash
python -m hawser_synth.dataset datasets/invoice-po/v1          # deterministic; keeps cassettes
hawser eval run --dataset datasets/invoice-po/v1 --model openrouter/anthropic/claude-haiku-5.5   # replay
hawser eval run --dataset datasets/invoice-po/v1 --tag smoke --model openrouter/anthropic/claude-opus-5.5
hawser eval run --dataset datasets/invoice-po/v1 --live --model <any registry id> --budget 2     # re-record
hawser eval gate <report-dir> --thresholds evals/thresholds.yaml
```

CI replays the native smoke subset on every PR (`.github/workflows/eval.yml`) and gates on
`evals/thresholds.yaml`.
