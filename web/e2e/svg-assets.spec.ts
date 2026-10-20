import { mkdirSync, mkdtempSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { expect, test } from './fixtures'

test('SVG 素材：真实解码、加入画布并保持矢量预览', { tag: '@feature:assets.svg-preview' }, async ({ app, page }) => {
  const dir = mkdtempSync(path.join(os.tmpdir(), 'tavotto-svg-'))
  mkdirSync(dir, { recursive: true })
  writeFileSync(path.join(dir, 'diagram.svg'), '<svg xmlns="http://www.w3.org/2000/svg" width="96" height="48" viewBox="0 0 96 48"><rect width="96" height="48" fill="#e06040"/></svg>')
  const a = await app({ figures: dir })
  await page.goto(a.baseURL)
  const card = page.locator('[data-card="diagram.svg"]')
  await expect(card).toBeVisible()
  const image = card.locator('img')
  await expect.poll(() => image.evaluate((el: HTMLImageElement) => el.complete && el.naturalWidth)).toBe(96)
  await expect(image).toBeInViewport()
  const box = await image.boundingBox()
  expect(box && box.width > 0 && box.height > 0).toBeTruthy()
  await card.click()
  await page.locator('[data-asset-add]').click()
  const placed = page.locator('[data-object-id] img').filter({ visible: true })
  await expect(placed).toHaveCount(1)
  await expect.poll(() => placed.evaluate((el: HTMLImageElement) => el.complete && el.naturalWidth)).toBe(96)
  await expect(placed).toBeInViewport()
  await expect(placed).toHaveAttribute('src', /\/api\/file\?id=diagram.svg/)
})
