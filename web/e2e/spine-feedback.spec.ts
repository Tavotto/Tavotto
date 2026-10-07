import { copyFileSync, mkdirSync, mkdtempSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { expect, test } from './fixtures'

function project() {
  const dir = path.join(mkdtempSync(path.join(os.tmpdir(), 'tavotto-spine-feedback-')), 'figures')
  mkdirSync(dir)
  writeFileSync(path.join(dir, 'feedback.py'), [
    'import matplotlib', 'matplotlib.use("Agg")',
    'import matplotlib.pyplot as plt', 'from pathlib import Path',
    'def main():',
    '    fig, ax = plt.subplots(figsize=(4, 3))',
    '    ax.plot([0, 1], [0.5, 0.5])',
    '    ax.set_xlim(0, 1)', '    ax.set_ylim(0, 1)',
    '    fig.subplots_adjust(left=.15, right=.9, bottom=.2, top=.9)',
    '    fig.savefig(Path(__file__).with_name("Fig_feedback.pdf"))',
    '    plt.close(fig)',
  ].join('\n'))
  writeFileSync(path.join(dir, 'tavotto_registry.json'), JSON.stringify({
    version: 1, scripts: { 'feedback.py': { entry: 'main', cost: 'light', stems: ['Fig_feedback'] } },
  }))
  // Asset-card placeholder; the production engine renders the synthetic script above.
  copyFileSync(path.join(import.meta.dirname, '../../examples/figures/Fig1_kinetics.pdf'), path.join(dir, 'Fig_feedback.pdf'))
  return dir
}

test('spine hover text paints above a later panel stacking context', { tag: '@feature:figure.select-and-edit' }, async ({ app, page }) => {
  const a = await app({ figures: project() })
  await page.goto(a.baseURL)
  const rendered = page.waitForResponse(r => r.url().includes('/api/engine/render') && r.status() === 200)
  await page.getByText('Fig_feedback.pdf').dblclick({ timeout: 30_000 })
  const { manifest } = await (await rendered).json()
  await expect(page.locator('[data-element-svg] svg').first()).toBeVisible({ timeout: 60_000 })
  await expect(page.locator('[data-authority="ready"]').first()).toBeVisible()
  await expect(page.locator('[data-world-transform][data-view-tweening]')).toHaveCount(0)
  const bottom = manifest.elements.find((e: { spines?: { bottom?: unknown } }) => e.spines?.bottom).spines.bottom
  const svg = (await page.locator('[data-element-svg] svg').first().boundingBox())!
  // The synthetic plot has no text or line at this exposed lower-left point.
  const x = svg.x + (bottom.from[0] * .75 + bottom.to[0] * .25) * svg.width
  const y = svg.y + bottom.from[1] * svg.height - 5
  await page.mouse.move(x, y)
  const label = page.locator('[data-spine-zone-label="bottom"]')
  await expect(label).toBeVisible()
  await page.evaluate(async () => { await document.fonts.ready })
  const rect = (await label.boundingBox())!
  // Sample the opaque top padding, away from glyph antialiasing and rounded corners.
  const clip = { x: Math.floor(rect.x + rect.width / 2) - 3, y: Math.ceil(rect.y) + 1, width: 6, height: 1 }
  const before = await page.screenshot({ clip })

  // Simulate a later opaque translated panel using the production world-layer
  // coordinate scale. It covers the label but leaves the actual hover point exposed.
  expect(y).toBeGreaterThan(rect.y + rect.height)
  await page.locator('[data-element-svg]').first().evaluate((svgElement, { rect, clip }) => {
    const object = svgElement.closest('[data-object-id]') as HTMLElement
    const canvas = object.parentElement!
    const origin = canvas.getBoundingClientRect()
    const zoom = object.getBoundingClientRect().width / parseFloat(object.style.width)
    const later = document.createElement('div')
    later.dataset.feedbackOccluder = ''
    Object.assign(later.style, {
      position: 'absolute', pointerEvents: 'none', background: 'rgb(255, 0, 0)', transform: 'translate(0px, 0px)',
      left: `${(rect.x - origin.x) / zoom}px`, top: `${(rect.y - origin.y) / zoom}px`,
      width: `${rect.width / zoom}px`, height: `${rect.height / zoom}px`,
    })
    canvas.append(later)
    const actual = later.getBoundingClientRect()
    if (actual.left > clip.x || actual.top > clip.y || actual.right < clip.x + clip.width || actual.bottom < clip.y + clip.height) {
      throw new Error('The occluder does not cover the sampled pixels')
    }
  }, { rect, clip })
  const covered = await page.screenshot({ clip })
  expect(covered.equals(before), 'opaque sibling must not paint over the spine status text').toBe(true)
  const feedback = page.locator('[data-spine-feedback-layer]')
  // Counterexample validates the pixel oracle: placing the feedback underneath
  // its siblings must change the sampled pixels. A DOM visibility check cannot do this.
  await feedback.evaluate(el => { (el as HTMLElement).style.zIndex = '-1' })
  expect((await page.screenshot({ clip })).equals(before)).toBe(false)
  await feedback.evaluate(el => { (el as HTMLElement).style.removeProperty('z-index') })
  expect((await page.screenshot({ clip })).equals(before)).toBe(true)
  await page.mouse.move(svg.x + svg.width / 2, svg.y + svg.height / 2)
  await expect(label).toHaveCount(0)
  await expect(feedback).toHaveCount(0)
  await page.locator('[data-feedback-occluder]').evaluate(el => el.remove())
})
