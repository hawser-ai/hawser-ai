# Quickstart

Ten minutes from install to a verified extraction with page-level evidence.

## 1. Install

```bash
pip install hawser            # once published; today: `uv sync --all-packages` in the repo
hawser --version
```

## 2. Initialise a project

```bash
mkdir invoices && cd invoices
hawser init
```

This writes `hawser.toml`, a local `.hawser/` store (runs, artifacts, events) and a
`quickstart/` folder with a synthetic invoice, its schema, a policy and a recorded model response.

## 3. Run the recorded example (no API key)

```bash
hawser quickstart
```

You get a status panel, the extracted JSON, and a **Fields** table: every field with its value,
the page it was found on, the bounding box in PDF points and the anchoring confidence. Usage says
`replayed from cassette` — nothing was billed.

## 4. Run it live

```bash
export ANTHROPIC_API_KEY=...
hawser quickstart --live
```

Same request, real model, real cost (a few cents at most under the bundled `policy.yaml`).
Through OpenRouter instead (any OpenAI-compatible endpoint works the same way):

```bash
export OPENROUTER_API_KEY=...
hawser quickstart --live --model openrouter/anthropic/claude-opus-5.5
```

The bundle also ships a *real* recording of exactly that OpenRouter run, so
`hawser quickstart --model openrouter/anthropic/claude-opus-5.5` (without `--live`) replays a
genuine Claude Opus 5.5 response offline. Cassette entries say whether they are `live` or
`synthetic`.

## 5. Inspect a run

```bash
hawser runs list
hawser inspect run_...            # steps, fields, validation, usage
hawser inspect run_... --events   # the full event timeline
hawser inspect run_... --field /total
```

## 6. Your own documents

```bash
hawser run -i "Extract the invoice." -f my-invoice.pdf --schema quickstart/invoice.schema.json
```

Or from Python:

```python
import hawser
from pydantic import BaseModel

class Receipt(BaseModel):
    merchant: str
    date: str
    total: float

result = hawser.run("Extract the receipt.", inputs=["receipt.pdf"], output_schema=Receipt)
print(result.status)                       # accepted | needs_review | rejected | failed
print(result.parse_as(Receipt))
for ev in result.evidence_for("/total"):
    print(ev.page, ev.bbox, ev.text)       # where the total came from
```

## What "accepted" means

A result is **accepted** only when it conforms to your schema, every required field was found,
and no error-severity validation rule fired. Fields the model could not find are `unknown`,
never guessed; if a required one is unknown the run ends `needs_review` (or `rejected` when
`review_on_failure: false`). Missing evidence is a warning you can escalate in the policy:

```yaml
validation:
  escalate:
    evidence_missing: error
```

## Recording and replay

Set `HAWSER_RECORD_MODE=auto` with `--cassette DIR` to record live calls once and replay them
afterwards (tests, demos, CI). Replayed calls are marked in usage and cost nothing.
