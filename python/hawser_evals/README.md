# hawser-evals

Measure how accurate a [Hawser](https://pypi.org/project/hawser-ai/) workflow is on your own
documents: datasets with expected answers, field-level scoring, metrics, failure clusters,
reports, model comparisons and CI gates.

```bash
pip install hawser-evals
hawser eval run --dataset path/to/dataset --model openrouter/anthropic/claude-haiku-5.5 --out eval-out
hawser eval gate eval-out --thresholds thresholds.yaml   # fail CI on a regression
hawser compare old-eval-out/ eval-out/                  # what got better or worse
```

Guide: https://github.com/hawser-ai/hawser-ai/blob/main/docs/concepts/evals.md
