# Hawser quickstart

- `invoice.pdf` — a synthetic invoice (generated; no real company data).
- `invoice.schema.json` — the output schema (JSON Schema, from a Pydantic model).
- `policy.yaml` — budget, repair and review policy for the run.
- `cassette/` — recorded model responses so `hawser quickstart` works offline. Entries labelled
  `source: synthetic` were produced from the generator's ground truth (used for the default
  model); entries labelled `source: live` are real recordings (e.g. Claude Opus 5.5 via
  OpenRouter: `hawser quickstart --model openrouter/anthropic/claude-opus-5.5`).
  `hawser quickstart --live` runs the request against the real model and records it.

Equivalent explicit command:

    hawser run -i "Extract the invoice." -f quickstart/invoice.pdf \
      --schema quickstart/invoice.schema.json --policy quickstart/policy.yaml \
      --cassette quickstart/cassette
