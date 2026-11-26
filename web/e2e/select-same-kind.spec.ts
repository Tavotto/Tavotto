/**
 * 图内元素右键「选中全部同类」（来信 TVM-CKRH）：右键 x 轴标题 → 选中全部同类 → 两条轴标题一起选中、
 * 属性页给出公共文字样式 → 改一次字号，两条都变大 → 撤销一次，两条一起回去。
 */
import { expect, test, waitForFigureEdit } from './fixtures'
import type { Page } from '@playwright/test'

const box = (page: Page, gid: string) =>
  page.locator(`[data-element-svg] [id="${gid}"]`).evaluate((node) => {
    const r = node.getBoundingClientRect()
    return { x: r.x, y: r.y, width: r.width, height: r.height }
  })

test('右键轴标题选中全部同类，改一次字号两条都变，撤销一次都回去', async ({ app, page }) => {
  test.setTimeout(240_000)
  const a = await app()
  await page.setViewportSize({ width: 1500, height: 950 })
  await page.goto(a.baseURL)
  await page.locator('[data-card="Fig1_kinetics.pdf"]').dblclick({ timeout: 30_000 })
  await waitForFigureEdit(page)

  const x0 = await box(page, 'axes_0.xlabel')
  const y0 = await box(page, 'axes_0.ylabel')
  await page.mouse.click(x0.x + x0.width / 2, x0.y + x0.height / 2, { button: 'right' })
  const item = page.locator('[data-quick-item="select-same-kind"]')
  await expect(item).toContainText('2')
  await item.click()

  const size = page.locator('[data-inspector-panel] [data-prop="fontsize"] input')
  await expect(size).toBeVisible()
  await size.fill('16')
  await size.press('Enter')
  await expect.poll(async () => (await box(page, 'axes_0.xlabel')).height, { timeout: 90_000 }).toBeGreaterThan(x0.height * 1.2)
  await expect.poll(async () => (await box(page, 'axes_0.ylabel')).width, { timeout: 90_000 }).toBeGreaterThan(y0.width * 1.2)

  await page.locator('[data-canvas-stage]').click({ position: { x: 5, y: 5 } })
  await page.keyboard.press('ControlOrMeta+z')
  await expect.poll(async () => (await box(page, 'axes_0.xlabel')).height, { timeout: 90_000 }).toBeLessThan(x0.height * 1.1)
  await expect.poll(async () => (await box(page, 'axes_0.ylabel')).width, { timeout: 90_000 }).toBeLessThan(y0.width * 1.1)
})
