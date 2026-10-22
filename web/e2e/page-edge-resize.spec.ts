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
