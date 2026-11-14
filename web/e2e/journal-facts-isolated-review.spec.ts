import { readFileSync } from 'node:fs'
import path from 'node:path'
import { expect, test, waitForDocumentReady } from './fixtures'

test('source facts show context, retain revision across pages and reject changed catalog', async ({ app, page }) => {
  const catalog = JSON.parse(readFileSync(path.resolve(import.meta.dirname, '..', '..', 'tests', 'golden', 'journal_vectors.json'), 'utf8')).catalog
  await page.addInitScript((fixture) => {
    localStorage.setItem('tavotto.journalCatalog', JSON.stringify(fixture))
    localStorage.setItem('tavotto.dev.journalCatalogUi', '1')
  }, catalog)
  const requests: Record<string, unknown>[] = []
  await page.route('**/api/journal-facts/journal', async (route) => {
    const body = route.request().postDataJSON() as Record<string, unknown>
    requests.push(body)
    if (body.offset === 1) {
      await route.fulfill({ status: 409, contentType: 'application/json', body: JSON.stringify({code: 'catalog_changed'}) })
      return
    }
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({
      journal_id: 'fx-beta', journal: { label: 'Fixture Beta', publisher_id: 'fx-pub' }, catalog_rev: 4, next_offset: 1,
      sync_state: 'offline', sync_code: 'network', catalog_completeness: 'unknown', semantic_review: 'not_authenticated',
      facts: [{ id: 'synthetic-fact', subject: 'raster_dpi', relation: 'gte', value: 600, unit: 'dpi', strength: 'recommended', stage: 'unknown', origin: 'journal', source: 's', quote: 'Synthetic figure quote', conditions: { figure_kind: 'combination' }, condition_text: 'Synthetic condition', context: [{ source: 's', quote: 'Synthetic contextual passage' }] }],
      coverage: [], sources: { s: { url: 'https://example.invalid/guide', retrieved: '2026-01-01' } },
    }) })
  })
  const running = await app()
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto(running.baseURL)
  await page.locator('[data-card="Fig1_kinetics.pdf"]').dblclick({timeout:30_000})
  await waitForDocumentReady(page)
  await page.locator('[data-rail="settings"]').click()
  await page.locator('[data-settings-shell] [data-section="spec"]').click()
  await page.locator('[data-journal-picker]').click()
  await page.locator('[data-journal-search]').fill('fixture beta')
  await page.locator('[data-journal-option="fx-beta"]').click()
  await page.getByRole('button', { name: '期刊要求' }).click()
  const viewer = page.locator('[data-journal-facts-view]')
  await expect(viewer).toBeVisible()
  await viewer.getByRole('button', { name:'查看来源事实' }).click()
  expect(requests).toHaveLength(0)
  await viewer.getByRole('button', { name:'读取来源' }).click()
  await expect(viewer.locator('[data-fact-id="synthetic-fact"]')).toContainText('Synthetic figure quote')
  await expect(viewer).toContainText('Synthetic contextual passage')
  await expect(viewer).toContainText('独立语义审校未认证')
  await expect(viewer.locator('a[href="https://example.invalid/guide"]')).toHaveCount(2)
  await viewer.getByRole('button', { name:'下一页' }).click()
  await expect(viewer.locator('[data-facts-error]')).toBeVisible()
  await expect(viewer.locator('[data-facts-page]')).toHaveCount(0)
  expect(requests).toHaveLength(2)
  expect(requests[0].journal_id).toBe('fx-beta')
  expect(requests[1].offset).toBe(1)
  expect(requests[1].expected_catalog_rev).toBe(4)
})
