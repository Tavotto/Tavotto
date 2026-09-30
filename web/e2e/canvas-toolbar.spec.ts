import { expect, test } from './fixtures'
import type { Page } from '@playwright/test'

/**
 * 画布底部的浮动工具条（2026-09-30 重设计 A1）：选择 / 文字 / 标注 ▾ / 序号 | 适应。
 *
 * 只放 jsdom 量不到的：工具条**画出来的盒子**在任何窗口宽度下都完整在画布面板里（不出画布、
 * 不出视口），并且不压住选中浮动栏——那条栏是 `fixed` 的、按窗口坐标落位，落位时给底部留了
 * `BOTTOM_SAFE`（canvas/context-bar/position.ts）。定位认稳定 `data-*`：
 * `data-canvas-toolbar` / `data-work-panel` / `data-context-bar` / `data-tool` /
 * `data-fit-canvas` / `data-zoom-menu`。
 */

interface Box {
  left: number
  top: number
  right: number
  bottom: number
}

const box = (page: Page, sel: string) =>
  page.evaluate((s) => {
    const el = document.querySelector(s)
    if (!el) return null
    const r = el.getBoundingClientRect()
    return { left: r.left, top: r.top, right: r.right, bottom: r.bottom } as Box
  }, sel)

const intersects = (a: Box, b: Box) =>
  Math.min(a.right, b.right) - Math.max(a.left, b.left) > 0.5 &&
  Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top) > 0.5

async function toLayout(page: Page, baseURL: string) {
  await page.goto(baseURL)
  const card = page.locator('[data-card="Fig1_kinetics.pdf"]')
  await expect(card).toHaveCount(1, { timeout: 30_000 })
  await card.dblclick()
  // 快速编辑：没有浮动工具条，缩放菜单仍够得着（没有标签行，悬在画布右上角）
  await expect(page.locator('[data-context-back]')).toHaveCount(1)
  await expect(page.locator('[data-canvas-toolbar]')).toHaveCount(0)
  await expect(page.locator('[data-zoom-menu]')).toHaveCount(1)
  await page.locator('[data-context-back]').click()
  await expect(page.locator('[data-canvas-toolbar]')).toHaveCount(1)
}

for (const width of [600, 780, 920, 1024]) {
  test(`浮动工具条 ${width} 宽：完整在画布面板里、不压选中浮动栏`, async ({ app, page }) => {
    await page.setViewportSize({ width, height: 700 })
    const a = await app()
    await toLayout(page, a.baseURL)

    // 窄断点下右栏是盖在画布上的抽屉，先收起再点图（`data-inspector-close`，见 multi-selection-bar.spec）
    const close = page.locator('[data-inspector-close]')
    if (await close.count()) await close.first().click()
    // 选中画布上的那张图：选中浮动栏出现
    await page.locator('[data-object-id]').first().click()
    await expect(page.locator('[data-context-bar]')).toHaveCount(1)

    const bar = await box(page, '[data-canvas-toolbar]')
    const panel = await box(page, '[data-work-panel]')
    expect(bar, '工具条没画出来').not.toBeNull()
    expect(panel).not.toBeNull()
    // 不出画布面板、不出视口
    expect(bar!.left, JSON.stringify({ bar, panel })).toBeGreaterThanOrEqual(panel!.left - 0.5)
    expect(bar!.right, JSON.stringify({ bar, panel })).toBeLessThanOrEqual(panel!.right + 0.5)
    expect(bar!.bottom, JSON.stringify({ bar, panel })).toBeLessThanOrEqual(panel!.bottom + 0.5)
    expect(bar!.left).toBeGreaterThanOrEqual(-0.5)
    expect(bar!.right).toBeLessThanOrEqual(width + 0.5)
    // 不压选中浮动栏
    const ctx = await box(page, '[data-context-bar]')
    expect(ctx).not.toBeNull()
    expect(intersects(bar!, ctx!), JSON.stringify({ bar, ctx })).toBe(false)

    // 五颗按钮都在、都够得着（盒子在工具条里）
    const inside = await page.evaluate(() => {
      const bar = document.querySelector('[data-canvas-toolbar]')!.getBoundingClientRect()
      return [...document.querySelectorAll('[data-canvas-toolbar] button')].map((b) => {
        const r = b.getBoundingClientRect()
        return r.width > 0 && r.left >= bar.left - 0.5 && r.right <= bar.right + 0.5
      })
    })
    expect(inside).toEqual([true, true, true, true, true])
  })
}

test('工具条上的文字工具与适应钮真的生效（钩子随元素搬了家）', async ({ app, page }) => {
  await page.setViewportSize({ width: 1024, height: 700 })
  const a = await app()
  await toLayout(page, a.baseURL)
  const text = page.locator('[data-canvas-toolbar] [data-tool="text"]')
  await expect(text).toHaveCount(1)
  await text.click()
  await expect(text).toHaveAttribute('data-active', 'true')
  await page.locator('[data-canvas-toolbar] [data-tool="select"]').click()
  await expect(text).not.toHaveAttribute('data-active', 'true')
  // 缩放菜单在画布标签行里；适应钮在工具条里
  await expect(page.locator('[data-zoom-menu]')).toHaveCount(1)
  await page.locator('[data-canvas-toolbar] [data-fit-canvas]').click()
})
