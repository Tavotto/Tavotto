/**
 * STATE 节真浏览器**探针**（characterization，不是门禁）：把「合同没写」或「本机不能真做」的
 * 中断入口的实际行为量出来、打进日志，由 QA 台账判读。断言只放在「无论合同怎么定都必须成立」
 * 的地方（例如：松手后 DOM 与后端请求的一致性），其余一律 console.log 出实测值。
 *
 * 跑法：docs/qa/2026-09-24/state/repro/run.sh e2e-repro state_probe.spec.ts [--grep <名>]
 * （脚本会把本文件临时拷进 web/e2e/ 再跑，跑完删除；fixtures 用 web/e2e/fixtures.ts）
 */
import type { Page } from '@playwright/test'
import { expect, test } from './fixtures'

type Target = { id: string; x: number; y: number; transform: string | null }

async function openFigure(page: Page, baseURL: string): Promise<Target> {
  await page.goto(baseURL)
  await page.getByText('Fig1_kinetics.pdf').dblclick({ timeout: 30_000 })
  const svgWrap = page.locator('[data-element-svg]').first()
  await expect(svgWrap.locator('svg')).toBeVisible({ timeout: 60_000 })
  await page.waitForTimeout(1500)
  const t = await page.evaluate(() => {
    const svg = document.querySelector('[data-element-svg] svg')
    if (!svg) return null
    for (const id of ['axes_0.title', 'axes_0.xlabel', 'axes_0.ylabel']) {
      const n = svg.querySelector(`[id="${id}"]`)
      if (n) {
        const r = (n as SVGGraphicsElement).getBoundingClientRect()
        if (r.width > 2 && r.height > 2)
          return { id, x: r.x + r.width / 2, y: r.y + r.height / 2, transform: n.getAttribute('transform') }
      }
    }
    return null
  })
  expect(t).not.toBeNull()
  return t!
}

const readTf = (page: Page, id: string) =>
  page.evaluate((i) => document.querySelector(`[data-element-svg] [id="${i}"]`)?.getAttribute('transform') ?? null, id)
const readCenter = (page: Page, id: string) =>
  page.evaluate((i) => {
    const n = document.querySelector(`[data-element-svg] [id="${i}"]`) as SVGGraphicsElement | null
    if (!n) return null
    const r = n.getBoundingClientRect()
    return { x: r.x + r.width / 2, y: r.y + r.height / 2 }
  }, id)

function counters(page: Page) {
  const renders: { url: string; body: string }[] = []
  const autosaves: { url: string; body: string }[] = []
  page.on('request', (req) => {
    const u = req.url()
    if (u.includes('/api/engine/render')) renders.push({ url: u, body: req.postData() ?? '' })
    if (u.includes('/api/autosave/') && req.method() === 'PUT') autosaves.push({ url: u, body: req.postData() ?? '' })
  })
  return { renders, autosaves }
}

async function dragSteps(page: Page, t: Target, dx: number, dy: number, n = 20) {
  await page.mouse.move(t.x, t.y)
  await page.mouse.down()
  for (let i = 1; i <= n; i++) await page.mouse.move(t.x + (dx * i) / n, t.y + (dy * i) / n)
}

test('probe: Esc 在拖动中', async ({ app, page }) => {
  const a = await app()
  const c = counters(page)
  const t = await openFigure(page, a.baseURL)
  await page.waitForTimeout(1500)
  c.renders.length = 0
  await dragSteps(page, t, 60, 30)
  const beforeEsc = await readTf(page, t.id)
  await page.keyboard.press('Escape')
  await page.waitForTimeout(200)
  const afterEsc = await readTf(page, t.id)
  await page.mouse.move(t.x + 90, t.y + 45)
  await page.waitForTimeout(100)
  const afterMoreMove = await readTf(page, t.id)
  const editSvgStillThere = await page.locator('[data-element-svg] svg').count()
  await page.mouse.up()
  await page.waitForTimeout(3000)
  console.log(JSON.stringify({ probe: 'esc', beforeEsc, afterEsc, afterMoreMove, editSvgStillThere, renders: c.renders.length, renderBodies: c.renders.map((r) => r.body.slice(0, 300)) }))
})

test('probe: Esc 两次（退出图内编辑）在拖动中', async ({ app, page }) => {
  const a = await app()
  const c = counters(page)
  const t = await openFigure(page, a.baseURL)
  await page.waitForTimeout(1500)
  c.renders.length = 0
  await dragSteps(page, t, 60, 30)
  await page.keyboard.press('Escape')
  await page.keyboard.press('Escape')
  await page.waitForTimeout(300)
  const svgCountAfter = await page.locator('[data-element-svg] svg').count()
  await page.mouse.move(t.x + 90, t.y + 45)
  await page.mouse.up()
  await page.waitForTimeout(3000)
  console.log(JSON.stringify({ probe: 'esc2', svgCountAfter, renders: c.renders.length, renderBodies: c.renders.map((r) => r.body.slice(0, 300)) }))
})

