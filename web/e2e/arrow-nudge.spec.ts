import type { Page } from '@playwright/test'
import { expect, test } from './fixtures'

/**
 * 方向键微调的浏览器腿（ADR 0093）：**真 PanelView、真键盘事件、真后端渲染**。
 *
 * 起点（2026-09-26 实测，改动前的 main）：画布排版里进图内编辑、选中图例按方向键，挪的是
 * **整张图**，图例纹丝不动；快速编辑里按方向键什么都不发生。
 *
 * 独立尺：同一视图倍率下，先用方向键推**画布上的面板**（画布对象的坐标就是页面 mm，
 * 与图内元素无关的另一条路径）量出「一步 = 多少屏幕像素」，再推图内图例，**权威重渲染之后**
 * 图例墨迹框的位移必须等于同样步数 × 那个像素数。图例从 matplotlib 自动摆放换成显式位置那一下
 * 可能有与平台字形度量相关的跳动（#576），先热身一段不计入。
 *
 * 主语：`[data-element-svg] [id="axes_0.legend"]`（matplotlib 输出的 gid，稳定锚点）与
 * `[data-object-id]`（画布对象）；时刻：按键之前 vs 这一段收尾、权威 SVG 换上画布之后。
 */

const LEGEND = 'axes_0.legend'
const BUDGET_PX = 0.5
const N = 6

type Box = { x: number; y: number; w: number; h: number }

const boxOf = (page: Page, gid: string): Promise<Box | null> =>
  page.evaluate((id) => {
    const hosts = document.querySelectorAll('[data-element-svg]')
    if (hosts.length !== 1) throw new Error(`图内编辑宿主应恰有一个，实际 ${hosts.length}`)
    const n = hosts[0].querySelector(`[id="${id}"]`)
    if (!n) return null
    const r = (n as SVGGraphicsElement).getBoundingClientRect()
    return { x: r.x, y: r.y, w: r.width, h: r.height }
  }, gid)

/** 图例在它那张图里的位置（按图宽 / 图高归一）：视口换了（重开后重新适配）也能比 */
const relOf = (page: Page, gid: string) =>
  page.evaluate((id) => {
    const host = document.querySelector('[data-element-svg] > svg')!
    const n = host.querySelector(`[id="${id}"]`)!
    const h = host.getBoundingClientRect()
    const r = (n as SVGGraphicsElement).getBoundingClientRect()
    return [(r.x - h.x) / h.width, (r.y - h.y) / h.height, r.width / h.width, r.height / h.height]
  }, gid)

const panelBox = (page: Page): Promise<Box> =>
  page.evaluate(() => {
    const all = document.querySelectorAll('[data-object-id]')
    if (all.length !== 1) throw new Error(`画布上应恰有一个对象，实际 ${all.length}`)
    const r = all[0].getBoundingClientRect()
    return { x: r.x, y: r.y, w: r.width, h: r.height }
  })

const timingsCount = (page: Page) =>
  page.evaluate(() => {
    const w = window as unknown as { __MM_PREVIEW_TIMINGS__?: unknown[] }
    return w.__MM_PREVIEW_TIMINGS__?.length ?? 0
  })

/** 按 n 下方向键（点按），然后等这一段收尾、权威 SVG 换上画布 */
async function nudgeAndSettle(page: Page, key: string, n: number) {
  const before = await timingsCount(page)
  for (let i = 0; i < n; i++) await page.keyboard.press(key)
  await expect
    .poll(() => timingsCount(page), { timeout: 60_000, message: '这一段收尾后权威 SVG 应当换上画布' })
    .toBeGreaterThan(before)
  await page.waitForTimeout(300)
}

async function addFigureToCanvas(page: Page, baseURL: string) {
  await page.goto(baseURL)
  // 素材卡认稳定锚点 data-card；Shift+Enter = 添加到画布
  const card = page.locator('[data-card="Fig1_kinetics.pdf"]')
  await card.focus({ timeout: 30_000 })
  await page.keyboard.press('Shift+Enter')
  await expect(page.locator('[data-object-id]')).toHaveCount(1, { timeout: 30_000 })
  await page.waitForTimeout(1500)
}

async function enterFigure(page: Page) {
  const p = await panelBox(page)
  await page.mouse.click(p.x + p.w / 2, p.y + p.h / 2)
  await page.keyboard.press('Enter')
  await expect(page.locator('[data-element-svg] > svg')).toBeVisible({ timeout: 60_000 })
  await page.waitForTimeout(1500)
}

async function selectLegend(page: Page) {
  const b = (await boxOf(page, LEGEND))!
  await page.mouse.click(b.x + b.w / 2, b.y + b.h / 2)
  await page.waitForTimeout(300)
}

