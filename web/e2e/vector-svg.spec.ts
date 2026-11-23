import type { Page } from '@playwright/test'
import { expect, test, waitForDocumentReady } from './fixtures'

/**
 * 粘贴 SVG（ADR 0185 M5 / ADR 0189）：剪贴板里是一段 SVG 标记（Illustrator / Inkscape 复制出来的样子），
 * ⌘V 之后画布上落成**可编辑的对象**（不是一张当图片的位图）：每个图元一个对象、颜色原样、点得中、能改；一次撤销全部撤回。
 * 判据：画布上的对象个数与它们画的图元（没有 <img>）、点中后属性栏里的填充色，不是文档字段。
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
}

// 一个红矩形 + 一个蓝圆：两个互不重叠的图元
const SVG = [
  '<svg xmlns="http://www.w3.org/2000/svg" width="120" height="60" viewBox="0 0 120 60">',
  '<rect x="0" y="0" width="50" height="40" fill="#ff0000"/>',
  '<circle cx="90" cy="30" r="25" fill="#0000ff"/>',
  '</svg>',
].join('')

/** 派发一个带 SVG 文本的原生 paste 事件（与 ⌘V 读剪贴板走同一条 `handlePasteEvent`） */
async function pasteSvg(page: Page, svg: string) {
  await page.evaluate((text) => {
    const dt = new DataTransfer()
    dt.setData('text/plain', text)
    dt.setData('image/svg+xml', text)
    document.body.dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true }))
  }, svg)
}

const objects = (page: Page) =>
  page.locator('[data-canvas-stage] [data-object-id]').evaluateAll((els) =>
    els.map((e) => {
      const r = e.getBoundingClientRect()
      return { x: r.x, y: r.y, w: r.width, h: r.height, imgs: e.querySelectorAll('img, image').length, drawn: e.querySelectorAll('svg rect, svg ellipse, svg circle, svg path').length }
    }),
  )

test.describe('粘贴 SVG', () => {
  test(
    '粘贴一段 SVG：落成两个可编辑对象（不是图片），点中红的那个属性栏里是它的红色；一次撤销全撤回',
    { tag: ['@feature:canvas.svg-paste'] },
    async ({ app, page }) => {
      const a = await app()
      await openCanvas(page, a.baseURL)
      await page.locator('[data-canvas-stage]').click({ position: { x: 20, y: 20 } }) // 焦点回到画布

      expect(await objects(page)).toHaveLength(0)
      await pasteSvg(page, SVG)
      await expect.poll(async () => (await objects(page)).length, { timeout: 30_000 }).toBe(2)
      const objs = await objects(page)
      // 是形状 / 路径对象在画：没有一张位图，每个对象都有自己的矢量图元
      expect(objs.every((o) => o.imgs === 0 && o.drawn > 0)).toBe(true)
      // 两个对象并排：一个在左一个在右，宽度之比大致是 50 : 50（矩形宽 50，圆直径 50）
      const [left, right] = [...objs].sort((m, n) => m.x - n.x)
      expect(right.x).toBeGreaterThan(left.x + left.w * 0.8)
      expect(Math.abs(left.w / right.w - 1)).toBeLessThan(0.25)

      // 点中左边（红矩形）：属性栏的填充色就是 #FF0000——颜色原样带进来、对象能编辑
      await page.keyboard.press('Escape')
      await page.mouse.click(left.x + left.w / 2, left.y + left.h / 2)
      await expect
        .poll(() => page.locator('[data-inspector-panel] input').evaluateAll((els) => els.map((e) => (e as HTMLInputElement).value).join('|')))
        .toMatch(/#?FF0000/i)

      // 一次撤销：粘贴整体撤回
      await page.keyboard.press('ControlOrMeta+z')
      await expect.poll(async () => (await objects(page)).length).toBe(0)
    },
  )
})
