import type { Page, Route } from '@playwright/test'
import { expect, test } from './fixtures'

/**
 * 图内拖动手势的生命周期，**真浏览器几何**（QA 2026-09-24 STATE 节，#583）。
 * 纯状态与事务的一半在 `src/canvas/dragGestureLifecycle.test.tsx`（jsdom 量不到元素在屏幕上的位置）。
 *
 *   STATE-04-B1  权威渲染在途时撤销：标题回到原位；拖动那一版晚到也不把它带回来。
 *   STATE-02-B1  拖动中 Esc：先取消手势（元素回原位），再退出图内编辑态、松手，0 次渲染。
 *   STATE-07-B1  拖动中 ⌘= 改视图倍率：下一次 move 不跳位，之后逐帧跟着指针的增量走。
 */

type Probe = { x: number; y: number; tf: string | null }
type Opened = Probe & { pid: string }

const TITLE = 'axes_0.title'

/**
 * 量**打开的那块面板**里的标题。gid（`axes_0.title`）在每块面板的 SVG 里都一样，
 * 全局 `querySelector` 取第一个匹配会量到别的面板（#591 评审）：一律按面板 id
 * （`data-element-svg` 的值）限定范围。
 */
const locate = (page: Page, pid: string): Promise<Probe | null> =>
  page.evaluate(
    ([panel, gid]) => {
      const n = document.querySelector(
        `[data-element-svg="${panel}"] [id="${gid}"]`,
      ) as SVGGraphicsElement | null
      if (!n) return null
      const r = n.getBoundingClientRect()
      return { x: r.x + r.width / 2, y: r.y + r.height / 2, tf: n.getAttribute('transform') }
    },
    [pid, TITLE] as const,
  )

/** 预览按 rAF 合并落 DOM：量之前等两帧，免得读到上一帧 */
const frames = (page: Page) =>
  page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))))

const dist = (a: Probe, b: Probe) => Math.hypot(a.x - b.x, a.y - b.y)

async function openTitle(page: Page, baseURL: string): Promise<Opened> {
  await page.goto(baseURL)
  await page.getByText('Fig1_kinetics.pdf').dblclick({ timeout: 30_000 })
  // 图内编辑态只给正在编辑的那块面板挂内联 SVG：必须恰好一块，它的 id 就是被测面板
  const editing = page.locator('[data-element-svg]')
  await expect(editing.locator('svg')).toHaveCount(1, { timeout: 60_000 })
  await expect(editing).toHaveCount(1)
  const pid = await editing.getAttribute('data-element-svg')
  expect(pid, '编辑中的面板应当带 data-element-svg').toBeTruthy()
  // 等首次渲染安顿下来（与 fake-realtime.spec 同一取舍）
  await page.waitForTimeout(2500)
  const t = await locate(page, pid!)
  expect(t, '图里应当有标题').not.toBeNull()
  return { ...t!, pid: pid! }
}

async function dragFrom(page: Page, t: Probe, dx: number, dy: number, n = 20) {
  await page.mouse.move(t.x, t.y)
  await page.mouse.down()
  for (let i = 1; i <= n; i++) await page.mouse.move(t.x + (dx * i) / n, t.y + (dy * i) / n)
}

test('STATE-04：权威渲染在途时撤销，画布回到原位，拖动那一版晚到也不带回来', async ({ app, page }) => {
  const a = await app()
  const held: Route[] = []
  let hold = false
  await page.route('**/api/engine/render**', async (route) => {
    if (hold) {
      held.push(route)
      return
    }
    await route.continue()
  })
  const t0 = await openTitle(page, a.baseURL)
  hold = true
  await dragFrom(page, t0, 60, 30)
  await page.mouse.up()
  await frames(page)
  await expect.poll(() => held.length, { timeout: 15_000, message: '松手应当发出定稿渲染' }).toBe(1)
  const afterUp = await locate(page, t0.pid)
  expect(dist(afterUp!, t0), '松手后等权威：预览应当停在拖到的位置').toBeGreaterThan(30)
  hold = false

  await page.keyboard.press('Control+z')
  await expect
    .poll(async () => dist((await locate(page, t0.pid))!, t0), {
      timeout: 5_000,
      message: '撤销后标题仍停在拖到的位置（画布与文档不一致）',
    })
    .toBeLessThan(1)

  await held[0].continue()
  // 晚到的回包只入库：给它足够时间落地，再量一次
  await page.waitForTimeout(3000)
  const afterLate = await locate(page, t0.pid)
  expect(dist(afterLate!, t0), '拖动那一版回包之后，撤销掉的位移又回到了画布上').toBeLessThan(1)
})

