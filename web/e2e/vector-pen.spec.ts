import type { Page } from '@playwright/test'
import { expect, test, waitForDocumentReady } from './fixtures'

/**
 * 钢笔（P）与锚点编辑（ADR 0185 M1）：点几下画出一条闭合路径（一个 path 对象）；
 * Enter / 双击进锚点编辑，拖一个锚点，路径的形状与屏幕包围盒真的跟着变。
 * 判据：画布上那个对象画的 `<path>` 的 d 带不带闭合、拖锚点前后 d 与框变没变，不是文档字段。
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

const lastObject = (page: Page) => page.locator('[data-canvas-stage] [data-object-id]').last()
const boxOf = (page: Page) =>
  lastObject(page).evaluate((e) => {
    const r = e.getBoundingClientRect()
    return { x: r.x, y: r.y, w: r.width, h: r.height }
  })
/** 画布上这条路径可见那条 <path> 的 d（取最长的那条，端型等小图元不算） */
const pathD = (page: Page) =>
  lastObject(page).evaluate((e) => {
    const ds = [...e.querySelectorAll('svg path')].map((p) => p.getAttribute('d') ?? '')
    return ds.sort((a, b) => b.length - a.length)[0] ?? ''
  })

test.describe('钢笔与锚点编辑', () => {
  test(
    'P 钢笔点三个点再点回起点：画出一条闭合路径；Enter 进锚点编辑、拖一个锚点，形状真的变了',
    { tag: ['@feature:canvas.pen-tool'] },
    async ({ app, page }) => {
      const a = await app()
      const sheet = await openCanvas(page, a.baseURL)
      const pt = (x: number, y: number) => ({ x: sheet.x + x, y: sheet.y + y })

      // ── 钢笔：三个角点，再点回起点闭合 ──────────────────────────────────
      await page.keyboard.press('p')
      const [p0, p1, p2] = [pt(120, 260), pt(260, 120), pt(400, 260)]
      for (const p of [p0, p1, p2, p0]) {
        await page.mouse.click(p.x, p.y)
        await page.waitForTimeout(80)
      }
      await expect(page.locator('[data-canvas-stage] [data-object-id]')).toHaveCount(1)
      const d0 = await pathD(page)
      expect(d0, '闭合路径的 d 应以 Z 结束').toMatch(/z\s*$/i)
      const box0 = await boxOf(page)
      // 三角形的包围盒大致就是三个点围出的范围（描边线宽以内的差）
      expect(Math.abs(box0.w - 280)).toBeLessThan(12)
      expect(Math.abs(box0.h - 140)).toBeLessThan(12)

      // ── Enter 进锚点编辑：三个锚点都在 ──────────────────────────────────
      await page.keyboard.press('v')
      await page.keyboard.press('Enter')
      const hits = page.locator('[data-path-anchor-hit]')
      await expect(hits).toHaveCount(3)

      // ── 拖顶点往上 80 px：形状与屏幕框变了 ───────────────────────────────
      const apex = (await hits.evaluateAll((els) =>
        els.map((e) => {
          const r = e.getBoundingClientRect()
          return { x: r.x + r.width / 2, y: r.y + r.height / 2 }
        }),
      )).sort((m, n) => m.y - n.y)[0]
      await page.mouse.move(apex.x, apex.y)
      await page.mouse.down()
      await page.mouse.move(apex.x, apex.y - 80, { steps: 8 })
      await page.mouse.up()
      await expect.poll(async () => (await boxOf(page)).h, { message: '顶点上拖后路径应变高' }).toBeGreaterThan(box0.h + 60)
      expect(await pathD(page)).not.toBe(d0)

      // ── Esc 退出锚点编辑；一次撤销回到拖之前（没拖的锚点不动） ─────────────
      await page.keyboard.press('Escape')
      await expect(hits).toHaveCount(0)
      await page.keyboard.press('ControlOrMeta+z')
      await expect.poll(async () => Math.abs((await boxOf(page)).h - box0.h)).toBeLessThan(2)
    },
  )
})
