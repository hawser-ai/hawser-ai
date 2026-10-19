import { expect, test, type Page } from '@playwright/test'
import { readFileSync } from 'node:fs'
import { execSync } from 'node:child_process'

const KEY = 'hk_live_e2e_key'
const quickstart = execSync('cd .. && uv run python -c "from hawser.conformance.scenarios import _quickstart as q; print(q())"').toString().trim()

async function signIn(page: Page) {
  await page.goto('/app/login')
  await page.getByTestId('api-key').fill(KEY)
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page).toHaveURL(/\/app\/playground/)
}

test('playground run: load example, run, watch steps, open evidence for total', async ({ page }) => {
  await signIn(page)
  // the bundled example is exactly the request the recording answers (schema, policy, instruction)
  await page.getByTestId('load-example').click()
  await expect(page.getByTestId('asset-row')).toContainText('pdf')
  await expect(page.getByTestId('instruction')).toHaveValue('Extract the invoice.')
  await expect(page.getByLabel('Max cost (USD)')).toHaveValue('0.25')
  await page.getByTestId('run-button').click()
  await expect(page.getByTestId('event-timeline')).toContainText('step.completed', { timeout: 60_000 })
  await expect(page.getByTestId('status')).toHaveText('accepted', { timeout: 60_000 })
  await page.getByTestId('open-run').click()
  await expect(page.getByTestId('annotated-json')).toBeVisible()
  await page.getByTestId('annotated-json').locator('[data-pointer="/total"]').click()
  const box = page.locator('.evidence-box[data-selected="true"]')
  await expect(box).toHaveAttribute('data-pointer', '/total')
  await expect(page.locator('img[alt="document page"]')).toBeVisible()
  // keyboard navigation across evidence boxes
  await box.focus()
  await page.keyboard.press('ArrowDown')
  await expect(page.locator('.evidence-box:focus')).toHaveCount(1)
})

test('review workspace: correct a value with a conflict, approve, see the reviewed revision', async ({ page, request }) => {
  await signIn(page)
  // a run that stops on budget before any model call rests on needs_review
  const asset = await request.post('/v1/assets', {
    headers: { Authorization: `Bearer ${KEY}` },
    multipart: { file: { name: 'invoice.pdf', mimeType: 'application/pdf', buffer: readFileSync(`${quickstart}/invoice.pdf`) } },
  })
  const assetId = (await asset.json()).id as string
  const created = await request.post('/v1/runs', {
    headers: { Authorization: `Bearer ${KEY}` },
    data: {
      instruction: 'Extract the invoice.',
      inputs: [{ kind: 'asset', asset_id: assetId }],
      output_schema: JSON.parse(readFileSync(`${quickstart}/invoice.schema.json`, 'utf-8')),
      policy: { limits: { max_cost_usd: 0.000001 } },
    },
  })
  const runId = (await created.json()).id as string
  await expect.poll(async () => (await (await request.get(`/v1/runs/${runId}`, { headers: { Authorization: `Bearer ${KEY}` } })).json()).status).toBe('needs_review')

  await page.goto(`/app/runs/${runId}/review`)
  await expect(page.getByTestId('review-version')).toContainText('v1')
  const field = page.getByLabel('/invoice_number').first()
  await field.fill('INV-UI')
  // a colleague edits through the API first → our If-Match is stale
  await request.post(`/v1/reviews/${runId}/edit`, {
    headers: { Authorization: `Bearer ${KEY}`, 'If-Match': '"1"' },
    data: { changes: { '/total': 1 }, reviewer: 'colleague' },
  })
  await page.getByTestId('save-edits').click()
  await expect(page.getByTestId('conflict')).toBeVisible()
  await page.getByTestId('conflict-retry').click()
  await expect(page.getByTestId('conflict')).toBeHidden()
  await expect(page.getByTestId('review-version')).toContainText('v3')
  await page.getByTestId('approve').click()
  await expect(page.getByText(/approved by/)).toBeVisible()

  const revisions = await (await request.get(`/v1/runs/${runId}/revisions`, { headers: { Authorization: `Bearer ${KEY}` } })).json()
  expect(revisions.map((r: { kind: string }) => r.kind)).toEqual(['machine', 'reviewed'])
  expect(revisions[1].result.invoice_number).toBe('INV-UI')
  expect(revisions[1].result.total).toBe(1)
  const result = await (await request.get(`/v1/runs/${runId}/result`, { headers: { Authorization: `Bearer ${KEY}` } })).json()
  expect(result.status).toBe('accepted')
})

test('registry, usage, policy and evaluations pages load', async ({ page }) => {
  await signIn(page)
  await page.goto('/app/registry')
  await expect(page.getByText('anthropic/claude-opus-5-5')).toBeVisible()
  await page.goto('/app/usage')
  await expect(page.getByRole('heading', { name: 'Budget consumption per run' })).toBeVisible()
  await page.goto('/app/policy')
  await page.getByLabel('Max cost (USD)').fill(String((Math.floor(Math.random() * 900) + 100) / 1000))
  await page.getByTestId('save-policy').click()
  await expect(page.getByText('Saved.')).toBeVisible()
  await page.goto('/app/evals')
  await expect(page.getByRole('heading', { name: 'Evaluations' })).toBeVisible()
})