test('画布里选中图内图例按 → N 次：图例挪 N 步、面板不动；撤销一次回原位；保存重开位置一致', async ({
  app,
  page,
}) => {
  const a = await app()
  const renders: string[] = []
  page.on('request', (r) => {
    if (r.url().includes('/api/engine/render')) renders.push(r.url())
  })
  await addFigureToCanvas(page, a.baseURL)

  // 尺子：画布对象的一步（页面 0.5 mm）在屏幕上是多少像素。之后不改视图倍率
  const p0 = await panelBox(page)
  await page.mouse.click(p0.x + p0.w / 2, p0.y + p0.h / 2)
  for (let i = 0; i < N; i++) await page.keyboard.press('ArrowRight')
  await page.waitForTimeout(600)
  const p1 = await panelBox(page)
  const stepPx = (p1.x - p0.x) / N
  console.log(`[arrow-nudge e2e] 画布对象一步 = ${stepPx.toFixed(3)} px；面板宽 ${((p0.w / stepPx) * 0.5).toFixed(2)} mm`)
  expect(stepPx, '画布对象一步应当大于 0（尺子是活的）').toBeGreaterThan(0.5)
  expect(Math.abs(p1.y - p0.y)).toBeLessThan(0.01)

  await enterFigure(page)
  await selectLegend(page)
  // 热身一段（不计入）：图例从自动摆放换成显式 loc_frac 那一下可能有平台相关的跳动（#576）
  await nudgeAndSettle(page, 'ArrowUp', 1)
  const panelBefore = await panelBox(page)
  const l0 = (await boxOf(page, LEGEND))!
  const rel0 = await relOf(page, LEGEND)

  // 这一段里零渲染：渲染请求只在收尾时发
  const rendersBefore = renders.length
  for (let i = 0; i < N; i++) await page.keyboard.press('ArrowRight')
  const during = renders.length - rendersBefore
  await expect
    .poll(async () => (await boxOf(page, LEGEND))!.x - l0.x, { timeout: 60_000 })
    .toBeGreaterThan(stepPx * N * 0.5)
  await page.waitForTimeout(1500)
  const l1 = (await boxOf(page, LEGEND))!
  const panelAfter = await panelBox(page)
  const err = [l1.x - l0.x - N * stepPx, l1.y - l0.y]
  console.log(
    `[arrow-nudge e2e] 图例 Δ=(${(l1.x - l0.x).toFixed(3)}, ${(l1.y - l0.y).toFixed(3)}) 期望 ${(N * stepPx).toFixed(3)} ` +
      `误差=(${err.map((v) => v.toFixed(3))}) 按键期间渲染请求 ${during} 收尾后 ${renders.length - rendersBefore}`,
  )
  expect(during, '按键期间不应发渲染请求').toBe(0)
  expect(renders.length - rendersBefore, '这一段收尾只发一次渲染').toBe(1)
  expect(Math.abs(err[0])).toBeLessThanOrEqual(BUDGET_PX)
  expect(Math.abs(err[1])).toBeLessThanOrEqual(BUDGET_PX)
  expect(panelAfter.x, '图内微调不能推整张图').toBeCloseTo(panelBefore.x, 2)
  expect(panelAfter.y).toBeCloseTo(panelBefore.y, 2)
  const hot = await relOf(page, LEGEND)

  // 撤销一次：这一段 N 下整个回去
  await page.keyboard.press('Control+z')
  await expect
    .poll(async () => Math.abs((await boxOf(page, LEGEND))!.x - l0.x), {
      timeout: 60_000,
      message: '撤销一次应当回到这一段之前',
    })
    .toBeLessThanOrEqual(BUDGET_PX)
  expect(Math.abs((await boxOf(page, LEGEND))!.y - l0.y)).toBeLessThanOrEqual(BUDGET_PX)
  expect((await relOf(page, LEGEND))[0]).toBeCloseTo(rel0[0], 3)

  // 重做回到热态，保存、重开：重放出来的位置 == 热态
  await page.keyboard.press('Control+Shift+z')
  await expect
    .poll(async () => Math.abs((await relOf(page, LEGEND))[0] - hot[0]), { timeout: 60_000 })
    .toBeLessThan(1e-3)
  await page.keyboard.press('Escape')
  await page.keyboard.press('Escape')
  await page.keyboard.press('Control+s')
  await page.waitForTimeout(1500)
  await page.reload()
  await expect(page.locator('[data-object-id]')).toHaveCount(1, { timeout: 30_000 })
  await page.waitForTimeout(1500)
  await enterFigure(page)
  const replay = await relOf(page, LEGEND)
  const w = (await page.locator('[data-element-svg] > svg').boundingBox())!.width
  console.log(
    `[arrow-nudge e2e] 热态 vs 重开 Δ=(${((replay[0] - hot[0]) * w).toFixed(3)}, ${((replay[1] - hot[1]) * w).toFixed(3)}) px`,
  )
  expect(Math.abs(replay[0] - hot[0]) * w).toBeLessThanOrEqual(BUDGET_PX)
  expect(Math.abs(replay[1] - hot[1]) * w).toBeLessThanOrEqual(BUDGET_PX)
})

test('快速编辑里选中图例按方向键：图例动（以前纹丝不动），一段一条撤销', async ({ app, page }) => {
  const a = await app()
  await page.goto(a.baseURL)
  await page.locator('[data-card="Fig1_kinetics.pdf"]').dblclick({ timeout: 30_000 })
  await expect(page.locator('[data-element-svg] > svg')).toBeVisible({ timeout: 60_000 })
  await page.waitForTimeout(2500)
  await selectLegend(page)
  const l0 = (await boxOf(page, LEGEND))!
  await nudgeAndSettle(page, 'ArrowDown', 4)
  const l1 = (await boxOf(page, LEGEND))!
  console.log(`[arrow-nudge e2e] 快速编辑 图例 Δ=(${(l1.x - l0.x).toFixed(3)}, ${(l1.y - l0.y).toFixed(3)})`)
  expect(l1.y - l0.y, '↓ 应当把图例往下挪').toBeGreaterThan(1)
  expect(Math.abs(l1.x - l0.x)).toBeLessThanOrEqual(BUDGET_PX)

  await page.keyboard.press('Control+z')
  await expect
    .poll(async () => Math.abs((await boxOf(page, LEGEND))!.y - l0.y), { timeout: 60_000 })
    .toBeLessThanOrEqual(BUDGET_PX)
})
