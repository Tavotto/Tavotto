/**
 * STATE-04 / STATE-05 真浏览器探针：用 Playwright 路由把 `/api/engine/render` 扣住（F1 可控慢渲染 /
 * 乱序回包），量「权威在途时撤销」与「旧回包晚于新回包」两件事在真实画布上的样子。
 *
 * 跑法：docs/qa/2026-09-24/state/repro/run.sh e2e-repro state_slow_authority_probe.spec.ts
 */
import type { Page, Route } from '@playwright/test'
import { expect, test } from './fixtures'

type Target = { id: string; x: number; y: number; transform: string | null }

async function openFigure(page: Page, baseURL: string): Promise<Target> {
  await page.goto(baseURL)
  await page.getByText('Fig1_kinetics.pdf').dblclick({ timeout: 30_000 })
  await expect(page.locator('[data-element-svg] svg').first()).toBeVisible({ timeout: 60_000 })
  await page.waitForTimeout(1500)
  const t = await locate(page, 'axes_0.title')
  expect(t).not.toBeNull()
  return t!
}
const locate = (page: Page, id: string) =>
  page.evaluate((i) => {
    const n = document.querySelector(`[data-element-svg] [id="${i}"]`) as SVGGraphicsElement | null
    if (!n) return null
    const r = n.getBoundingClientRect()
    return { id: i, x: r.x + r.width / 2, y: r.y + r.height / 2, transform: n.getAttribute('transform') }
  }, id)

async function drag(page: Page, t: Target, dx: number, dy: number) {
  await page.mouse.move(t.x, t.y)
  await page.mouse.down()
  for (let i = 1; i <= 20; i++) await page.mouse.move(t.x + (dx * i) / 20, t.y + (dy * i) / 20)
  await page.mouse.up()
}

/** 扣住渲染请求：armed 之后的每个请求都进 held，等测试手动放行 */
function holdRenders(page: Page) {
  const held: { route: Route; body: string }[] = []
  const passed: string[] = []
  let armed = false
  const holdNext = { n: 0 }
  void page.route('**/api/engine/render**', async (route) => {
    const body = route.request().postData() ?? ''
    if (armed && holdNext.n > 0) {
      holdNext.n--
      held.push({ route, body })
      return
    }
    passed.push(body)
    await route.continue()
  })
  return {
    held,
    passed,
    arm: (n: number) => {
      armed = true
      holdNext.n = n
    },
    release: async (i: number) => {
      await held[i].route.continue()
    },
  }
}

test('probe STATE-04：权威在途时撤销，画布是否回到原位', async ({ app, page }) => {
  const a = await app()
  const autosaves: string[] = []
  page.on('request', (r) => {
    if (r.url().includes('/api/autosave/') && r.method() === 'PUT') autosaves.push(r.postData() ?? '')
  })
  const h = holdRenders(page)
  const t0 = await openFigure(page, a.baseURL)
  h.arm(1)
  await drag(page, t0, 60, 30)
  await expect.poll(() => h.held.length, { timeout: 15_000 }).toBe(1)
  const afterRelease = await locate(page, t0.id)
  await page.keyboard.press('Control+z')
  await page.waitForTimeout(800)
  const afterUndo = await locate(page, t0.id)
  await page.waitForTimeout(1500) // 自动保存 1s 防抖
  const lastSave = autosaves.at(-1) ?? ''
  const savedHasPos = /"pos_frac"/.test(lastSave)
  await h.release(0)
  await page.waitForTimeout(3000)
  const afterLate = await locate(page, t0.id)
  console.log(JSON.stringify({ probe: 'undo-while-pending', t0, afterRelease, afterUndo, afterLate, heldBody: h.held[0].body.slice(0, 200), autosaveCount: autosaves.length, lastSaveHasPosFrac: savedHasPos }))
})

test('probe STATE-05：旧回包晚于新回包', async ({ app, page }) => {
  const a = await app()
  const h = holdRenders(page)
  const t0 = await openFigure(page, a.baseURL)
  h.arm(1)
  await drag(page, t0, 60, 0) // V1：扣住
  await expect.poll(() => h.held.length, { timeout: 15_000 }).toBe(1)
  await page.keyboard.press('Control+z') // 回到 V0（缓存里的精确变体）
  await page.waitForTimeout(800)
  const beforeSecond = await locate(page, t0.id)
  // V2：从当前可见位置起拖（若画布残留 V1 的位移，这里起点就不是原位）
  await drag(page, { ...t0, x: beforeSecond!.x, y: beforeSecond!.y }, 0, 40)
  await expect.poll(() => h.passed.length, { timeout: 30_000 }).toBeGreaterThanOrEqual(1)
  await page.waitForTimeout(2500)
  const afterV2 = await locate(page, t0.id)
  await h.release(0) // V1 姗姗来迟
  await page.waitForTimeout(3000)
  const afterLateV1 = await locate(page, t0.id)
  console.log(JSON.stringify({ probe: 'out-of-order', t0, beforeSecond, afterV2, afterLateV1, v1: h.held[0].body.slice(0, 200), passed: h.passed.map((b) => b.slice(0, 200)) }))
})
