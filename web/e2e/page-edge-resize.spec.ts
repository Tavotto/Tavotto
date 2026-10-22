import { addSecondFigure, expect, test } from './fixtures'
import type { Page } from '@playwright/test'

/**
 * 多图排版里拖页面四条边改画布大小（渐进式画布改版 · 裁决 5）。
 *
 * 只放 jsdom 量不到的：真浏览器里命中区压在纸面边缘上能不能按到、拖动后**屏幕上量到的**纸面与图的位置。
 * 逻辑（吸附 / 夹取 / 事务）在 `canvas/pageEdgeResize.test.tsx`。定位认 `data-page-edge` / `data-page-sheet` /
 * `data-ruler-dim` / `data-object-id`。
 */

async function toLayout(page: Page, baseURL: string) {
  await page.goto(baseURL)
  const card = page.locator('[data-card="Fig1_kinetics.pdf"]')
  await expect(card).toHaveCount(1, { timeout: 30_000 })
  await card.dblclick()
  // 一张图是单图编辑（没有纸面、没有拖边）；加第二张图才进多图排版
  await addSecondFigure(page)
  await expect(page.locator('[data-page-sheet]')).toBeVisible()
  // 进排版的取景补间走完再量几何（补间中途量到的是动画帧，不是拖边的结果）
  await expect(page.locator('[data-view-tweening]')).toHaveCount(0)
  // 切成排版之后侧栏 / 舞台宽度还会安顿一两秒，适应模式的取景随舞台尺寸重算：等世界层的变换连续 800 ms 不变再量
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
    }, { timeout: 30_000, intervals: [200], message: '排版取景一直在变，没有稳定下来' })
    .toBeGreaterThan(800)
}

const sheetBox = (page: Page) => page.locator('[data-page-sheet]').first().boundingBox()
const objBox = (page: Page) => page.locator('[data-object-id]').first().boundingBox()

test('拖右边改宽：纸面变宽、标注出现又消失、图不动', async ({ app, page }) => {
  const a = await app()
  await toLayout(page, a.baseURL)
  const s0 = (await sheetBox(page))!
  const o0 = (await objBox(page))!
  const edge = page.locator('[data-page-edge="r"]')
  const eb = (await edge.boundingBox())!
  const x = eb.x + eb.width / 2
  const y = eb.y + eb.height / 2
  await page.mouse.move(x, y)
  await page.mouse.down()
  await page.mouse.move(x + 60, y, { steps: 6 })
  await expect(page.locator('[data-ruler-dim]')).toHaveCount(1)
  await page.mouse.up()
  await expect(page.locator('[data-ruler-dim]')).toHaveCount(0)
  const s1 = (await sheetBox(page))!
  expect(s1.width).toBeGreaterThan(s0.width + 40)
  const o1 = (await objBox(page))!
  expect(o1.x).toBeCloseTo(o0.x, 0)
})

test('拖左边：纸面向左长出去，图在屏幕上不动', async ({ app, page }) => {
  const a = await app()
  await toLayout(page, a.baseURL)
  const s0 = (await sheetBox(page))!
  const o0 = (await objBox(page))!
  const eb = (await page.locator('[data-page-edge="l"]').boundingBox())!
  const x = eb.x + eb.width / 2
  const y = eb.y + eb.height / 2
  await page.mouse.move(x, y)
  await page.mouse.down()
  await page.mouse.move(x - 50, y, { steps: 6 })
  await page.mouse.up()
  const s1 = (await sheetBox(page))!
  expect(s1.width).toBeGreaterThan(s0.width + 30)
  const o1 = (await objBox(page))!
  expect(Math.abs(o1.x - o0.x)).toBeLessThan(1.5)
  expect(Math.abs(o1.y - o0.y)).toBeLessThan(1.5)
})

test('单图编辑里没有拖边', async ({ app, page }) => {
  const a = await app()
  await page.goto(a.baseURL)
  const card = page.locator('[data-card="Fig1_kinetics.pdf"]')
  await expect(card).toHaveCount(1, { timeout: 30_000 })
  await card.dblclick()
  await expect(page.locator('[data-context-back]')).toHaveCount(1)
  await expect(page.locator('[data-page-edge]')).toHaveCount(0)
})

