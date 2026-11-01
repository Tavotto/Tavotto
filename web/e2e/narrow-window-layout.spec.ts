/**
 * 窄窗口的工作台排布（2026-10 UI 审计 G1）：Windows 1366×768 @125% 的实际视口约 1093×614。
 * 此前右侧属性栏仍占 360px、左轨 64px，画布只剩一半。现在 < 1280 且没固定属性栏时，
 * 右栏是盖在画布上的覆盖层（不挤占画布），左轨 < 1180 只留图标。
 *
 * 量的是画出来的盒子：画布舞台（`data-canvas-stage`）的宽度占视口的比例；横向溢出只用
 * `e2e/overflow.ts` 那一把尺。
 */
import { expect, selectCanvasFigure, test, waitForDocumentReady } from './fixtures'
import { horizontalOffenders } from './overflow'

/**
 * 尺子认顶栏 / 左轨 / 左抽屉 / 属性栏这几块外壳（画布内部的绝对定位世界层本来就是 0 宽容器 + 溢出内容，
 * 不是布局撑破），再加页面本身不出现横向滚动。
 */
async function expectNoHorizontalOverflow(page: import('@playwright/test').Page) {
  for (const root of ['[data-topbar]', 'nav', '[data-left-drawer]', '[data-inspector-panel]']) {
    if (await page.locator(root).count()) expect(await horizontalOffenders(page, root), root).toEqual([])
  }
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
}

test('1093×614：画布占视口 ≥ 60%、无横向溢出、左轨只留图标', async ({ app, page }) => {
  await page.setViewportSize({ width: 1093, height: 614 })
  const a = await app()
  await page.goto(a.baseURL)
  await page.locator('[data-asset-list]').getByText('Fig1_kinetics.pdf').dblclick({ timeout: 30_000 })
  const stage = page.locator('[data-canvas-stage]')
  await expect(stage).toHaveCount(1, { timeout: 60_000 })

  const box = await stage.boundingBox()
  expect(box!.width / 1093, JSON.stringify(box)).toBeGreaterThanOrEqual(0.6)
  await expectNoHorizontalOverflow(page)

  // 左轨：图标还在，短名标签不画（可达名仍是完整名）
  const rail = page.locator('[data-rail="assets"]')
  await expect(rail).toBeVisible()
  expect(await rail.evaluate((el) => (el.textContent ?? '').trim())).toBe('')
})

test('1093 宽：选中对象后属性栏盖在画布上、不挤窄舞台', async ({ app, page }) => {
  await page.setViewportSize({ width: 1093, height: 614 })
  const a = await app()
  await page.goto(a.baseURL)
  await page.locator('[data-asset-list]').getByText('Fig1_kinetics.pdf').dblclick({ timeout: 30_000 })
  await waitForDocumentReady(page)
  const stage = page.locator('[data-canvas-stage]')
  const before = (await stage.boundingBox())!.width
  await selectCanvasFigure(page)
  const inspector = page.locator('[data-inspector-panel]')
  await expect(inspector).toBeVisible()
  // 选中后素材抽屉按产品规则让位，舞台只会变宽；属性栏盖在舞台上（与舞台相交），没有把舞台挤窄
  const after = (await stage.boundingBox())!
  const ins = (await inspector.boundingBox())!
  expect(after.width).toBeGreaterThanOrEqual(before)
  expect(ins.x, JSON.stringify({ ins, after })).toBeLessThan(after.x + after.width)
  await expectNoHorizontalOverflow(page)
})
