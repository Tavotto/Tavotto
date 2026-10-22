/**
 * 导出对话框的缩略图说真话（PR3）：对象头缩略图旁有「示意 · 非最终文件」标注；
 * 勾上透明背景后画布范围的缩略图页面画成棋盘格。语言无关写法（chromium 与 chromium-en 各跑一遍）。
 */
import type { Page } from '@playwright/test'
import { addSecondFigure, expect, test } from './fixtures'

const CARD = '[data-card="Fig1_kinetics.pdf"]'

async function openExport(page: Page, baseURL: string) {
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto(baseURL)
  await page.locator(CARD).dblclick({ timeout: 30_000 })
  await expect(page.locator('[data-element-svg] > svg')).toBeVisible({ timeout: 120_000 })
  // 画布范围的缩略图 / 透明背景只在多图排版里有：第二张图进来
  await addSecondFigure(page)
  await expect(page.locator('[data-canvas-stage] [data-object-id]')).toHaveCount(2)
  await page.keyboard.press('ControlOrMeta+e')
  const dialog = page.getByRole('dialog')
  await expect(dialog).toBeVisible()
  return dialog
}

test('导出对象头：缩略图有「示意」标注，勾透明背景后页面是棋盘格', async ({ app, page }) => {
  test.setTimeout(180_000)
  const a = await app()
  const dialog = await openExport(page, a.baseURL)
  const target = dialog.locator('[data-export-target]')
  const note = target.locator('[data-export-thumb-note]')
  await expect(note).toBeVisible()
  await expect(note).not.toHaveText('')
  // 标注在对话框内、没有撑破它
  const nb = (await note.boundingBox())!
  const db = (await dialog.boundingBox())!
  expect(nb.x).toBeGreaterThanOrEqual(db.x)
  expect(nb.x + nb.width).toBeLessThanOrEqual(db.x + db.width + 0.5)

  // 默认（含 PNG、不透明）：页面是纸白，没有棋盘格
  const png = dialog.getByRole('checkbox', { name: 'PNG', exact: true })
  if (!(await png.isChecked())) await png.check()
  // 缩略图在左栏大预览里（对象头不再重复一份）
  const stage = dialog.locator('[data-export-preview-stage]')
  await expect(stage.locator('[data-thumb-page]')).toHaveCount(1)
  await expect(stage.locator('[data-thumb-transparent]')).toHaveCount(0)

  await dialog.getByRole('switch', { name: /透明背景|transparent/i }).click()
  await expect(stage.locator('[data-thumb-transparent]')).toHaveCount(1)
  await expect(stage.locator('pattern')).toHaveCount(1)
  // 标注仍在（棋盘格不替代「示意」）
  await expect(note).toBeVisible()
})
