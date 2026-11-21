import type { Page } from '@playwright/test'
import { expect, test, waitForDocumentReady } from './fixtures'

/**
 * 形状转为路径（ADR 0185）：右键「转为路径」后画面一点不动、对象真的变成 path、一次撤销回到形状；
 * 变成 path 后命中沿真实轮廓——空心路径只在线上点得中，中间点空白处不选。
 * 判据是画布上这个对象此刻画的是 `<rect>` 还是 `<path>`、它的屏幕框动没动，不是文档字段。
 */

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    try {
      const key = 'tavotto.ui'
      const saved = JSON.parse(localStorage.getItem(key) || '{}')
      localStorage.setItem(key, JSON.stringify({ ...saved, prefsVersion: 2, snapEnabled: false }))
    } catch {
      /* 存储不可用时照常跑 */
    }
  })
})

async function openCanvas(page: Page, baseURL: string) {
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto(baseURL)
  await expect(page.locator('[data-page-sheet]')).toBeVisible({ timeout: 30_000 })
  await waitForDocumentReady(page)
  await page.waitForTimeout(500)
  return (await page.locator('[data-page-sheet]').boundingBox())!
}

/** 按工具键，再在页面纸上拖一笔（坐标相对页面纸左上角） */
async function drawBy(page: Page, sheet: { x: number; y: number }, key: string, x0: number, y0: number, x1: number, y1: number) {
  await page.keyboard.press(key)
  await page.mouse.move(sheet.x + x0, sheet.y + y0)
  await page.mouse.down()
  await page.mouse.move(sheet.x + x1, sheet.y + y1, { steps: 6 })
  await page.mouse.up()
  await page.waitForTimeout(250)
}

const objectBox = (page: Page) =>
  page.locator('[data-canvas-stage] [data-object-id]').last().evaluate((e) => {
    const r = e.getBoundingClientRect()
    return { x: r.x, y: r.y, w: r.width, h: r.height }
  })

test.describe('矢量路径', () => {
  test(
    '右键「转为路径」：画面不动、矩形变成 path、一次撤销回到矩形；空心路径沿轮廓点得中',
    { tag: ['@feature:canvas.vector-path'] },
    async ({ app, page }) => {
      const a = await app()
      const sheet = await openCanvas(page, a.baseURL)

      // 画一个空心矩形（默认只有描边）
      await drawBy(page, sheet, 'r', 120, 120, 320, 260)
      const obj = page.locator('[data-canvas-stage] [data-object-id]').last()
      await expect(obj.locator('svg rect')).toHaveCount(1)
      const before = await objectBox(page)

      // 右键 →「转为路径」
      await page.mouse.click(before.x + before.w * 0.25, before.y + 1, { button: 'right' })
      const item = page.locator('[data-quick-item="convert-to-path"]')
      await expect(item).toBeVisible()
      await item.click()

      // 画的东西从 <rect> 变成了 <path>，屏幕框几乎不动（路径的框按曲线实际范围算，允许描边线宽级的差）
      await expect(obj.locator('svg rect')).toHaveCount(0)
      await expect(obj.locator('svg path').first()).toBeVisible()
      const after = await objectBox(page)
      expect(Math.abs(after.x - before.x)).toBeLessThan(4)
      expect(Math.abs(after.y - before.y)).toBeLessThan(4)
      expect(Math.abs(after.w - before.w)).toBeLessThan(4)
      expect(Math.abs(after.h - before.h)).toBeLessThan(4)

      // 命中沿真实轮廓：点中间的空白，选区被清掉；点在上边线上，选中它
      await page.mouse.click(after.x + after.w / 2, after.y + after.h / 2)
      await expect(page.locator('[data-context-bar]')).toHaveCount(0)
      await page.mouse.click(after.x + after.w * 0.25, after.y + 1)
      await expect(page.locator('[data-context-bar]')).toHaveCount(1)

      // 一次撤销回到矩形
      await page.keyboard.press('ControlOrMeta+z')
      await expect(obj.locator('svg rect')).toHaveCount(1)
      await expect(obj.locator('svg path')).toHaveCount(0)
      const undone = await objectBox(page)
      expect(Math.abs(undone.x - before.x)).toBeLessThan(1)
      expect(Math.abs(undone.w - before.w)).toBeLessThan(1)
    },
  )
})
