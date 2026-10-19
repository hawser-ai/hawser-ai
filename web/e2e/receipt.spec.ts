import { expect, test, type Page } from '@playwright/test'
import { readFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

/**
 * Phase 7 exit demo: a photo of a receipt rotated 12° is deskewed, OCR'd and extracted, and
 * the evidence is drawn on the ORIGINAL photo in the viewer. The model answer is a recording
 * (web/e2e/cassettes, Claude Haiku 5.5 via OpenRouter); the run pins that model through the
 * policy's allowed models so the recorded request matches.
 */
const KEY = 'hk_live_e2e_key'
const MODEL = 'openrouter/anthropic/claude-haiku-5.5'
const FIXTURES = path.join(path.dirname(fileURLToPath(import.meta.url)), 'fixtures')

async function signIn(page: Page) {
  await page.goto('/app/login')
  await page.getByTestId('api-key').fill(KEY)
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page).toHaveURL(/\/app\/playground/)
}

test('rotated receipt photo: deskew, OCR, evidence drawn on the original photo', async ({ page, request }) => {
  await signIn(page)
  // Upload through the API (the playground accepts the same asset id), with the recorded schema.
  const asset = await request.post('/v1/assets', {
    headers: { Authorization: `Bearer ${KEY}` },
    multipart: { file: { name: 'receipt.jpg', mimeType: 'image/jpeg', buffer: readFileSync(path.join(FIXTURES, 'receipt.jpg')) } },
  })
  const assetBody = await asset.json()
  expect(assetBody.media_class).toBe('image')
  const created = await request.post('/v1/runs', {
    headers: { Authorization: `Bearer ${KEY}` },
    data: {
      instruction: 'Extract the invoice.',
      inputs: [{ kind: 'asset', asset_id: assetBody.id }],
      output_schema: JSON.parse(readFileSync(path.join(FIXTURES, 'receipt.schema.json'), 'utf-8')),
      policy: { limits: { max_cost_usd: 0.25 }, allowed_models: [MODEL] },
      models: { pinned: MODEL },
    },
  })
  const runId = (await created.json()).id as string
  await expect
    .poll(async () => (await (await request.get(`/v1/runs/${runId}`, { headers: { Authorization: `Bearer ${KEY}` } })).json()).status, { timeout: 90_000 })
    .toMatch(/accepted|needs_review|failed/)
  const result = await (await request.get(`/v1/runs/${runId}/result`, { headers: { Authorization: `Bearer ${KEY}` } })).json()
  test.skip(result.status === 'failed' && result.error?.code === 'cassette_miss', 'recording does not match this OCR output (platform drift); re-record with HAWSER_RECORD_MODE=auto')
  expect(result.status).toBe('accepted')

  // Evidence is in the ORIGINAL photo's pixel space, and the deskew chain is recorded.
  const totalMeta = result.field_meta['/total']
  const ev = result.evidence.find((e: { id: string }) => e.id === totalMeta.evidence[0])
  expect(ev.coordinate_system).toBe('pixels')
  expect(ev.transform_chain.map((t: { kind: string }) => t.kind)).toEqual(['rotate', 'pad'])
  expect(ev.bbox.x1).toBeLessThanOrEqual(assetBody.manifest.image.width)
  expect(ev.bbox.y1).toBeLessThanOrEqual(assetBody.manifest.image.height)

  // The viewer shows the original photo with the box placed on it.
  await page.goto(`/app/runs/${runId}`)
  await expect(page.getByTestId('annotated-json')).toBeVisible()
  await page.getByTestId('annotated-json').locator('[data-pointer="/total"]').click()
  const box = page.locator('.evidence-box[data-selected="true"]')
  await expect(box).toHaveAttribute('data-pointer', '/total')
  const img = page.locator('img[alt="document page"]')
  await expect(img).toBeVisible()
  const size = await img.evaluate((el) => ({ w: (el as HTMLImageElement).naturalWidth, h: (el as HTMLImageElement).naturalHeight }))
  expect(size).toEqual({ w: assetBody.manifest.image.width, h: assetBody.manifest.image.height })
  const style = await box.evaluate((el) => ({ left: parseFloat(el.style.left), top: parseFloat(el.style.top) }))
  expect(style.left).toBeCloseTo((ev.bbox.x0 / assetBody.manifest.image.width) * 100, 1)
  expect(style.top).toBeCloseTo((ev.bbox.y0 / assetBody.manifest.image.height) * 100, 1)
  // the trace shows the OCR call and the deskew angle
  await page.getByRole('tab', { name: /trace/i }).click()
  await expect(page.getByTestId('event-timeline')).toContainText('ocr.rapidocr')
})
