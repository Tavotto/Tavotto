import type { Page } from '@playwright/test'
import { expect, test, waitForDocumentReady } from './fixtures'

/**
 * 路径查找器（ADR 0185 M2）：选两个重叠的矩形，属性栏点「联集」——两个对象换成一个 path，
 * 它的屏幕包围盒就是两个矩形合起来的范围；一次撤销回到两个矩形。
 * 判据：画布上对象个数、有没有 <rect>、合成对象的屏幕框，不是文档字段。
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

async function drawRect(page: Page, sheet: { x: number; y: number }, x0: number, y0: number, x1: number, y1: number) {
  await page.keyboard.press('r')
  await page.mouse.move(sheet.x + x0, sheet.y + y0)
  await page.mouse.down()
  await page.mouse.move(sheet.x + x1, sheet.y + y1, { steps: 6 })
  await page.mouse.up()
  await page.waitForTimeout(250)
  // 加一层填充（新画的矩形默认空心），路径查找器按填充区域运算
  await page.locator('[data-inspector-panel] [data-effect-add]').first().click()
}

const objects = (page: Page) =>
  page.locator('[data-canvas-stage] [data-object-id]').evaluateAll((els) =>
    els.map((e) => {
      const r = e.getBoundingClientRect()
      return { x: r.x, y: r.y, w: r.width, h: r.height, rects: e.querySelectorAll('svg rect').length, paths: e.querySelectorAll('svg path').length }
    }),
  )

test.describe('路径查找器', () => {
  test(
    '选两个重叠矩形点「联集」：合成一个 path、框是两者合起来的范围；一次撤销回到两个矩形',
    { tag: ['@feature:canvas.pathfinder'] },
    async ({ app, page }) => {
      const a = await app()
      const sheet = await openCanvas(page, a.baseURL)

      // 左上 (100,100)-(260,220)，右下 (200,160)-(360,280)：重叠，合起来的范围 260 × 180
      await drawRect(page, sheet, 100, 100, 260, 220)
      await drawRect(page, sheet, 200, 160, 360, 280)
      const before = await objects(page)
      expect(before).toHaveLength(2)
      expect(before.every((o) => o.rects === 1)).toBe(true)

      // 全选 → 属性栏里的「联集」
      await page.keyboard.press('Escape')
      await page.keyboard.press('ControlOrMeta+a')
      // 路径查找器收在属性栏的折叠里，先展开
      await page.locator('[data-fold="pathfinder"] button').first().click()
      const unite = page.locator('[data-pathfinder-command="pathfinder-unite"]')
      await expect(unite).toBeVisible()
      await expect(unite).toBeEnabled({ timeout: 30_000 }) // 运算内核首次要编译
      await unite.click()

      // 联集之后：只剩一个对象，没有 <rect>、有 <path>，屏幕框 = 两个矩形的并集框
      await expect.poll(async () => (await objects(page)).length, { timeout: 30_000 }).toBe(1)
      const [merged] = await objects(page)
      expect(merged.rects).toBe(0)
      expect(merged.paths).toBeGreaterThan(0)
      const left = Math.min(...before.map((o) => o.x))
      const top = Math.min(...before.map((o) => o.y))
      const right = Math.max(...before.map((o) => o.x + o.w))
      const bottom = Math.max(...before.map((o) => o.y + o.h))
      expect(Math.abs(merged.x - left)).toBeLessThan(4)
      expect(Math.abs(merged.y - top)).toBeLessThan(4)
      expect(Math.abs(merged.x + merged.w - right)).toBeLessThan(4)
      expect(Math.abs(merged.y + merged.h - bottom)).toBeLessThan(4)

      // 一次撤销：两个矩形回来
      await page.keyboard.press('ControlOrMeta+z')
      await expect.poll(async () => (await objects(page)).length).toBe(2)
      expect((await objects(page)).every((o) => o.rects === 1)).toBe(true)
    },
  )
})
