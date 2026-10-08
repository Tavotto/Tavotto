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
const scatter = 'axes_0.scatter_0'
const title = 'axes_0.title'
const row = (page: Page, gid: string) => page.locator(`[data-el="${gid}"]`)
const rail = (page: Page) => page.locator('[data-rail="elements"]')

async function openTree(page: Page) {
  if (await rail(page).getAttribute('aria-expanded') !== 'true') {
    // Narrow layouts put an open property drawer's scrim above the rail.
    await dismissOverlay(page)
    await rail(page).click({ timeout: 15_000 })
  }
  await expect(rail(page)).toHaveAttribute('aria-expanded', 'true')
  await expect(page.locator('[data-left-drawer]')).toHaveAttribute('data-state', 'open')
  // Assert the worker's actual canonical IDs before any input can exhaust the test timeout.
  for (const gid of [line, scatter, title]) await expect(row(page, gid)).toBeVisible()
}

async function dismissOverlay(page: Page) {
  const scrim = page.locator('[data-scrim][data-state="open"]')
  if (await scrim.count()) {
    await scrim.click({ timeout: 15_000 })
    await expect(scrim).toHaveCount(0)
  }
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

    if (width === 1100) {
      // Space drag changes the viewport, leaving both selection and the tree workflow intact.
      const stage = await page.locator('[data-canvas-stage]').boundingBox()
      expect(stage).not.toBeNull()
      await page.keyboard.down('Space')
      try {
        const x = stage!.x + stage!.width / 2
        const y = stage!.y + stage!.height / 2
        await page.mouse.move(x, y)
        await page.mouse.down()
        await page.mouse.move(x + 24, y + 12, { steps: 4 })
        await page.mouse.up()
      } finally {
        await page.keyboard.up('Space')
      }
      await assertSelection(page, [title], exclusive)

      // Drag the same selected title: sidebar swapping must wait until pointer tracking ends.
      const target = await page.locator('[data-element-svg] svg [id="axes_0.title"]').boundingBox()
      const beforeDrag = await page.locator('[data-canvas-stage]').boundingBox()
      expect(target).not.toBeNull()
      expect(beforeDrag).not.toBeNull()
      await page.mouse.move(target!.x + target!.width / 2, target!.y + target!.height / 2)
      await page.mouse.down()
      try {
        await page.mouse.move(target!.x + target!.width / 2 + 24,
          target!.y + target!.height / 2 + 12, { steps: 4 })
        // Let the input's React effects settle before measuring the live gesture.
        await page.evaluate(() => new Promise<void>(resolve => {
          requestAnimationFrame(() => requestAnimationFrame(() => resolve()))
        }))
        await assertSelection(page, [title], exclusive)
        const duringDrag = await page.locator('[data-canvas-stage]').boundingBox()
        expect(duringDrag?.x).toBe(beforeDrag!.x)
      } finally {
        await page.mouse.up()
      }
      await expect(page.locator('[data-inspector-panel]')).toHaveAttribute('data-state', 'open')
      await openTree(page)
      await row(page, title).click()
      await assertSelection(page, [title], exclusive)

      // Escape cancels tracking without pointercancel; later mouseup must keep the tree.
      const cancelTarget = await page.locator('[data-element-svg] svg [id="axes_0.title"]').boundingBox()
      const beforeCancel = await page.locator('[data-canvas-stage]').boundingBox()
      expect(cancelTarget).not.toBeNull()
      expect(beforeCancel).not.toBeNull()
      await page.mouse.move(cancelTarget!.x + cancelTarget!.width / 2,
        cancelTarget!.y + cancelTarget!.height / 2)
      await page.mouse.down()
      try {
        await page.mouse.move(cancelTarget!.x + cancelTarget!.width / 2 + 24,
          cancelTarget!.y + cancelTarget!.height / 2 + 12, { steps: 4 })
        await assertSelection(page, [title], exclusive)
        await page.keyboard.press('Escape')
        await assertSelection(page, [title], exclusive)
      } finally {
        await page.mouse.up()
      }
      await assertSelection(page, [title], exclusive)
      const afterCancel = await page.locator('[data-canvas-stage]').boundingBox()
      expect(afterCancel?.x).toBe(beforeCancel!.x)
    }

    // Focus remains in the tree while the same element is clicked on the canvas.
    // In overlay mode close the drawer explicitly to expose the canvas; the medium
    // mode exercises stale tree focus without an overlay obscuring the target.
    if (width < 1024) {
      await dismissOverlay(page)
      await expect(page.locator('[data-left-drawer]')).toHaveCount(0)
    }
    await clickCanvasTitle(page)
    await expect(page.locator('[data-inspector-panel]')).toHaveAttribute('data-state', 'open')
    if (exclusive) await expect(rail(page)).toHaveAttribute('aria-expanded', 'false')

    // The existing context-menu action explicitly requests all properties.
    await openTree(page)
    await row(page, title).click()
    if (width < 1024) {
      await dismissOverlay(page)
      await expect(page.locator('[data-left-drawer]')).toHaveCount(0)
    }
    await clickCanvasTitle(page, 'right')
    await page.locator('[data-quick-item="open-inspector"]').click()
    await expect(page.locator('[data-inspector-panel]')).toHaveAttribute('data-state', 'open')
    if (exclusive) {
      // An explicit property visit consumes the tree workflow when the user dismisses it.
      if (width < 1024) await dismissOverlay(page)
      else await page.locator('[data-inspector-close]').click()
      await expect(page.locator('[data-inspector-panel]')).toHaveCount(0)
      await clickCanvasTitle(page)
      await expect(page.locator('[data-inspector-panel]')).toHaveCount(0)
    }
  })
}

test.describe('narrow touchscreen', () => {
  test.use({ viewport: { width: 820, height: 900 }, hasTouch: true })
  test('touchscreen narrow drawer keeps curve, scatter and title selection', async ({ app, page }) => {
    const a = await app({ figures: syntheticProject() })
    await openFigure(page, a)
    for (const gid of [line, scatter, title]) {
      await row(page, gid).tap()
      await assertSelection(page, [gid], true)
    }
  })
})