test('probe: window blur 在拖动中（合成 blur 事件 + 另开页面抢焦点）', async ({ app, page, context }) => {
  const a = await app()
  const c = counters(page)
  const t = await openFigure(page, a.baseURL)
  await page.waitForTimeout(1500)
  c.renders.length = 0
  await dragSteps(page, t, 60, 30)
  await page.evaluate(() => window.dispatchEvent(new Event('blur')))
  const other = await context.newPage()
  await other.bringToFront()
  await page.bringToFront()
  await page.mouse.move(t.x + 90, t.y + 45)
  const tfAfterBlur = await readTf(page, t.id)
  await page.mouse.up()
  await page.waitForTimeout(3000)
  console.log(JSON.stringify({ probe: 'blur', tfAfterBlur, renders: c.renders.length }))
  await other.close()
})

test('probe: 移出画布 / 移出视口后松开', async ({ app, page }) => {
  const a = await app()
  const c = counters(page)
  const t = await openFigure(page, a.baseURL)
  await page.waitForTimeout(1500)
  c.renders.length = 0
  // (1) 视口内、画布外（左侧栏上方）松开
  await dragSteps(page, t, -t.x + 20, 0)
  const tfAtRelease = await readTf(page, t.id)
  await page.mouse.up()
  await page.waitForTimeout(300)
  const tfAfterUp = await readTf(page, t.id)
  await page.mouse.move(t.x + 200, t.y + 50)
  await page.waitForTimeout(200)
  const tfAfterMove = await readTf(page, t.id)
  await expect.poll(() => c.renders.length, { timeout: 30_000 }).toBeGreaterThanOrEqual(1)
  await page.waitForTimeout(3000)
  const r1 = c.renders.length
  // (2) 视口外松开
  const t2 = await openFigureTarget(page, t.id)
  await dragSteps(page, t2, 0, -t2.y - 40)
  await page.mouse.up()
  await page.waitForTimeout(300)
  await page.mouse.move(t2.x + 30, t2.y + 30)
  await page.waitForTimeout(200)
  const tf2 = await readTf(page, t.id)
  await page.waitForTimeout(3000)
  console.log(JSON.stringify({ probe: 'outside', tfAtRelease, tfAfterUp, tfAfterMove, rendersAfterInViewport: r1, rendersTotal: c.renders.length, tfAfterOutsideViewport: tf2 }))
})

async function openFigureTarget(page: Page, id: string): Promise<Target> {
  const t = await page.evaluate((i) => {
    const n = document.querySelector(`[data-element-svg] [id="${i}"]`) as SVGGraphicsElement | null
    if (!n) return null
    const r = n.getBoundingClientRect()
    return { id: i, x: r.x + r.width / 2, y: r.y + r.height / 2, transform: n.getAttribute('transform') }
  }, id)
  return t!
}

test('probe: 视图倍率 / 窗口宽度在拖动中改变（STATE-07 浏览器腿）', async ({ app, page }) => {
  const a = await app()
  const c = counters(page)
  const t = await openFigure(page, a.baseURL)
  await page.waitForTimeout(1500)
  c.renders.length = 0
  const c0 = await readCenter(page, t.id)
  await dragSteps(page, t, 60, 0, 10)
  const cBeforeZoom = await readCenter(page, t.id)
  await page.keyboard.press('Control+Equal')
  await page.waitForTimeout(400)
  const cAfterZoomNoMove = await readCenter(page, t.id)
  await page.mouse.move(t.x + 61, t.y)
  await page.waitForTimeout(100)
  const cAfterZoomMove = await readCenter(page, t.id)
  await page.mouse.up()
  await expect.poll(() => c.renders.length, { timeout: 30_000 }).toBeGreaterThanOrEqual(1)
  await page.waitForTimeout(3000)
  const cFinal = await readCenter(page, t.id)
  console.log(JSON.stringify({ probe: 'zoom-mid-drag', pointer: { x0: t.x, y0: t.y, dx: 61 }, c0, cBeforeZoom, cAfterZoomNoMove, cAfterZoomMove, cFinal, renders: c.renders.length, body: c.renders[0]?.body.slice(0, 300) }))

  // 窗口宽度：拖动中缩窗
  const t2 = await openFigureTarget(page, t.id)
  const d0 = await readCenter(page, t.id)
  await dragSteps(page, t2, 0, 40, 10)
  const dBefore = await readCenter(page, t.id)
  const vp = page.viewportSize()!
  await page.setViewportSize({ width: Math.round(vp.width * 0.7), height: vp.height })
  await page.waitForTimeout(400)
  const dAfterResize = await readCenter(page, t.id)
  await page.mouse.move(t2.x, t2.y + 41)
  await page.waitForTimeout(100)
  const dAfterResizeMove = await readCenter(page, t.id)
  await page.mouse.up()
  await page.waitForTimeout(3000)
  console.log(JSON.stringify({ probe: 'resize-mid-drag', pointer: { x0: t2.x, y0: t2.y, dy: 41 }, d0, dBefore, dAfterResize, dAfterResizeMove, renders: c.renders.length }))
})
