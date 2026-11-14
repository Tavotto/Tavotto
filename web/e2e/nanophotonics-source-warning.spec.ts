import { readFileSync } from 'node:fs'
import path from 'node:path'
import { expect, test, waitForDocumentReady } from './fixtures'

test('saved historical journal source appears in settings and problems', async ({ app, page }) => {
  // Synthetic catalog: only the journal ID and historical source URL reproduce
  // the matching condition; no real journal data is fetched or installed.
  const catalog = JSON.parse(readFileSync(path.resolve(import.meta.dirname, '..', '..', 'tests', 'golden', 'journal_vectors.json'), 'utf8')).catalog
  const journal = structuredClone(catalog.journals['fx-beta'])
  journal.label = 'Nanophotonics'
  journal.sources.s10.url = 'https://www.degruyterbrill.com/journal/key/nanoph/html'
  journal.rules.min_raster_dpi.value = 600
  delete journal.rules.min_raster_dpi.snapshot
  catalog.journals.nanophotonics = journal
  await page.addInitScript((fixture) => {
    localStorage.setItem('tavotto.journalCatalog', JSON.stringify(fixture))
    localStorage.setItem('tavotto.dev.journalCatalogUi', '1')
  }, catalog)
  const a = await app()
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto(a.baseURL)
  await page.locator('[data-card="Fig1_kinetics.pdf"]').dblclick({ timeout: 30_000 })
  await waitForDocumentReady(page)
  await page.locator('[data-rail="settings"]').click()
  const shell = page.locator('[data-settings-shell]')
  await shell.locator('[data-section="spec"]').click()
  await page.locator('[data-journal-picker]').click()
  await page.locator('[data-journal-search]').fill('Nanophotonics')
  await page.locator('[data-journal-option="nanophotonics"]').click()
  const warning = page.locator('[data-journal-nanophotonics-warning="legacy"]')
  await expect(warning).toBeVisible()
  await expect(warning).toContainText('可能已过时')
  await expect(warning.locator('a')).toHaveAttribute('href', 'https://onlinelibrary.wiley.com/page/journal/21928614/homepage/author-guidelines')
  await expect(async () => {
    if (await shell.count()) await page.keyboard.press('Escape')
    await expect(shell).toHaveCount(0, { timeout: 1_500 })
  }).toPass({ timeout: 15_000 })
  await page.locator('[data-rail="problems"]').click()
  await expect(page.locator('[data-nanophotonics-problem-notice="legacy"]')).toBeVisible()
})
