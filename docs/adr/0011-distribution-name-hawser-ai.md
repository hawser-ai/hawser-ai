# ADR-0011: Publish the core package as `hawser-ai`; keep `import hawser` and the `hawser` command

- **Status:** accepted; point 2 (which distributions are published) is superseded by ADR-0012
- **Date:** 2026-10-19
- **Phase:** release 0.1

## Context

ADR-0001 named the core distribution `hawser`. That name is taken on PyPI by an unrelated
project (a 2021 API wrapper with one release). PEP 541 transfers are slow and not guaranteed.

## Decision

1. The core distribution is **`hawser-ai`**: `pip install hawser-ai`. The import package stays
   `hawser`, and the console script stays `hawser`.
2. The other distributions keep their names: `hawser-media`, `hawser-server`, `hawser-evals`,
   `hawser-synth` (all free on PyPI).
3. Versions move together; inter-package dependencies use `>=X.Y.0,<X.(Y+1)` ranges.
4. `hawser.__version__` and `hawser_server.__version__` come from installed package metadata.

## Consequences

- Users install `hawser-ai` and write `import hawser`, like `pip install scikit-learn` /
  `import sklearn`. The docs state this once, next to every install command.
- Installing the unrelated `hawser` distribution alongside would clash on the `hawser` import
  package; this is unlikely and documented here.
- If the `hawser` name becomes available, a `hawser` meta-package depending on `hawser-ai` can
  be published without breaking anyone.

## Alternatives considered

- Rename the import package too (`hawser_ai`): churn in every user script for no benefit.
- Wait for a PEP 541 transfer: blocks the release on an external process.
