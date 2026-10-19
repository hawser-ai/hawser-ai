/** MSW handlers backed by fixtures exported from real local runs (see web/src/mocks/fixtures). */
import { HttpResponse, http } from 'msw'
import asset from './fixtures/asset.json'
import evalGpt from './fixtures/eval_gpt.json'
import evalHaiku from './fixtures/eval_haiku.json'
import events from './fixtures/events.json'
import models from './fixtures/models.json'
import policy from './fixtures/policy.json'
import review from './fixtures/review.json'
import revisions from './fixtures/revisions.json'
import runRequest from './fixtures/run_request.json'
import runResult from './fixtures/run_result.json'
import runResultNeedsReview from './fixtures/run_result_needs_review.json'
import runSummary from './fixtures/run_summary.json'
import skills from './fixtures/skills.json'
import steps from './fixtures/steps.json'
import tools from './fixtures/tools.json'

export const BASE = 'http://test.local'
export const fixtures = { asset, events, models, policy, review, revisions, runResult, runResultNeedsReview, runSummary, skills, steps, tools, evalHaiku, evalGpt }

const ACCEPTED = runResult.run_id
const NEEDS_REVIEW = runResultNeedsReview.run_id

/** A mutable review so tests can simulate a concurrent editor bumping the version. */
export const state = {
  review: { ...review, version: 1, decisions: [] as { field: string; old: unknown; new: unknown; reviewer: string; at: string }[], status: 'open' as string },
  runStatus: { [ACCEPTED]: 'accepted', [NEEDS_REVIEW]: 'needs_review' } as Record<string, string>,
  policy: { ...policy },
}

export function resetState(): void {
  state.review = { ...review, version: 1, decisions: [], status: 'open' }
  state.runStatus = { [ACCEPTED]: 'accepted', [NEEDS_REVIEW]: 'needs_review' }
  state.policy = { ...policy }
}

const problem = (status: number, code: string, detail: string, extra: Record<string, unknown> = {}) =>
  HttpResponse.json({ type: `https://hawser.dev/errors/${code}`, title: code, status, code, detail, ...extra }, { status, headers: { 'Content-Type': 'application/problem+json' } })

