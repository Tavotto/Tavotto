import { addSecondFigure, expect, test } from './fixtures'
import type { Page } from '@playwright/test'

/**
 * 多图排版里拖面板（ADR 0161）：角 = 整张图等比缩放（图内 pt 跟着变，胶囊实时说），边 = 改图幅（往外往里都是、文字 pt 不变），按住 ⌥ 拖边 = 裁剪。
 *
 * 只放 jsdom 量不到的：真浏览器里手柄按不按得到、**拖动中**状态胶囊在不在（拖动即藏的旧规则不能吞掉它）、⌥ 是否真的到了手势、
 * 屏幕上量到的框。逻辑在 `canvas/panelEdgeCrop.test.tsx`、`canvas/layoutCornerFollow.test.tsx`、`lib/panelEdgeCrop.test.ts`。
 * 定位认 `data-handle` / `data-object-id` / `data-drag-status` / `data-drag-tone`。
 */

async function toLayout(page: Page, baseURL: string) {
  await page.goto(baseURL)
  const card = page.locator('[data-card="Fig1_kinetics.pdf"]')
  await expect(card).toHaveCount(1, { timeout: 30_000 })
  await card.dblclick()
  await expect(page.locator('[data-figure-edge]')).toHaveCount(4, { timeout: 90_000 })
  await addSecondFigure(page)
  await expect(page.locator('[data-view-tweening]')).toHaveCount(0)
  const first = page.locator('[data-object-id]').first()
  await first.click()
  await expect(page.locator('[data-handle="se"]')).toHaveCount(1)
  return first
}

async function dragHandle(page: Page, dir: string, dx: number, dy: number, onMid?: () => Promise<void>, alt = false) {
  const b = (await page.locator(`[data-handle="${dir}"]`).boundingBox())!
  const x = b.x + b.width / 2
  const y = b.y + b.height / 2
  await page.mouse.move(x, y)
  await page.mouse.down()
  if (alt) await page.keyboard.down('Alt')
  await page.mouse.move(x + dx, y + dy, { steps: 8 })
  if (onMid) await onMid()
  await page.mouse.up()
  if (alt) await page.keyboard.up('Alt')
}

test(
  '排版里拖面板的角：等比缩小，拖动中胶囊说缩放比与图内字号；撤销一步回到原样',
  { tag: '@feature:canvas.layout-panel-corner-edge' },
  async ({ app, page }) => {
    const a = await app()
    const first = await toLayout(page, a.baseURL)
    const b0 = (await first.boundingBox())!
    await dragHandle(page, 'se', -60, -40, async () => {
      const status = page.locator('[data-drag-status]')
      await expect(status).toBeVisible()
      await expect(status).toContainText('%')
      await expect(status).toContainText('pt')
    })
    await expect(page.locator('[data-drag-status]')).toHaveCount(0)
    const b1 = (await first.boundingBox())!
    expect(b1.width).toBeLessThan(b0.width - 30)
    // 等比：宽高比不变；左上角不动
    expect(Math.abs(b1.width / b1.height - b0.width / b0.height)).toBeLessThan(0.02)
    expect(Math.abs(b1.x - b0.x)).toBeLessThan(1.5)
    expect(Math.abs(b1.y - b0.y)).toBeLessThan(1.5)
    await page.keyboard.press('ControlOrMeta+z')
    await expect.poll(async () => Math.abs(((await first.boundingBox())?.width ?? 0) - b0.width)).toBeLessThan(1.5)
  },
)

test(
  '排版里拖面板的右边往里：改图幅缩小重新出图，文字 pt 不变；按住 ⌥ 才是裁剪',
  { tag: '@feature:canvas.layout-panel-corner-edge' },
  async ({ app, page }) => {
    const a = await app()
    const first = await toLayout(page, a.baseURL)
    const b0 = (await first.boundingBox())!
    await dragHandle(page, 'e', -60, 0, async () => {
      await expect(page.locator('[data-drag-status]')).toContainText('改图幅')
      await expect(page.locator('[data-figure-preview]')).toHaveCount(1)
    })
    await expect.poll(async () => (await first.boundingBox())!.width, { timeout: 60_000 }).toBeLessThan(b0.width - 40)
    await expect(page.locator('[data-figure-pending]')).toHaveCount(0, { timeout: 60_000 })
    const b1 = (await first.boundingBox())!
    expect(Math.abs(b1.width - b0.width + 60)).toBeLessThan(8)
    expect(Math.abs(b1.height - b0.height)).toBeLessThan(1.5)
    expect(Math.abs(b1.x - b0.x)).toBeLessThan(1.5)
    // 按住 ⌥：裁剪，不重新出图——没有预览框，左边与高度不动
    await dragHandle(page, 'e', -30, 0, async () => {
      await expect(page.locator('[data-figure-preview]')).toHaveCount(0)
      await expect(page.locator('[data-drag-status]')).not.toContainText('改图幅')
    }, true)
    const b2 = (await first.boundingBox())!
    expect(b2.width).toBeLessThan(b1.width - 20)
    expect(Math.abs(b2.x - b0.x)).toBeLessThan(1.5)
    expect(Math.abs(b2.height - b0.height)).toBeLessThan(1.5)
  },
)

test(
  '排版里拖面板的右边往外：改图幅重新出图，高度与左边不动；撤销一步回到原样',
  { tag: '@feature:canvas.layout-panel-corner-edge' },
  async ({ app, page }) => {
    const a = await app()
    const first = await toLayout(page, a.baseURL)
    const b0 = (await first.boundingBox())!
    await dragHandle(page, 'e', 80, 0, async () => {
      await expect(page.locator('[data-drag-status]')).toContainText('改图幅')
      await expect(page.locator('[data-drag-status]')).toContainText('文字 pt 不变')
      await expect(page.locator('[data-figure-preview]')).toHaveCount(1)
      if (process.env.TAVOTTO_E2E_SHOT) await page.screenshot({ path: `${process.env.TAVOTTO_E2E_SHOT}/during.png` })
      // 拖动中不动文档：面板还是原来的宽
      expect(Math.abs((await first.boundingBox())!.width - b0.width)).toBeLessThan(1.5)
    })
    await expect(page.locator('[data-drag-status]')).toHaveCount(0)
    await expect.poll(async () => (await first.boundingBox())!.width, { timeout: 60_000 }).toBeGreaterThan(b0.width + 60)
    await expect(page.locator('[data-figure-pending]')).toHaveCount(0, { timeout: 60_000 })
    const b1 = (await first.boundingBox())!
    expect(Math.abs(b1.width - b0.width - 80)).toBeLessThan(8)
    expect(Math.abs(b1.height - b0.height)).toBeLessThan(1.5)
    expect(Math.abs(b1.x - b0.x)).toBeLessThan(1.5)
    if (process.env.TAVOTTO_E2E_SHOT) await page.screenshot({ path: `${process.env.TAVOTTO_E2E_SHOT}/after.png` })
    await page.keyboard.press('ControlOrMeta+z')
    await expect.poll(async () => Math.abs(((await first.boundingBox())?.width ?? 0) - b0.width), { timeout: 60_000 }).toBeLessThan(1.5)
  },
)