test('STATE-02：拖动中 Esc 先取消手势，退出图内编辑态后松手不写文档', async ({ app, page }) => {
  const a = await app()
  const renders: string[] = []
  page.on('request', (r) => {
    if (r.url().includes('/api/engine/render')) renders.push(r.postData() ?? '')
  })
  const t0 = await openTitle(page, a.baseURL)
  renders.length = 0
  await dragFrom(page, t0, 60, 30)
  await frames(page)
  const during = await locate(page, t0.pid)
  expect(dist(during!, t0), '拖动中标题应当跟着手').toBeGreaterThan(30)

  await page.keyboard.press('Escape')
  const afterEsc = await locate(page, t0.pid)
  expect(afterEsc!.tf, 'Esc 之后预览位移应当还原').toBe(t0.tf)
  expect(dist(afterEsc!, t0)).toBeLessThan(1)
  // 指针还按着：继续移动不复活手势
  await page.mouse.move(t0.x + 80, t0.y + 40)
  await frames(page)
  expect(dist((await locate(page, t0.pid))!, t0), '取消后继续移动，手势复活了').toBeLessThan(1)

  // 再按：清选中 → 退出图内编辑态
  await page.keyboard.press('Escape')
  await page.keyboard.press('Escape')
  await expect(page.locator(`[data-element-svg="${t0.pid}"] svg`)).toHaveCount(0)
  await page.mouse.move(t0.x + 90, t0.y + 45)
  await page.mouse.up()
  await page.waitForTimeout(3000)
  expect(renders, '取消过的手势在松手时写下 override 并渲染').toHaveLength(0)
})

test('STATE-07：拖动中改视图倍率，下一次 move 不跳位、之后跟着指针走', async ({ app, page }) => {
  const a = await app()
  const renders: string[] = []
  page.on('request', (r) => {
    if (r.url().includes('/api/engine/render')) renders.push(r.url())
  })
  const t0 = await openTitle(page, a.baseURL)
  renders.length = 0
  await dragFrom(page, t0, 60, 0, 10)
  await page.keyboard.press('Control+Equal')
  // 倍率变化：元素随画布一起缩放（内容位置不变），量它此刻在哪
  await page.waitForTimeout(400)
  const afterZoom = await locate(page, t0.pid)
  expect(dist(afterZoom!, t0), '⌘= 没有生效（画面没变），这条量不到东西').toBeGreaterThan(1)

  await page.mouse.move(t0.x + 61, t0.y)
  await frames(page)
  const next = await locate(page, t0.pid)
  // 没重建基准时这里跳 ≈ 60 × (1.25 − 1) = 15px（QA 实测 14px）
  expect(dist(next!, afterZoom!), '倍率变化后的下一次 move 跳位了').toBeLessThan(2)

  await page.mouse.move(t0.x + 81, t0.y + 10)
  await frames(page)
  const later = await locate(page, t0.pid)
  expect(later!.x - next!.x, '倍率变化后元素应当跟着指针的增量走（横）').toBeCloseTo(20, 0)
  expect(later!.y - next!.y, '倍率变化后元素应当跟着指针的增量走（纵）').toBeCloseTo(10, 0)

  await page.mouse.up()
  await expect.poll(() => renders.length, { timeout: 30_000 }).toBeGreaterThanOrEqual(1)
  await page.waitForTimeout(3000)
  const final = await locate(page, t0.pid)
  expect(dist(final!, later!), '权威渲染上屏后弹离了最后一帧预览的位置').toBeLessThan(2)
})
