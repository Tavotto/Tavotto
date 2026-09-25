/**
 * STATE-07 浏览器腿探针：拖动中用 CDP 改 deviceScaleFactor（模拟窗口被拖到另一块 DPR 不同的
 * 显示器），以及在 DPR=2 下完整拖一次。**浏览器 DPR 模拟不代替真实跨显示器测试**——本机
 * 没有第二块显示器，真机腿记「未执行」。
 * 跑法：docs/qa/2026-09-24/state/repro/run.sh e2e-repro state07_dpr_probe.spec.ts
 */
import type { Page } from '@playwright/test'
import { expect, test } from './fixtures'

const locate = (page: Page, id: string) =>
  page.evaluate((gid) => {
    const n = document.querySelector(`[data-element-svg] [id="${gid}"]`) as SVGGraphicsElement | null
    if (!n) return null
    const r = n.getBoundingClientRect()
    return { x: r.x + r.width / 2, y: r.y + r.height / 2, tf: n.getAttribute('transform'), dpr: window.devicePixelRatio }
  }, id)

test('probe STATE-07：拖动中 DPR 1→2', async ({ app, page }) => {
  const a = await app()
  const renders: string[] = []
  page.on('request', (r) => {
    if (r.url().includes('/api/engine/render')) renders.push(r.postData() ?? '')
  })
  await page.goto(a.baseURL)
  await page.getByText('Fig1_kinetics.pdf').dblclick({ timeout: 30_000 })
  await expect(page.locator('[data-element-svg] svg').first()).toBeVisible({ timeout: 60_000 })
  await page.waitForTimeout(2500)
  renders.length = 0
  const p0 = (await locate(page, 'axes_0.title'))!
  const vp = page.viewportSize()!
  const cdp = await page.context().newCDPSession(page)
  await page.mouse.move(p0.x, p0.y)
  await page.mouse.down()
  for (let i = 1; i <= 10; i++) await page.mouse.move(p0.x + i * 4, p0.y + i * 2)
  await page.waitForTimeout(100)
  const before = await locate(page, 'axes_0.title')
  await cdp.send('Emulation.setDeviceMetricsOverride', { width: vp.width, height: vp.height, deviceScaleFactor: 2, mobile: false })
  await page.waitForTimeout(400)
  const afterDpr = await locate(page, 'axes_0.title')
  await page.mouse.move(p0.x + 41, p0.y + 20)
  await page.waitForTimeout(100)
  const afterDprMove = await locate(page, 'axes_0.title')
  await page.mouse.up()
  await expect.poll(() => renders.length, { timeout: 30_000 }).toBeGreaterThanOrEqual(1)
  await page.waitForTimeout(2500)
  const final = await locate(page, 'axes_0.title')
  console.log(JSON.stringify({ probe: 'dpr-mid-drag', p0, before, afterDpr, afterDprMove, final, pointerEnd: { x: p0.x + 41, y: p0.y + 20 }, renders: renders.map((r) => r.slice(0, 200)) }))
})
