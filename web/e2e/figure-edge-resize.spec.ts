import { addSecondFigure, expect, test } from './fixtures'
import type { Page } from '@playwright/test'

/**
 * 单图编辑拖画布（ADR 0161）：边 = 留白（图不动、画纸变大），角 = 等比改图幅（松手重新出图）。
 *
 * 只放 jsdom 量不到的：真浏览器里命中区 / 角柄按不按得到、**屏幕上量到的**画纸与图框、对角是否纹丝不动、
 * 胶囊在拖动中是否显示读数、撤销一步是否回到原样。逻辑在 `canvas/frameDrag.test.tsx` 与 `lib/framePad.test.ts`。
 * 定位认 `data-figure-edge` / `data-figure-corner` / `data-fast-edit-paper` / `data-object-id` / `data-drag-status`。
 */

async function toFastEdit(page: Page, baseURL: string) {
  await page.goto(baseURL)
  const card = page.locator('[data-card="Fig1_kinetics.pdf"]')
  await expect(card).toHaveCount(1, { timeout: 30_000 })
  await card.dblclick()
  await expect(page.locator('[data-context-back]')).toHaveCount(1)
  await expect(page.locator('[data-fast-edit-paper]')).toBeVisible()
  // 拖边只认精确 manifest：图渲染回来（冷启动要一会儿）命中区才挂出来
  await expect(page.locator('[data-figure-edge]')).toHaveCount(4, { timeout: 90_000 })
  await expect(page.locator('[data-figure-corner]')).toHaveCount(4)
  await expect(page.locator('[data-view-tweening]')).toHaveCount(0)
  // 取景随舞台尺寸安顿：等世界层变换连续 800 ms 不变再量
  const world = page.locator('[data-world-transform]')
  let last = ''
  let since = Date.now()
  await expect
    .poll(async () => {
      const now = (await world.evaluate((n) => (n as HTMLElement).style.transform)) ?? ''
      if (now !== last) {
        last = now
        since = Date.now()
      }
      return Date.now() - since
    }, { timeout: 30_000, intervals: [200], message: '取景一直在变，没有稳定下来' })
    .toBeGreaterThan(800)
}

/** 画纸框（= 画布：图 + 留白）；松手后新图回来之前画纸收起（只剩虚线占位），那时量不到 = null */
const paperBox = (page: Page) => page.locator('[data-fast-edit-paper]').boundingBox({ timeout: 1000 }).catch(() => null)
/** 图本身（面板对象）的屏幕框 */
const figureBox = (page: Page) => page.locator('[data-object-id]').first().boundingBox({ timeout: 1000 }).catch(() => null)

async function dragHandle(page: Page, sel: string, dx: number, dy: number, onMid?: () => Promise<void>) {
  const eb = (await page.locator(sel).boundingBox())!
  // 上 / 下边取靠左 15% 处按下：舞台底部居中的通知条（教程提示 / 构建中）会压在下边中段
  const horizontalStrip = sel.includes('"t"') || sel.includes('"b"')
  const x = horizontalStrip ? eb.x + eb.width * 0.15 : eb.x + eb.width / 2
  const y = eb.y + eb.height / 2
  await page.mouse.move(x, y)
  await page.mouse.down()
  await page.mouse.move(x + dx, y + dy, { steps: 6 })
  if (onMid) await onMid()
  await page.mouse.up()
}

test(
  '单图编辑拖右边往外：画纸变宽、图纹丝不动，拖动中胶囊说「扩充画布」，撤销一步回到原样',
  { tag: '@feature:canvas.single-figure-edge-resize' },
  async ({ app, page }) => {
    const a = await app()
    await toFastEdit(page, a.baseURL)
    const p0 = (await paperBox(page))!
    const f0 = (await figureBox(page))!
    await expect(page.locator('[data-page-edge]')).toHaveCount(0)
    await dragHandle(page, '[data-figure-edge="r"]', 60, 0, async () => {
      await expect(page.locator('[data-drag-status]')).toBeVisible()
      await expect(page.locator('[data-figure-dims]')).toHaveCount(1)
      const mid = (await paperBox(page))!
      expect(mid.width).toBeGreaterThan(p0.width + 40)
    })
    await expect(page.locator('[data-drag-status]')).toHaveCount(0)
    const p1 = (await paperBox(page))!
    const f1 = (await figureBox(page))!
    expect(p1.width).toBeGreaterThan(p0.width + 40)
    expect(Math.abs(p1.x - p0.x)).toBeLessThan(1.5)
    // 图本身不动、不重渲染
    expect(Math.abs(f1.x - f0.x)).toBeLessThan(1)
    expect(Math.abs(f1.width - f0.width)).toBeLessThan(1)
    // 画布外画淡出现（伸出画布的不进导出）
    await expect(page.locator('[data-page-outside-mask]')).toHaveCount(1)
    await page.keyboard.press('ControlOrMeta+z')
    await expect.poll(async () => Math.abs(((await paperBox(page))?.width ?? 1e9) - p0.width), { timeout: 10_000 }).toBeLessThan(1.5)
    await expect(page.locator('[data-page-outside-mask]')).toHaveCount(0)
  },
)

