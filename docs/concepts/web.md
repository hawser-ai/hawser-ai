# The web application

`hawserd` serves the web app at `/app` (blueprint §23). It exposes runtime behaviour, not a chat
window: what was uploaded and how it was admitted, which steps ran, what each model call cost,
where every field came from, what validation decided, and what a reviewer changed.

```bash
make web                     # build web/ and bundle it into hawser-server (served at /app)
hawserd serve --dev          # http://127.0.0.1:8080/app, sign in with the printed dev key
cd web && pnpm dev           # hot-reloading dev server at :5173, API proxied to :8080
```

Sign in with an API key; it is kept in the browser session only. Everything the app shows is
read through the public API (`api/openapi/openapi.json`), and the TypeScript types are
generated from that document (`pnpm gen:api`, drift-checked in CI).

## Screens

| Screen | What it shows |
|---|---|
| **Playground** | Drag-drop upload → admission result inline (media class, pages, `document.needs_ocr`, limits); instruction; JSON Schema editor with a "from example JSON" helper; skill and mode pickers; the policy form (budget, repair attempts, review on failure, model pinning, raw JSON); run; live step/model/validation events over SSE. "Load the bundled example" admits the quickstart invoice with its schema and policy so the recording answers it. |
| **Runs** | Status, mode, skill, cost, latency, metadata; filters by status, skill, mode; search by id or metadata. |
| **Run detail** | Header with status, usage and budget share. Tabs: *Result* (annotated JSON tree; click a field → its evidence highlights), *Plan* (React Flow graph of the agent plan or the steps with status and cost), *Trace* (event timeline with payloads, steps, model calls with tokens/cost/latency), *Validation* (issues by severity, linked to fields), *Artifacts* (lineage: parents, producer, transform), *Revisions*. Pending approvals can be decided here. |
| **Evidence viewer** | The page rendered by the server (`GET /v1/assets/{id}/pages/{n}`), with every document-region evidence drawn as a box placed by percentage of the page size in PDF points, so any dpi works. Hover shows the quoted text, producer and confidence. Boxes are buttons: Tab reaches them, arrows move between them in reading order, Enter selects, Escape clears. Image assets use pixel coordinates the same way. |
| **Review workspace** | Source on the left, the result as a form on the right (one control per leaf, generated from the result and the request's schema, with the machine's issues inline and change tracking). Edits save with `If-Match` on the review version; a 409 shows what the other reviewer changed and offers to re-apply your edits on top of the latest version. Approve/reject; export original and reviewed JSON. |
| **Reviews** | The queue: runs resting on `needs_review`, open reviews, pending side-effect approvals, recent decisions. |
| **Evaluations** | Reports published with `hawser eval publish` (or uploaded here): metric cards, per-case accuracy chart and table (each case links to its run), failure clusters; compare any two with regressions and improvements highlighted. |
| **Registry** | Models (modalities, context, structured output, prices, hosting, regions, priors), tools (side effects, permissions, limits, input schema), skills (inputs, tools, validators, evidence requirements, manifest). |
| **Usage** | Cost and tokens by day, skill, model and status; per-run budget consumption. |
| **Policy** | The tenant default policy with a form and a JSON editor, checked in the browser against the Policy schema served by the API and validated again by the server on save. |
| **Settings** | Webhooks: register (secret shown once), list, delete, delivery log with the dead letters. |

## Tests

- Unit and component tests (Vitest, Testing Library, MSW) run on fixtures exported from real
  local runs (`web/src/mocks/fixtures`, regenerated with the script in the Phase 6 plan note).
- End to end (Playwright) runs against a real `hawserd serve --dev` that replays the bundled
  quickstart: the playground run with live steps and evidence for `total`, the review flow with
  a conflict, the other pages, plus an axe accessibility scan and keyboard navigation across
  evidence boxes and the review form.

## Deviations from the plan

- No Storybook: the component tests use the same real fixtures a Storybook would, and the
  design-system cost was not worth it at this stage.
- Plan graphs for agent runs are drawn from the ordered node list in the `plan.created` event;
  branch/loop edges are not reconstructed (the executor does not emit them yet).
- Audio and video evidence viewers come with Phases 8 and 9.
