import type { Page } from '@playwright/test'
import { expect, test, waitForDocumentReady } from './fixtures'

/**
 * 路径文字（ADR 0190）：文字工具（T）点在一条斜着的路径上，就地打字、Enter 提交——字沿着这条路径排出来
 * （后端落字，画布照后端字形画）。判据：画布上字形的包围盒是斜着的（竖向范围明显大于横排同一串字的），
 * 不是文档字段；撤销之后路径文字消失、只剩那条线。
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

test.describe('路径文字', () => {
  test(
    '文字工具点在斜线路径上打字：字沿路径斜着排出来；撤销后路径文字消失',
    { tag: ['@feature:canvas.path-text'] },
    async ({ app, page }) => {
      const a = await app()
      const sheet = await openCanvas(page, a.baseURL)
      const at = (x: number, y: number) => ({ x: sheet.x + x, y: sheet.y + y })

      // 钢笔画一条斜线（两点，Enter 结束成开放路径）
      await page.keyboard.press('p')
      const [s, e] = [at(120, 330), at(480, 130)]
      await page.mouse.click(s.x, s.y)
      await page.waitForTimeout(80)
      await page.mouse.click(e.x, e.y)
      await page.waitForTimeout(80)
      await page.keyboard.press('Enter')
      await expect(page.locator('[data-canvas-stage] [data-object-id]')).toHaveCount(1)

      // 文字工具点在线的中点：落成路径文字并进入就地输入
      await page.keyboard.press('t')
      const mid = { x: (s.x + e.x) / 2, y: (s.y + e.y) / 2 }
      await page.mouse.click(mid.x, mid.y)
      const input = page.locator('[data-path-text-input]')
      await expect(input).toBeVisible()
      await page.keyboard.type('Slanted along path')
      await page.keyboard.press('Enter')

      // 后端落字后，画布上画的是字形（不再是浏览器顶替用的 <textPath>），整串字沿着斜线排：
      // 字形包围盒的高度与宽度之比远大于横排文字（横排一行字的高宽比远小于 0.2）
      const glyphs = page.locator('[data-path-text-glyphs]')
      await expect(glyphs).toHaveCount(1, { timeout: 60_000 })
      await expect(glyphs.locator('path').first()).toBeVisible({ timeout: 60_000 })
      const box = await glyphs.evaluate((g) => {
        const r = g.getBoundingClientRect()
        return { w: r.width, h: r.height }
      })
      expect(box.w).toBeGreaterThan(80)
      expect(box.h / box.w, '字应当沿斜线排，竖向范围明显大于横排文字').toBeGreaterThan(0.35)

      // 撤销：路径文字撤回（落成与打字是两步历史，撤两次；只剩钢笔画的那条线）
      await page.keyboard.press('Escape')
      await page.keyboard.press('ControlOrMeta+z')
      await page.keyboard.press('ControlOrMeta+z')
      await expect(page.locator('[data-path-text-glyphs]')).toHaveCount(0)
      await expect(page.locator('[data-canvas-stage] [data-object-id]')).toHaveCount(1)
    },
  )
})
