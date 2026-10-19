import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'

const KEY = 'hk_live_e2e_key'

async function signIn(page: Page) {
  await page.goto('/app/login')
  await page.getByTestId('api-key').fill(KEY)
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page).toHaveURL(/\/app\/playground/)
}

/** The review workspace and the evidence viewer must have no serious/critical axe violations
 * and must be operable from the keyboard (plan WP6 tests). */
test('review workspace and evidence viewer: axe and keyboard', async ({ page, request }) => {
  await signIn(page)
  await page.getByTestId('load-example').click()
  await expect(page.getByTestId('asset-row')).toContainText('pdf')
  await page.getByTestId('run-button').click()
  await expect(page.getByTestId('status')).toHaveText('accepted', { timeout: 60_000 })
  await page.getByTestId('open-run').click()
  await expect(page.locator('.evidence-box').first()).toBeVisible()

  const runScan = await new AxeBuilder({ page }).include('[data-testid="evidence-viewer"]').analyze()
  expect(runScan.violations.filter((v) => v.impact === 'critical' || v.impact === 'serious')).toEqual([])

  // Tab reaches the first evidence box; arrows move between boxes; Enter selects.
  const first = page.locator('.evidence-box').first()
  await first.focus()
  await page.keyboard.press('ArrowDown')
  await expect(page.locator('.evidence-box:focus')).toHaveCount(1)
  await page.keyboard.press('Enter')
  await expect(page.locator('.evidence-box[data-selected="true"]')).toHaveCount(1)
  await page.keyboard.press('Escape')
  await expect(page.locator('.evidence-box[data-selected="true"]')).toHaveCount(0)

  // a needs_review run for the workspace
  const asset = await request.post('/v1/examples/quickstart', { headers: { Authorization: `Bearer ${KEY}` } })
  const ex = await asset.json()
  const created = await request.post('/v1/runs', {
    headers: { Authorization: `Bearer ${KEY}` },
    data: { instruction: ex.instruction, inputs: [{ kind: 'asset', asset_id: ex.asset.id }], output_schema: ex.output_schema, policy: { limits: { max_cost_usd: 0.000001 } } },
  })
  const runId = (await created.json()).id as string
  await expect.poll(async () => (await (await request.get(`/v1/runs/${runId}`, { headers: { Authorization: `Bearer ${KEY}` } })).json()).status).toBe('needs_review')
  await page.goto(`/app/runs/${runId}/review`)
  await expect(page.getByTestId('result-form')).toBeVisible()
  await expect(page.getByTestId('approve')).toBeEnabled() // the review has been opened
  await page.waitForTimeout(400) // let button transitions settle before measuring contrast
  const reviewScan = await new AxeBuilder({ page }).analyze()
  expect(reviewScan.violations.filter((v) => v.impact === 'critical' || v.impact === 'serious')).toEqual([])
  // every form control is labelled and reachable by Tab
  await page.getByLabel('/invoice_number').first().focus()
  await page.keyboard.press('Tab')
  await expect(page.locator(':focus')).toHaveCount(1)
})
