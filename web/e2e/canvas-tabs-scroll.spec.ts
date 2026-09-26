import { expect, test } from './fixtures'
import type { Locator } from '@playwright/test'

/**
 * 画布页签条：只许横滚，不许纵向溢出。
 *
 * 页签条是 `overflow-x-auto`；CSS 规定 overflow-x 不是 visible 时 overflow-y 被算成 auto，
 * 于是里面任何一个子元素纵向多出一点，条就多出一段纵向滚动。此前条的内容盒 35px（h-9 减
 * border-b），页签却写死 h-9 = 36px，多出 1px——桌面版（WKWebView）在「Figure 1」与「+」
 * 之间常驻一根灰色竖滚动条，没有任何用处（用户截图，2026-09-26）。
 *
 * 几何只有真布局量得出，jsdom 恒真，所以这里用真浏览器；webkit 腿是用户那台引擎。
 * 两个时刻都量：一个页签（用户截图那一幕），以及页签多到出现横滚条之后——横滚条自己
 * 占了高度，内容若不跟着缩，纵向溢出会在这一刻回来。
 */

async function verticalOverflow(strip: Locator) {
  return strip.evaluate((el) => ({
    scrollHeight: el.scrollHeight,
    clientHeight: el.clientHeight,
    overflowY: getComputedStyle(el).overflowY,
  }))
}

test('画布页签条没有纵向溢出，页签多了仍能横滚', async ({ app, page }) => {
  // 窄一点的窗口：十几个默认名页签就放不下，横滚才真的发生
  await page.setViewportSize({ width: 900, height: 700 })
  const a = await app()
  await page.goto(a.baseURL)
  await page.locator('[data-canvas-stage]').waitFor({ timeout: 60_000 })

  const strip = page.locator('[data-canvas-tabs]')
  await expect(strip).toHaveCount(1)
  await expect(strip.getByRole('tab')).toHaveCount(1)

  // 一个页签：用户截图那一幕
  const one = await verticalOverflow(strip)
  expect(one.scrollHeight, JSON.stringify(one)).toBeLessThanOrEqual(one.clientHeight)

  const newCanvas = page.getByRole('button', { name: '新建画布', exact: true }).first()
  for (let i = 0; i < 14; i++) await newCanvas.click()
  await expect(strip.getByRole('tab')).toHaveCount(15)

  // 横向：放不下时仍然能滚——scrollWidth 超出，scrollLeft 真的改得动。
  // 光看 scrollLeft 不够：overflow-x: hidden 的盒子脚本照样能改 scrollLeft，用户却滚不动，
  // 所以一并钉住 overflowX 是可滚的那两档
  const h = await strip.evaluate((el) => {
    const before = el.scrollLeft
    el.scrollLeft = 0
    const atStart = el.scrollLeft
    el.scrollLeft = el.scrollWidth
    const overflowX = getComputedStyle(el).overflowX
    return { sw: el.scrollWidth, cw: el.clientWidth, overflowX, before, atStart, atEnd: el.scrollLeft }
  })
  expect(['auto', 'scroll'], JSON.stringify(h)).toContain(h.overflowX)
  expect(h.sw, JSON.stringify(h)).toBeGreaterThan(h.cw)
  expect(h.atStart, JSON.stringify(h)).toBe(0)
  expect(h.atEnd, JSON.stringify(h)).toBeGreaterThan(0)

  // 横滚条出现之后，纵向照样不许溢出
  const many = await verticalOverflow(strip)
  expect(many.scrollHeight, JSON.stringify(many)).toBeLessThanOrEqual(many.clientHeight)
})
