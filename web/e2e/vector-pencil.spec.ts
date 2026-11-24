import type { Page } from '@playwright/test'
import { expect, test, waitForDocumentReady } from './fixtures'

/**
 * 铅笔（N，ADR 0185 M8）：拖一笔，松手后用内核把几十个采样点拟合成一条光滑的贝塞尔路径——
 * 画出来的是**一个 path 对象**，曲线段是三次贝塞尔（`C`），段数远少于鼠标采样点数；
 * 一次撤销回到画之前。判据：画布上这个对象画的 `<path>` 的 d，不是文档字段。
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

test.describe('铅笔', () => {
  test(
    'N 铅笔拖一条正弦形的笔迹：松手得到一个光滑 path（三次贝塞尔、段数远少于采样点）；一次撤销撤回',
    { tag: ['@feature:canvas.pencil'] },
    async ({ app, page }) => {
      const a = await app()
      const sheet = await openCanvas(page, a.baseURL)
      expect(await page.locator('[data-canvas-stage] [data-object-id]').count()).toBe(0)

      // 一笔：x 从 100 走到 460，y 随 sin 起伏，共 60 个采样点
      const N = 60
      const pts = Array.from({ length: N + 1 }, (_, i) => ({
        x: sheet.x + 100 + (360 * i) / N,
        y: sheet.y + 220 + 60 * Math.sin((i / N) * Math.PI * 2),
      }))
      await page.keyboard.press('n')
      await page.mouse.move(pts[0].x, pts[0].y)
      await page.mouse.down()
      for (const p of pts.slice(1)) await page.mouse.move(p.x, p.y)
      await page.mouse.up()

      const obj = page.locator('[data-canvas-stage] [data-object-id]')
      await expect(obj).toHaveCount(1, { timeout: 30_000 })
      const d = await obj.evaluate((e) => {
        const ds = [...e.querySelectorAll('svg path')].map((p) => p.getAttribute('d') ?? '')
        return ds.sort((m, n) => n.length - m.length)[0] ?? ''
      })
      const curves = (d.match(/C/g) ?? []).length
      expect(curves, '拟合出来的是三次贝塞尔曲线段').toBeGreaterThanOrEqual(2)
      expect(curves, '段数应远少于 60 个采样点（已拟合简化）').toBeLessThanOrEqual(15)

      // 画出来的东西就在笔迹上：对象的屏幕框覆盖 x 100–460、y 的起伏范围
      const box = await obj.evaluate((e) => {
        const r = e.getBoundingClientRect()
        return { x: r.x, y: r.y, w: r.width, h: r.height }
      })
      expect(Math.abs(box.x - pts[0].x)).toBeLessThan(10)
      expect(Math.abs(box.w - 360)).toBeLessThan(14)
      expect(box.h).toBeGreaterThan(80)

      await page.keyboard.press('ControlOrMeta+z')
      await expect(obj).toHaveCount(0)
    },
  )
})
