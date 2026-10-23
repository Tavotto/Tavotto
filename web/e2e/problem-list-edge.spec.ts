/**
 * 问题列表的吸顶组头不许压到右边的画布上（beta 0.19.0b3 反馈）。
 *
 * 组头用 `sticky z-sticky` + 抽屉同色底；抽屉自己若不是层叠上下文，这个 z-index 就落在根层叠上下文里，
 * 压过画布面板（无 z-index）伸进抽屉一侧的投影 / 描边——组头那几行把画布左缘切出缺口（WebKit 与 Chromium 都画得出）。
 * 像素与投影量不稳，这里量判据本身：组头往上数第一个层叠上下文必须是抽屉，几何上也不越过抽屉右缘。
 */
import { expect, test } from './fixtures'

test('问题列表的组头被关在抽屉的层叠上下文里，右缘不越过抽屉', async ({ app, page }) => {
  const a = await app()
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto(a.baseURL)
  await page.getByText('Fig1_kinetics.pdf').dblclick({ timeout: 30_000 })
  await page.locator('[data-element-svg] > svg').waitFor({ timeout: 120_000 })
  const rail = page.locator('[data-rail="problems"]')
  if ((await rail.getAttribute('aria-expanded')) !== 'true') await rail.click()
  const heads = page.locator('[data-issue-group-head]')
  for (let i = 0; i < 6 && !(await heads.count()); i++) {
    await page.locator('li[data-problem-card] > button:first-child').first().click()
    await page.waitForTimeout(300)
  }
  await expect(heads.first()).toBeVisible({ timeout: 60_000 })

  const r = await page.evaluate(() => {
    const drawer = document.querySelector('[data-left-drawer]') as HTMLElement
    const edge = drawer.getBoundingClientRect().right
    const forms = (el: HTMLElement) => {
      const c = getComputedStyle(el)
      return c.isolation === 'isolate' || (c.zIndex !== 'auto' && c.position !== 'static') || c.opacity !== '1'
    }
    return [...document.querySelectorAll<HTMLElement>('[data-issue-group-head]')].map((h) => {
      let up: HTMLElement | null = h.parentElement
      while (up && up !== document.body && !forms(up)) up = up.parentElement
      return { right: h.getBoundingClientRect().right - edge, ctx: up === drawer, who: up?.tagName + '.' + String(up?.className).slice(0, 60) }
    })
  })
  expect(r.length).toBeGreaterThan(0)
  for (const h of r) {
    expect(h.right, '组头右缘 ≤ 抽屉右缘').toBeLessThanOrEqual(0.5)
    expect(h.ctx, '组头的层叠上下文是抽屉本身：' + h.who).toBe(true)
  }
})
