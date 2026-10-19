## What and why

<!-- One logical change. Link the issue it addresses. -->

## How it was tested

<!-- Tests added or changed; commands you ran. -->

## Checklist

- [ ] `make check` passes (and `make web-check` if `web/` changed)
- [ ] Contract changes have regenerated `api/schemas` / `api/openapi` (and `web/src/api/types.ts`)
- [ ] New model calls in tests use a cassette or `StaticAdapter` (no live calls in CI)
- [ ] User-facing change noted in `CHANGELOG.md` under "Unreleased"