export const handlers = [
  http.get(`${BASE}/readyz`, () => HttpResponse.json({ status: 'ready', version: 'test', database: true, jobs: {} })),
  http.get(`${BASE}/v1/runs`, ({ request }) => {
    const status = new URL(request.url).searchParams.get('status')
    const items = [
      { ...runSummary, id: ACCEPTED, status: 'accepted' },
      { ...runSummary, id: NEEDS_REVIEW, status: 'needs_review', cost_usd: 0 },
    ].filter((r) => !status || r.status === status)
    return HttpResponse.json({ items, next: null })
  }),
  http.get(`${BASE}/v1/runs/:id`, ({ params }) => {
    const id = String(params.id)
    if (!(id in state.runStatus)) return problem(404, 'run_unknown', `unknown run ${id}`)
    return HttpResponse.json({ ...runSummary, id, status: state.runStatus[id] })
  }),
  http.get(`${BASE}/v1/runs/:id/result`, ({ params }) => {
    const id = String(params.id)
    if (id === ACCEPTED) return HttpResponse.json(runResult)
    if (id === NEEDS_REVIEW) return HttpResponse.json({ ...runResultNeedsReview, status: state.runStatus[id] })
    return problem(404, 'run_unknown', `unknown run ${id}`)
  }),
  http.get(`${BASE}/v1/runs/:id/request`, () => HttpResponse.json(runRequest)),
  http.get(`${BASE}/v1/runs/:id/events`, () => HttpResponse.json(events)),
  http.get(`${BASE}/v1/runs/:id/steps`, () => HttpResponse.json(steps)),
  http.get(`${BASE}/v1/runs/:id/revisions`, () => HttpResponse.json(revisions)),
  http.get(`${BASE}/v1/assets/:id`, () => HttpResponse.json(asset)),
  http.get(`${BASE}/v1/assets/:id/pages/:page`, () =>
    HttpResponse.arrayBuffer(new Uint8Array([137, 80, 78, 71]).buffer, { headers: { 'Content-Type': 'image/png', 'X-Hawser-Dpi': '110' } }),
  ),
  http.get(`${BASE}/v1/reviews`, () => HttpResponse.json([state.review])),
  http.get(`${BASE}/v1/reviews/:id`, ({ params }) => {
    if (String(params.id) !== NEEDS_REVIEW) return problem(404, 'review_not_found', 'no review')
    return HttpResponse.json(state.review, { headers: { ETag: `W/"${state.review.version}"` } })
  }),
  http.get(`${BASE}/v1/reviews/:id/current`, () => {
    let result: unknown = runResultNeedsReview.result
    for (const d of state.review.decisions) result = { ...(result as object), [d.field.slice(1)]: d.new }
    return HttpResponse.json({ run_id: NEEDS_REVIEW, result })
  }),
  http.post(`${BASE}/v1/reviews/:id/open`, () => HttpResponse.json(state.review)),
  http.post(`${BASE}/v1/reviews/:id/edit`, async ({ request }) => {
    const ifMatch = request.headers.get('If-Match')?.replace(/^W\//, '').replace(/"/g, '')
    if (ifMatch !== undefined && Number(ifMatch) !== state.review.version)
      return problem(409, 'review_conflict', `review is at version ${state.review.version}, not ${ifMatch}`, { error_type: 'ReviewConflict' })
    const body = (await request.json()) as { changes: Record<string, unknown>; reviewer: string }
    for (const [field, value] of Object.entries(body.changes))
      state.review.decisions.push({ field, old: null, new: value, reviewer: body.reviewer, at: new Date().toISOString() })
    state.review.version += 1
    return HttpResponse.json(state.review, { headers: { ETag: `W/"${state.review.version}"` } })
  }),
  http.post(`${BASE}/v1/reviews/:id/approve`, ({ request }) => {
    const ifMatch = request.headers.get('If-Match')?.replace(/^W\//, '').replace(/"/g, '')
    if (ifMatch !== undefined && Number(ifMatch) !== state.review.version) return problem(409, 'review_conflict', 'stale')
    state.review.status = 'approved'
    state.review.version += 1
    state.runStatus[NEEDS_REVIEW] = 'accepted'
    return HttpResponse.json({ ...revisions[0], id: 'rev_01J9X3K4N7Q2M8R5T6V7W8X9Y1', kind: 'reviewed', author: 'test' })
  }),
  http.get(`${BASE}/v1/approvals`, () => HttpResponse.json([])),
  http.get(`${BASE}/v1/models`, () => HttpResponse.json(models)),
  http.get(`${BASE}/v1/tools`, () => HttpResponse.json(tools)),
  http.get(`${BASE}/v1/skills`, () => HttpResponse.json(skills)),
  http.get(`${BASE}/v1/policy`, () => HttpResponse.json(state.policy)),
  http.put(`${BASE}/v1/policy`, async ({ request }) => {
    state.policy = (await request.json()) as typeof policy
    return HttpResponse.json(state.policy)
  }),
  http.get(`${BASE}/v1/schemas/Policy`, () =>
    HttpResponse.json({
      title: 'Policy',
      type: 'object',
      additionalProperties: false,
      properties: { limits: { $ref: '#/$defs/Limits' }, review_on_failure: { type: 'boolean' }, mode: { anyOf: [{ type: 'string' }, { type: 'null' }] } },
      $defs: { Limits: { type: 'object', additionalProperties: false, properties: { max_cost_usd: { anyOf: [{ type: 'number', minimum: 0 }, { type: 'null' }] } } } },
    }),
  ),
  http.get(`${BASE}/v1/evals`, () =>
    HttpResponse.json([
      { id: 'eval_a', name: 'haiku', dataset: 'invoice-po/v1', skill: 'invoice-po-reconciliation', model: evalHaiku.config.model, mode: 'workflow', cases: evalHaiku.cases.length, metrics: evalHaiku.metrics, artifact_id: 'art_x', created_at: '2026-10-18T00:00:00.000000+00:00' },
      { id: 'eval_b', name: 'gpt', dataset: 'invoice-po/v1', skill: 'invoice-po-reconciliation', model: evalGpt.config.model, mode: 'workflow', cases: evalGpt.cases.length, metrics: evalGpt.metrics, artifact_id: 'art_y', created_at: '2026-10-18T00:00:00.000000+00:00' },
    ]),
  ),
  http.get(`${BASE}/v1/evals/:id/report`, ({ params }) => HttpResponse.json(params.id === 'eval_a' ? evalHaiku : evalGpt)),
  http.get(`${BASE}/v1/usage/breakdown`, () =>
    HttpResponse.json({
      tenant_id: 't',
      since: null,
      by_day: [{ key: '2026-10-18', runs: 2, cost_usd: 0.01, input_tokens: 10, output_tokens: 20, model_calls: 2 }],
      by_skill: [{ key: '(none)', runs: 2, cost_usd: 0.01, input_tokens: 10, output_tokens: 20, model_calls: 2 }],
      by_model: [{ key: 'claude-opus-5-5', runs: 2, cost_usd: 0.01, input_tokens: 10, output_tokens: 20, model_calls: 2 }],
      by_status: [{ key: 'accepted', runs: 2, cost_usd: 0.01, input_tokens: 10, output_tokens: 20, model_calls: 2 }],
      runs: [{ id: ACCEPTED, status: 'accepted', skill: null, created_at: '2026-10-18T00:00:00.000000+00:00', cost_usd: 0.01, max_cost_usd: 0.25, model_calls: 1 }],
    }),
  ),
  http.get(`${BASE}/v1/webhooks`, () => HttpResponse.json([])),
  http.get(`${BASE}/v1/webhooks/events`, () => HttpResponse.json(['run.completed', 'run.needs_review', 'run.awaiting_approval', 'review.decided'])),
]

export const ids = { ACCEPTED, NEEDS_REVIEW }
