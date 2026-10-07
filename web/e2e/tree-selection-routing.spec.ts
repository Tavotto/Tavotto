import { expect, test, type RunningApp } from './fixtures'
import { copyFileSync, mkdirSync, mkdtempSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import type { Page } from '@playwright/test'

// Synthetic public data only. The application's worker supplies the manifest / SVG.
function syntheticProject(): string {
  const dir = path.join(mkdtempSync(path.join(os.tmpdir(), 'tavotto-tree-routing-')), 'figures')
  mkdirSync(dir)
  writeFileSync(path.join(dir, 'synthetic.py'), [
    'import matplotlib', 'matplotlib.use("Agg")', 'import matplotlib.pyplot as plt',
    'from pathlib import Path', '', 'def main():',
    '    fig, ax = plt.subplots(figsize=(5, 3))',
    '    ax.plot([0, 1, 2], [0, 1, 0], label="Synthetic curve")',
    '    ax.scatter([.5, 1.5], [.25, .75], label="Synthetic points")',
    '    ax.set_title("Synthetic title")',
    '    fig.savefig(Path(__file__).with_name("Synthetic.pdf"))',
    '    plt.close(fig)', '', 'if __name__ == "__main__":', '    main()', '',
  ].join('\n'))
  writeFileSync(path.join(dir, 'tavotto_registry.json'), JSON.stringify({
    version: 1, scripts: { 'synthetic.py': { entry: 'main', cost: 'light', stems: ['Synthetic'] } },
  }))
  copyFileSync(path.join(import.meta.dirname, '..', '..', 'examples', 'figures', 'Fig1_kinetics.pdf'),
    path.join(dir, 'Synthetic.pdf'))
  return dir
}

const line = 'axes_0.lines_0'
const scatter = 'axes_0.collections_0'
const title = 'axes_0.title'
const row = (page: Page, gid: string) => page.locator(`[data-el="${gid}"]`)
const rail = (page: Page) => page.locator('[data-rail="elements"]')

async function openTree(page: Page) {
  if (await rail(page).getAttribute('aria-expanded') !== 'true') await rail(page).click()
  await expect(rail(page)).toHaveAttribute('aria-expanded', 'true')
  await expect(page.locator('[data-left-drawer]')).toHaveAttribute('data-state', 'open')
  await expect(row(page, line)).toBeVisible()
}

async function openFigure(page: Page, app: RunningApp) {
  await page.goto(app.baseURL)
  await page.locator('[data-card="Synthetic.pdf"]').dblclick({ timeout: 30_000 })
  await expect(page.locator('[data-element-svg] svg')).toBeVisible({ timeout: 60_000 })
  await openTree(page)
}

async function assertSelection(page: Page, gids: string[], exclusive: boolean) {
  await expect(rail(page)).toHaveAttribute('aria-expanded', 'true')
  await expect(page.locator('[data-left-drawer]')).toHaveAttribute('data-state', 'open')
  for (const gid of gids) await expect(row(page, gid)).toHaveAttribute('aria-selected', 'true')
  await expect(page.locator('[data-left-drawer] [data-el][aria-selected="true"]')).toHaveCount(gids.length)
  if (exclusive) await expect(page.locator('[data-inspector-panel]')).toHaveCount(0)
  else await expect(page.locator('[data-inspector-panel]')).toHaveAttribute('data-state', 'open')
}

async function clickCanvasTitle(page: Page, button: 'left' | 'right' = 'left') {
  const box = await page.locator('[data-element-svg] svg [id="axes_0.title"]').boundingBox()
  expect(box).not.toBeNull()
  // The transparent hit layer sits above the SVG; click screen coordinates through it.
  await page.mouse.click(box!.x + box!.width / 2, box!.y + box!.height / 2, { button })
}

for (const width of [820, 1100, 1366]) {
  test(`tree selection stays visible at ${width}px; canvas and explicit properties still route`, async ({ app, page }) => {
    await page.setViewportSize({ width, height: 900 })
    const a = await app({ figures: syntheticProject() })
    await openFigure(page, a)
    const exclusive = width < 1280
    for (const gid of [line, scatter, title]) {
      // Real click: includes pointerdown and the browser's default focus sequence.
      await row(page, gid).click()
      await assertSelection(page, [gid], exclusive)
    }
    await row(page, line).click()
    await row(page, scatter).click({ modifiers: ['Shift'] })
    await assertSelection(page, [line, scatter], exclusive)
    await row(page, line).click({ modifiers: ['ControlOrMeta'] })
    await assertSelection(page, [scatter], exclusive)
    await row(page, title).focus()
    await page.keyboard.press('Enter')
    await assertSelection(page, [title], exclusive)

    // Focus remains in the tree while the same element is clicked on the canvas.
    // In overlay mode close the drawer explicitly to expose the canvas; the medium
    // mode exercises stale tree focus without an overlay obscuring the target.
    if (width < 1024) {
      await rail(page).click()
      await expect(page.locator('[data-left-drawer]')).toHaveCount(0)
    }
    await clickCanvasTitle(page)
    await expect(page.locator('[data-inspector-panel]')).toHaveAttribute('data-state', 'open')
    if (exclusive) await expect(rail(page)).toHaveAttribute('aria-expanded', 'false')

    // The existing context-menu action explicitly requests all properties.
    await openTree(page)
    await row(page, title).click()
    if (width < 1024) {
      await rail(page).click()
      await expect(page.locator('[data-left-drawer]')).toHaveCount(0)
    }
    await clickCanvasTitle(page, 'right')
    await page.locator('[data-quick-item="open-inspector"]').click()
    await expect(page.locator('[data-inspector-panel]')).toHaveAttribute('data-state', 'open')
  })
}

test('touchscreen narrow drawer keeps curve, scatter and title selection', async ({ app, browser }) => {
  const a = await app({ figures: syntheticProject() })
  const context = await browser.newContext({ viewport: { width: 820, height: 900 }, hasTouch: true, locale: 'zh-CN' })
  try {
    const page = await context.newPage()
    await openFigure(page, a)
    for (const gid of [line, scatter, title]) {
      await row(page, gid).tap()
      await assertSelection(page, [gid], true)
    }
  } finally {
    await context.close()
  }
})