// 读数住在标尺里（无框尺寸线 `|---- 157.3 mm ----|`）：拖宽在水平标尺、拖高在竖直标尺；两端对齐页面边；文字居中随线段中点走
type Box = { x: number; y: number; width: number; height: number }
for (const [edge, axis] of [['r', 'x'], ['l', 'x'], ['b', 'y'], ['t', 'y']] as const) {
  test(`拖${{ r: '右', l: '左', b: '下', t: '上' }[edge]}边：读数在${axis === 'x' ? '水平' : '竖直'}标尺内、无边框、两端对齐页面边、文字居中不抖`, async ({ app, page }) => {
    const a = await app()
    await toLayout(page, a.baseURL)
    const eb = (await page.locator(`[data-page-edge="${edge}"]`).boundingBox())!
    const x = eb.x + eb.width / 2
    const y = eb.y + eb.height / 2
    const sign = edge === 'r' || edge === 'b' ? 1 : -1
    await page.mouse.move(x, y)
    await page.mouse.down()
    const samples: { rb: Box; line: Box; sheet: Box }[] = []
    for (let i = 1; i <= 8; i++) {
      await page.mouse.move(axis === 'x' ? x + sign * i * 9 : x, axis === 'y' ? y + sign * i * 9 : y)
      const dim = page.locator(`[data-ruler-dim="${axis}"]`)
      await expect(dim).toHaveCount(1)
      await expect(page.locator(`[data-ruler-dim="${axis === 'x' ? 'y' : 'x'}"]`)).toHaveCount(0)
      const readout = dim.locator('[data-dim-readout]')
      await expect(readout).toHaveCount(1)
      const rb = (await readout.boundingBox())!
      const ruler = (await page.locator(`[data-ruler="${axis}"]`).boundingBox())!
      // 读数整个落在对应方向的标尺矩形里
      expect(rb.x).toBeGreaterThanOrEqual(ruler.x - 0.5)
      expect(rb.y).toBeGreaterThanOrEqual(ruler.y - 0.5)
      expect(rb.x + rb.width).toBeLessThanOrEqual(ruler.x + ruler.width + 0.5)
      expect(rb.y + rb.height).toBeLessThanOrEqual(ruler.y + ruler.height + 0.5)
      const line = (await dim.locator('[data-dim-line]').boundingBox())!
      const sheet = (await page.locator('[data-page-sheet]').first().boundingBox())!
      samples.push({ rb, line, sheet })
      // 线段两端 = 页面两条边（±1 px；页面边在可视区内时）
      const [a0, a1, s0, s1] = axis === 'x' ? [line.x, line.x + line.width, sheet.x, sheet.x + sheet.width] : [line.y, line.y + line.height, sheet.y, sheet.y + sheet.height]
      if (s0 >= (axis === 'x' ? ruler.x : ruler.y) && s1 <= (axis === 'x' ? ruler.x + ruler.width : ruler.y + ruler.height)) {
        expect(Math.abs(a0 - s0)).toBeLessThanOrEqual(1)
        expect(Math.abs(a1 - s1)).toBeLessThanOrEqual(1)
      }
      // 文字中心 = 线段中点（沿标尺方向）
      const c = axis === 'x' ? rb.x + rb.width / 2 : rb.y + rb.height / 2
      expect(Math.abs(c - (a0 + a1) / 2)).toBeLessThanOrEqual(1)
    }
    // 无边框、无胶囊：读数自己没有 border / 圆角
    const style = await page.locator('[data-dim-readout]').evaluate((el) => {
      const cs = getComputedStyle(el)
      return { b: [cs.borderTopWidth, cs.borderRightWidth, cs.borderBottomWidth, cs.borderLeftWidth], r: cs.borderTopLeftRadius }
    })
    expect(style.b).toEqual(['0px', '0px', '0px', '0px'])
    expect(style.r).toBe('0px')
    // 垂直于标尺的那一轴上位置恒定；文字盒的大小只随数字（tabular-nums）几乎不变；中心的逐帧移动 = 线段中点的移动（相邻帧无额外跳动）
    for (let i = 1; i < samples.length; i++) {
      const p = samples[i - 1]
      const q = samples[i]
      if (axis === 'x') expect(q.rb.y).toBeCloseTo(p.rb.y, 1)
      else expect(q.rb.x).toBeCloseTo(p.rb.x, 1)
      const mid = (s: (typeof samples)[number]) => (axis === 'x' ? s.line.x + s.line.width / 2 : s.line.y + s.line.height / 2)
      const ctr = (s: (typeof samples)[number]) => (axis === 'x' ? s.rb.x + s.rb.width / 2 : s.rb.y + s.rb.height / 2)
      expect(Math.abs(ctr(q) - ctr(p) - (mid(q) - mid(p)))).toBeLessThanOrEqual(1)
    }
    await page.mouse.up()
    await expect(page.locator('[data-ruler-dim]')).toHaveCount(0)
  })
}
