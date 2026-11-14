import { expect, test, waitForDocumentReady } from './fixtures'

/**
 * 画布左下角的工具提示与底部居中的通知轨同在画布底边。画布一窄（1440 宽窗口、左右栏都开着，画布约 700px），
 * 长一点的工具提示就伸进通知下面被压住（2026-11-14 实测文字工具 300px 的提示与「没有可撤销的操作」这类通知相交）。
 * 修法是结构性的：轨上有通知时提示整块抬到最上面那条通知之上（`StatusBar.hudLift`），没有通知时位置不变。
 *
 * 判据量真实的 boundingRect（jsdom 量不到布局）：两块盒子不相交。
 */
const intersects = (
  a: { x: number; y: number; width: number; height: number },
  b: { x: number; y: number; width: number; height: number },
) => a.x < b.x + b.width && a.x + a.width > b.x && a.y < b.y + b.height && a.y + a.height > b.y

test('窄画布：工具提示与底部通知不相交；通知走了，提示回原位', async ({ app, page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  const a = await app()
  await page.goto(a.baseURL)
  await waitForDocumentReady(page)
  const stage = (await page.locator('[data-canvas-stage]').boundingBox())!
  // 前提：画布确实窄（左右栏都开着）。不窄的话这条用例证明不了任何事
  expect(stage.width).toBeLessThan(800)

  // 没有通知时的位置（基准）
  await page.keyboard.press('t')
  const hud = page.locator('[data-canvas-hud] p')
  await expect(hud).toBeVisible()
  const alone = (await hud.boundingBox())!
  await expect(page.locator('[data-canvas-hud]')).not.toHaveAttribute('data-hud-lifted', /.*/)

  // 来一条通知：没有可撤销的操作时 ⌘Z / Ctrl+Z 会说一句
  await page.keyboard.press('Control+z')
  const toast = page.locator('[data-notification-rail] [data-toast-layout][data-state="open"]').first()
  await expect(toast).toBeVisible()
  await expect(page.locator('[data-canvas-hud]')).toHaveAttribute('data-hud-lifted', 'true')
  const box = (await hud.boundingBox())!
  const note = (await toast.boundingBox())!
  // 这条提示长到会伸进通知的横向范围（否则不相交是白给的）
  expect(box.x + box.width).toBeGreaterThan(note.x)
  expect(intersects(box, note)).toBe(false)
  expect(box.y + box.height).toBeLessThanOrEqual(note.y)
  // 提示没出画布
  expect(box.x).toBeGreaterThanOrEqual(stage.x - 20)
  expect(box.x + box.width).toBeLessThanOrEqual(stage.x + stage.width)

  // 通知走了（info 通知自己会走）：提示回到原位
  await expect(page.locator('[data-canvas-hud]')).not.toHaveAttribute('data-hud-lifted', /.*/, { timeout: 20_000 })
  const back = (await hud.boundingBox())!
  expect(Math.abs(back.y - alone.y)).toBeLessThan(1)
})
