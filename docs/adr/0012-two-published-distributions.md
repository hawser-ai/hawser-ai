# ADR-0012: Publish two distributions, `hawser-ai` and `hawser-server`

- **Status:** accepted
- **Date:** 2026-10-19
- **Phase:** release 0.1

## Context

The workspace grew five Python packages along layer boundaries: `hawser-ai` (SDK, runtime, CLI),
`hawser-media` (PDF, image and OCR primitives), `hawser-evals` (evaluation engine),
`hawser-server` (`hawserd`) and `hawser-synth` (synthetic documents). ADR-0011 planned to
publish all five. Looked at from a user's side, before anything was on PyPI:

- `hawser-ai` always depends on `hawser-media`, so a separate distribution saves no install.
- `hawser-evals` is small and adds no dependencies, yet the `hawser eval` command ships in
  `hawser-ai` and failed without it.
- `hawser-synth` serves the repository's tests, quickstart data and benchmark.
- `hawser-server` brings FastAPI, uvicorn, boto3, a Postgres driver and the bundled web app,
  which SDK users should not have to install.

Distribution names are permanent on PyPI, so merging later would mean deprecating published
packages.

## Decision

1. **`hawser-ai`** contains three import packages: `hawser`, `hawser_media` and `hawser_evals`
   (all under `python/hawser/src/`). Media's dependencies move into `hawser-ai`; its `ocr`
   extra becomes `hawser-ai[ocr]`. Import names do not change.
2. **`hawser-server`** stays a separate distribution.
3. **`hawser-synth`** stays a workspace package and is never published; it carries the
   `Private :: Do Not Upload` classifier, which PyPI rejects.

## Consequences

- `pip install hawser-ai` gives the SDK, CLI, media primitives and evaluations; OCR is the one
  optional extra.
- The release builds and uploads two distributions, each through its own trusted-publisher
  environment (`pypi` and `pypi-hawser-server`).
- Folding `hawser_media` and `hawser_evals` into `hawser.media` and `hawser.evals` remains
  possible later; it would change imports, so it waits for a reason.

## Alternatives considered

- Publish all five (ADR-0011's plan): more pages, more install instructions and an `eval`
  command that breaks without a second install, for no reduction in install size.
- One distribution including the server behind a `[server]` extra: the server has its own
  command, its own deployment story and heavy dependencies; a separate package keeps the SDK
  install small and the server's release notes and pages clear.
