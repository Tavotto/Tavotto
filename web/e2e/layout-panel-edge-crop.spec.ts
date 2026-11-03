import { addSecondFigure, expect, test } from './fixtures'
import type { Page } from '@playwright/test'

/**
 * 多图排版里拖面板（ADR 0161）：角 = 整张图等比缩放（图内 pt 跟着变，胶囊实时说），边 = 裁剪（停在图边 / 内容边缘，⌥ 才裁进内容）。
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

test('排版里拖面板的右边往里：停在内容边缘（胶囊提示按 ⌥），按住 ⌥ 才裁进内容', async ({ app, page }) => {
  const a = await app()
  const first = await toLayout(page, a.baseURL)
  const b0 = (await first.boundingBox())!
  // 示例图的内容几乎贴着图边：一推就到内容边缘，不按 ⌥ 越不过去
  await dragHandle(page, 'e', -80, 0, async () => {
    await expect(page.locator('[data-drag-status]')).toHaveAttribute('data-drag-tone', 'warn')
  })
  const b1 = (await first.boundingBox())!
  expect(b1.width).toBeGreaterThan(b0.width - 15)
  // 按住 ⌥：裁进内容，胶囊转成危险语气
  await dragHandle(page, 'e', -80, 0, async () => {
    await expect(page.locator('[data-drag-status]')).toHaveAttribute('data-drag-tone', 'danger')
  }, true)
  const b2 = (await first.boundingBox())!
  expect(b2.width).toBeLessThan(b0.width - 50)
  // 裁剪不缩放：左边不动、高度不变
  expect(Math.abs(b2.x - b0.x)).toBeLessThan(1.5)
  expect(Math.abs(b2.height - b0.height)).toBeLessThan(1.5)
})
