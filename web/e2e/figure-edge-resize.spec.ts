import { addSecondFigure, expect, test } from './fixtures'
import type { Page } from '@playwright/test'

/**
 * 单图编辑里直接拖这张图的四条边改图幅（与多图排版里拖页面边同一套手感与标注）。
 *
 * 只放 jsdom 量不到的：真浏览器里命中区压在图边缘上能不能按到、拖动后**屏幕上量到的**图框、对侧边是否纹丝不动、
 * 撤销一步是否回到原样。逻辑（吸附 / 夹取 / 一次拖动一个事务）在 `canvas/figureEdgeResize.test.tsx`。
 * 定位认 `data-figure-edge` / `data-figure-dims` / `data-object-id`。
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

/** 图的背纸框；松手后新图回来之前背纸收起（只剩虚线占位），那时量不到 = null */
const paperBox = (page: Page) => page.locator('[data-fast-edit-paper]').boundingBox({ timeout: 1000 }).catch(() => null)

async function dragEdge(page: Page, edge: 'l' | 'r' | 't' | 'b', dx: number, dy: number, onMid?: () => Promise<void>) {
  const eb = (await page.locator(`[data-figure-edge="${edge}"]`).boundingBox())!
  // 上 / 下边取靠左 15% 处按下：舞台底部居中的通知条（教程提示 / 构建中）会压在下边中段
  const x = edge === 't' || edge === 'b' ? eb.x + eb.width * 0.15 : eb.x + eb.width / 2
  const y = eb.y + eb.height / 2
  await page.mouse.move(x, y)
  await page.mouse.down()
  await page.mouse.move(x + dx, y + dy, { steps: 6 })
  if (onMid) await onMid()
  await page.mouse.up()
}

test(
  '单图编辑拖右边：拖动中只有虚线框与标注，松手图幅变宽（重渲染）、左边不动，撤销一步回到原样',
  { tag: '@feature:canvas.single-figure-edge-resize' },
  async ({ app, page }) => {
    const a = await app()
    await toFastEdit(page, a.baseURL)
    const p0 = (await paperBox(page))!
    await expect(page.locator('[data-figure-edge]')).toHaveCount(4)
    await expect(page.locator('[data-page-edge]')).toHaveCount(0)
    await dragEdge(page, 'r', 60, 0, async () => {
      await expect(page.locator('[data-figure-dims]')).toHaveCount(1)
      await expect(page.locator('[data-figure-preview]')).toHaveCount(1)
      await expect(page.locator('[data-dim-readout="width"]')).toContainText(/\d/)
      // 拖动中图本身纹丝不动（不写文档、不重渲染）
      const mid = (await paperBox(page))!
      expect(Math.abs(mid.width - p0.width)).toBeLessThan(1)
      const pv = (await page.locator('[data-figure-preview]').boundingBox())!
      expect(pv.width).toBeGreaterThan(p0.width + 40)
    })
    await expect(page.locator('[data-figure-dims]')).toHaveCount(0)
    await expect(page.locator('[data-figure-preview]')).toHaveCount(0)
    // 松手后图按新图幅重渲染，面板随之变宽
    await expect.poll(async () => (await paperBox(page))?.width ?? 0, { timeout: 30_000 }).toBeGreaterThan(p0.width + 40)
    const p1 = (await paperBox(page))!
    expect(Math.abs(p1.x - p0.x)).toBeLessThan(1.5)
    expect(Math.abs(p1.height - p0.height)).toBeLessThan(1.5)
    // 一次拖动 = 一步撤销
    await page.keyboard.press('ControlOrMeta+z')
    await expect.poll(async () => Math.abs(((await paperBox(page))?.width ?? 1e9) - p0.width), { timeout: 30_000 }).toBeLessThan(1.5)
  },
)

test('单图编辑拖左边：松手并渲染回来后右边在屏幕上不动，图向左长出去；中间不闪旧图错位', async ({ app, page }) => {
  const a = await app()
  await toFastEdit(page, a.baseURL)
  const p0 = (await paperBox(page))!
  await dragEdge(page, 'l', -50, 0)
  const right0 = p0.x + p0.width
  // 采样松手后一段时间里「图的右边」：渲染没回来时看虚线占位框，回来后看背纸；任何时刻都不许偏离原处（不闪错位的旧图）
  let maxDev = 0
  let sawPlaceholder = false
  const t0 = Date.now()
  let settled = false
  while (Date.now() - t0 < 30_000 && !settled) {
    const pend = await page.locator('[data-figure-pending]').boundingBox({ timeout: 300 }).catch(() => null)
    const paper = await paperBox(page)
    const b = pend ?? paper
    if (pend) sawPlaceholder = true
    if (b) {
      maxDev = Math.max(maxDev, Math.abs(b.x + b.width - right0))
      settled = !pend && !!paper && paper.width > p0.width + 30 && Math.abs(paper.x + paper.width - right0) < 1.5
    }
    await page.waitForTimeout(30)
  }
  expect(settled, '渲染回来后右边没回到原处').toBe(true)
  console.log(`[figure-edge-resize] 拖左边松手后右边最大偏移 ${maxDev.toFixed(1)} px，见过占位 ${sawPlaceholder}`)
  expect(maxDev, '松手到新图回来之间右边偏离了原处（闪了错位的旧图）').toBeLessThan(2)
})

test('单图编辑拖下边改高、拖上边：对侧不动', async ({ app, page }) => {
  const a = await app()
  await toFastEdit(page, a.baseURL)
  const p0 = (await paperBox(page))!
  await dragEdge(page, 'b', 0, 40)
  await expect.poll(async () => (await paperBox(page))?.height ?? 0, { timeout: 30_000 }).toBeGreaterThan(p0.height + 25)
  const p1 = (await paperBox(page))!
  expect(Math.abs(p1.y - p0.y)).toBeLessThan(1.5)
  await dragEdge(page, 't', 0, -30)
  await expect.poll(async () => (await paperBox(page))?.height ?? 0, { timeout: 30_000 }).toBeGreaterThan(p1.height + 15)
  const p2 = (await paperBox(page))!
  expect(Math.abs(p2.y + p2.height - (p1.y + p1.height))).toBeLessThan(1.5)
})

test('加第二张图进多图排版后，拖的是页面边而不是图边', async ({ app, page }) => {
  const a = await app()
  await toFastEdit(page, a.baseURL)
  await addSecondFigure(page)
  await expect(page.locator('[data-page-sheet]')).toBeVisible()
  await expect(page.locator('[data-figure-edge]')).toHaveCount(0)
  await expect(page.locator('[data-page-edge]')).toHaveCount(4)
})
