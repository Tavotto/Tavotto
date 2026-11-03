import { addSecondFigure, expect, test } from './fixtures'
import type { Page } from '@playwright/test'

/**
 * 多图排版里拖页面四条边改画布大小（渐进式画布改版 · 裁决 5）。
 *
 * 只放 jsdom 量不到的：真浏览器里命中区压在纸面边缘上能不能按到、拖动后**屏幕上量到的**纸面与图的位置。
 * 逻辑（吸附 / 夹取 / 事务）在 `canvas/pageEdgeResize.test.tsx`。定位认 `data-page-edge` / `data-page-sheet` /
 * `data-page-dims` / `data-object-id`。
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
  await expect(page.locator('[data-page-dims]')).toHaveCount(1)
  await page.mouse.up()
  await expect(page.locator('[data-page-dims]')).toHaveCount(0)
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

// 机械制图式尺寸标注（画布内）：横向尺寸线在页面上方、竖向在页面左侧，数字居中于尺寸线可见段中点、写在线与页面之间，
// 无边框无底色，不与标尺相交，拖动时只随页面边走
type Box = { x: number; y: number; width: number; height: number }
const overlap = (a: Box, b: Box) =>
  Math.min(a.x + a.width, b.x + b.width) - Math.max(a.x, b.x) > 0 && Math.min(a.y + a.height, b.y + b.height) - Math.max(a.y, b.y) > 0

for (const [edge, axis] of [['r', 'x'], ['l', 'x'], ['b', 'y'], ['t', 'y']] as const) {
  test(`拖${{ r: '右', l: '左', b: '下', t: '上' }[edge]}边：尺寸数字在线中点、页面一侧，无框无底，不压标尺、不抖`, async ({ app, page }) => {
    const a = await app()
    await toLayout(page, a.baseURL)
    const eb = (await page.locator(`[data-page-edge="${edge}"]`).boundingBox())!
    const x = eb.x + eb.width / 2
    const y = eb.y + eb.height / 2
    const sign = edge === 'r' || edge === 'b' ? 1 : -1
    await page.mouse.move(x, y)
    await page.mouse.down()
    type Sample = { num: Box; line: Box; sheet: Box }
    const samples: Record<'width' | 'height', Sample[]> = { width: [], height: [] }
    const stageBox = (await page.locator('[data-canvas-stage]').boundingBox())!
    const rulers = [(await page.locator('[data-ruler="x"]').boundingBox())!, (await page.locator('[data-ruler="y"]').boundingBox())!]
    for (let i = 1; i <= 8; i++) {
      await page.mouse.move(axis === 'x' ? x + sign * i * 9 : x, axis === 'y' ? y + sign * i * 9 : y)
      await expect(page.locator('[data-page-dims]')).toHaveCount(1)
      await page.waitForTimeout(350) // 吸上栏宽时数字会滚（宽度在过渡）：等它落定再量中心
      const sheet = (await page.locator('[data-page-sheet]').first().boundingBox())!
      for (const [kind, dim] of [['width', 'w'], ['height', 'h']] as const) {
        const num = (await page.locator(`[data-dim-readout="${kind}"]`).boundingBox())!
        // 尺寸线几何取 SVG 的 x1/y1/x2/y2（boundingBox 会把两端箭头 marker 的不对称外延算进去）
        const line: Box = await page.locator(`[data-dim="${dim}"] line`).last().evaluate((el) => {
          const r = (el as SVGLineElement).ownerSVGElement!.getBoundingClientRect()
          const n = (k: string) => Number(el.getAttribute(k))
          const [x1, x2, y1, y2] = [n('x1'), n('x2'), n('y1'), n('y2')]
          return { x: r.x + Math.min(x1, x2), y: r.y + Math.min(y1, y2), width: Math.abs(x2 - x1), height: Math.abs(y2 - y1) }
        })
        samples[kind].push({ num, line, sheet })
        // 页面离舞台边够远（尺寸线没被贴边下限 14 px 夹住）时，数字必须落在线与页面之间
        const nearEdge = kind === 'width' ? sheet.y - stageBox.y >= 40 : sheet.x - stageBox.x >= 40
        // 不与标尺相交
        for (const r of rulers) expect(overlap(num, r), `${kind} 数字压到标尺`).toBe(false)
        if (kind === 'width') {
          // 数字中心 = 尺寸线可见段中点；在线下方（线与页面之间），离线 2–4 px
          expect(Math.abs(num.x + num.width / 2 - (line.x + line.width / 2))).toBeLessThanOrEqual(1)
          const gap = num.y - line.y
          expect(gap).toBeGreaterThanOrEqual(2)
          expect(gap).toBeLessThanOrEqual(4)
          if (nearEdge) expect(num.y + num.height).toBeLessThanOrEqual(sheet.y + 0.5)
        } else {
          expect(Math.abs(num.y + num.height / 2 - (line.y + line.height / 2))).toBeLessThanOrEqual(1)
          const gap = num.x - line.x
          expect(gap).toBeGreaterThanOrEqual(2)
          expect(gap).toBeLessThanOrEqual(4)
          if (nearEdge) expect(num.x + num.width).toBeLessThanOrEqual(sheet.x + 0.5)
        }
      }
    }
    // 无边框、无底色
    for (const kind of ['width', 'height']) {
      const st = await page.locator(`[data-dim-readout="${kind}"]`).evaluate((el) => {
        const cs = getComputedStyle(el)
        return { b: [cs.borderTopWidth, cs.borderRightWidth, cs.borderBottomWidth, cs.borderLeftWidth], bg: cs.backgroundColor, num: cs.fontVariantNumeric }
      })
      expect(st.b).toEqual(['0px', '0px', '0px', '0px'])
      expect(st.bg).toBe('rgba(0, 0, 0, 0)')
      expect(st.num).toContain('tabular-nums')
    }
    // 相邻帧无额外跳动：被拖那一维的数字中心位移 = 线段中点位移；另一轴（贴线的那一轴）位置不变
    const dragged = axis === 'x' ? 'width' : 'height'
    const ss = samples[dragged]
    for (let i = 1; i < ss.length; i++) {
      const c = (s: Sample) => (axis === 'x' ? s.num.x + s.num.width / 2 : s.num.y + s.num.height / 2)
      const m = (s: Sample) => (axis === 'x' ? s.line.x + s.line.width / 2 : s.line.y + s.line.height / 2)
      expect(Math.abs(c(ss[i]) - c(ss[i - 1]) - (m(ss[i]) - m(ss[i - 1])))).toBeLessThanOrEqual(1)
      if (axis === 'x') expect(ss[i].num.y).toBeCloseTo(ss[i - 1].num.y, 1)
      else expect(ss[i].num.x).toBeCloseTo(ss[i - 1].num.x, 1)
    }
    await page.mouse.up()
    await expect(page.locator('[data-page-dims]')).toHaveCount(0)
  })
}