test('单图编辑拖右下角：等比改图幅，松手重新出图后左上角不动、宽高同比变大', async ({ app, page }) => {
  const a = await app()
  await toFastEdit(page, a.baseURL)
  const p0 = (await paperBox(page))!
  await dragHandle(page, '[data-figure-corner="br"]', 60, 0, async () => {
    await expect(page.locator('[data-drag-status]')).toBeVisible()
    await expect(page.locator('[data-figure-preview]')).toHaveCount(1)
    // 拖动中图本身纹丝不动（不写文档、不重渲染）
    const mid = (await paperBox(page))!
    expect(Math.abs(mid.width - p0.width)).toBeLessThan(1)
  })
  await expect.poll(async () => (await paperBox(page))?.width ?? 0, { timeout: 30_000 }).toBeGreaterThan(p0.width + 40)
  const p1 = (await paperBox(page))!
  expect(Math.abs(p1.x - p0.x)).toBeLessThan(1.5)
  expect(Math.abs(p1.y - p0.y)).toBeLessThan(1.5)
  expect(Math.abs(p1.width / p1.height - p0.width / p0.height)).toBeLessThan(0.03)
})

test('单图编辑拖左上角：松手并渲染回来后右下角在屏幕上不动；中间不闪旧图错位', async ({ app, page }) => {
  const a = await app()
  await toFastEdit(page, a.baseURL)
  const p0 = (await paperBox(page))!
  await dragHandle(page, '[data-figure-corner="tl"]', -50, 0)
  const right0 = p0.x + p0.width
  const bottom0 = p0.y + p0.height
  let maxDev = 0
  const t0 = Date.now()
  let settled = false
  while (Date.now() - t0 < 30_000 && !settled) {
    const pend = await page.locator('[data-figure-pending]').boundingBox({ timeout: 300 }).catch(() => null)
    const paper = await paperBox(page)
    const b = pend ?? paper
    if (b) {
      maxDev = Math.max(maxDev, Math.abs(b.x + b.width - right0), Math.abs(b.y + b.height - bottom0))
      settled = !pend && !!paper && paper.width > p0.width + 30
    }
    await page.waitForTimeout(30)
  }
  expect(settled, '渲染回来后没有变大').toBe(true)
  expect(maxDev, '松手到新图回来之间右下角偏离了原处（闪了错位的旧图）').toBeLessThan(2)
})

test('加第二张图进多图排版后，拖的是页面边而不是画布边', async ({ app, page }) => {
  const a = await app()
  await toFastEdit(page, a.baseURL)
  await addSecondFigure(page)
  await expect(page.locator('[data-page-sheet]')).toBeVisible()
  await expect(page.locator('[data-figure-edge]')).toHaveCount(0)
  await expect(page.locator('[data-figure-corner]')).toHaveCount(0)
  await expect(page.locator('[data-page-edge]')).toHaveCount(4)
})

test('单图编辑里 Esc 退出图内编辑、选中整张图后拖右下角：改的是图幅（重新出图、等比），不是把图片拉伸', async ({ app, page }) => {
  const a = await app()
  await toFastEdit(page, a.baseURL)
  // Esc 退出图内编辑，再点一下图选中整张
  await page.keyboard.press('Escape')
  const fb0 = (await figureBox(page))!
  await page.mouse.click(fb0.x + fb0.width / 2, fb0.y + fb0.height / 2)
  // 选区的缩放手柄不画：角 / 边只剩画布框上的那一组
  await expect(page.locator('[data-handle]')).toHaveCount(0)
  await expect(page.locator('[data-figure-corner]')).toHaveCount(4)
  const p0 = (await paperBox(page))!
  await dragHandle(page, '[data-figure-corner="br"]', 60, 0)
  await expect.poll(async () => (await paperBox(page))?.width ?? 0, { timeout: 30_000 }).toBeGreaterThan(p0.width + 40)
  const p1 = (await paperBox(page))!
  expect(Math.abs(p1.width / p1.height - p0.width / p0.height)).toBeLessThan(0.03)
  // 重新出图后图本身与画纸同框（没有被视觉拉伸出画纸）
  const f1 = (await figureBox(page))!
  expect(Math.abs(f1.width - p1.width)).toBeLessThan(2)
})
