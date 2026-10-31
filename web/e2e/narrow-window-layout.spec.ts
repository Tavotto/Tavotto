/**
 * 窄窗口的工作台排布（2026-10 UI 审计 G1）：Windows 1366×768 @125% 的实际视口约 1093×614。
 * 此前右侧属性栏仍占 360px、左轨 64px，画布只剩一半。现在 < 1280 且没固定属性栏时，
 * 右栏是盖在画布上的覆盖层（不挤占画布），左轨 < 1180 只留图标。
 *
 * 量的是画出来的盒子：画布舞台（`data-canvas-stage`）的宽度占视口的比例；横向溢出只用
 * `e2e/overflow.ts` 那一把尺。
 */
import { expect, selectCanvasFigure, test, waitForFigureEdit } from './fixtures'
import { horizontalOffenders } from './overflow'

test('1093×614：画布占视口 ≥ 60%、无横向溢出、左轨只留图标', async ({ app, page }) => {
  await page.setViewportSize({ width: 1093, height: 614 })
  const a = await app()
  await page.goto(a.baseURL)
  await page.getByText('Fig1_kinetics.pdf').dblclick({ timeout: 30_000 })
  const stage = page.locator('[data-canvas-stage]')
  await expect(stage).toHaveCount(1, { timeout: 60_000 })

  const box = await stage.boundingBox()
  expect(box!.width / 1093, JSON.stringify(box)).toBeGreaterThanOrEqual(0.6)
  expect(await horizontalOffenders(page, '#root')).toEqual([])

  // 左轨：图标还在，短名标签不画（可达名仍是完整名）
  const rail = page.locator('[data-rail="assets"]')
  await expect(rail).toBeVisible()
  expect(await rail.evaluate((el) => (el.textContent ?? '').trim())).toBe('')
})

test('1093 宽：选中对象后属性栏盖在画布上，舞台宽度不变', async ({ app, page }) => {
  await page.setViewportSize({ width: 1093, height: 614 })
  const a = await app()
  await page.goto(a.baseURL)
  await page.getByText('Fig1_kinetics.pdf').dblclick({ timeout: 30_000 })
  await waitForFigureEdit(page)
  const stage = page.locator('[data-canvas-stage]')
  const before = (await stage.boundingBox())!.width
  await selectCanvasFigure(page)
  const inspector = page.locator('[data-inspector-panel]')
  await expect(inspector).toBeVisible()
  expect((await stage.boundingBox())!.width).toBe(before)
  expect(await horizontalOffenders(page, '#root')).toEqual([])
})
