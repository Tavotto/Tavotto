/**
 * STATE 节真浏览器缺陷复现（**预期红**，不进测试集）。
 *
 *   A. STATE-04：松手后权威渲染在途时撤销——文档回到拖动前，画布上的标题却一直停在拖到的位置
 *      （拖动那一版回包之后也不回来）。
 *   B. STATE-02：拖动中按两次 Esc 退出图内编辑态，松手仍把 override 写进文档并发出渲染
 *      （手势比它所属的编辑态活得更长；用户在已经离开的画面里「看不见地」改了图）。
 *
 * 跑法：docs/qa/2026-09-24/state/repro/run.sh e2e-repro state_bugs_repro.spec.ts
 */
import type { Page, Route } from '@playwright/test'
import { expect, test } from './fixtures'

const locate = (page: Page, id: string) =>
  page.evaluate((gid) => {
    const n = document.querySelector(`[data-element-svg] [id="${gid}"]`) as SVGGraphicsElement | null
    if (!n) return null
    const r = n.getBoundingClientRect()
    return { x: r.x + r.width / 2, y: r.y + r.height / 2, tf: n.getAttribute('transform') }
  }, id)

async function openTitle(page: Page, baseURL: string) {
  await page.goto(baseURL)
  await page.getByText('Fig1_kinetics.pdf').dblclick({ timeout: 30_000 })
  await expect(page.locator('[data-element-svg] svg').first()).toBeVisible({ timeout: 60_000 })
  await page.waitForTimeout(2500)
  const t = await locate(page, 'axes_0.title')
  expect(t).not.toBeNull()
  return t!
}

test('A. STATE-04：权威在途时撤销，画布应回到原位', async ({ app, page }) => {
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
  await page.mouse.move(t0.x, t0.y)
  await page.mouse.down()
  for (let i = 1; i <= 20; i++) await page.mouse.move(t0.x + i * 3, t0.y + i * 1.5)
  await page.mouse.up()
  await expect.poll(() => held.length, { timeout: 15_000 }).toBe(1)
  hold = false
  await page.keyboard.press('Control+z')
  await page.waitForTimeout(1000)
  const afterUndo = await locate(page, 'axes_0.title')
  const d1 = Math.hypot(afterUndo!.x - t0.x, afterUndo!.y - t0.y)
  await held[0].continue()
  await page.waitForTimeout(3000)
  const afterLate = await locate(page, 'axes_0.title')
  const d2 = Math.hypot(afterLate!.x - t0.x, afterLate!.y - t0.y)
  console.log(JSON.stringify({ case: 'STATE-04', t0, afterUndo, afterLate, d1, d2 }))
  expect(d1, '撤销后标题仍停在拖到的位置（画布与文档不一致）').toBeLessThan(1)
  expect(d2, '拖动那一版回包之后，撤销掉的位移仍挂在画布上').toBeLessThan(1)
})

test('B. STATE-02：拖动中 Esc×2 退出图内编辑，松手不应再改文档', async ({ app, page }) => {
  const a = await app()
  const renders: string[] = []
  page.on('request', (r) => {
    if (r.url().includes('/api/engine/render')) renders.push(r.postData() ?? '')
  })
  const t0 = await openTitle(page, a.baseURL)
  renders.length = 0
  await page.mouse.move(t0.x, t0.y)
  await page.mouse.down()
  for (let i = 1; i <= 20; i++) await page.mouse.move(t0.x + i * 3, t0.y + i * 1.5)
  await page.keyboard.press('Escape')
  await page.keyboard.press('Escape')
  await expect(page.locator('[data-element-svg] svg')).toHaveCount(0) // 图内编辑态确实退出了
  await page.mouse.move(t0.x + 90, t0.y + 45)
  await page.mouse.up()
  await page.waitForTimeout(3000)
  console.log(JSON.stringify({ case: 'STATE-02-esc', renders: renders.map((r) => r.slice(0, 200)) }))
  expect(renders, '已退出图内编辑态，松手仍写下 override 并渲染').toHaveLength(0)
})
