import { expect, test } from './fixtures'
import type { Locator } from '@playwright/test'

/**
 * 画布页签条：只许横滚，不许纵向溢出，也不画滚动条。
 *
 * 页签条是 `overflow-x-auto`；CSS 规定 overflow-x 不是 visible 时 overflow-y 被算成 auto，
 * 于是里面任何一个子元素纵向多出一点，条就多出一段纵向滚动。此前条的内容盒 35px（h-9 减
 * border-b），页签却写死 h-9 = 36px，多出 1px——桌面版（WKWebView）在「Figure 1」与「+」
 * 之间常驻一根灰色竖滚动条，没有任何用处（用户截图，2026-09-26）。
 *
 * 横滚条也不画（`scrollbar-none`，2026-09-27 用户拍板）：全局的 10px 自定义滚动条在 WebKit
 * 里占掉条内 10px、页签被压到 25px。不画之后滚动靠触控板横滑 / Shift+滚轮，以及激活时
 * 自动滚进视野——这几件都要在这里量到。
 *
 * 几何只有真布局量得出，jsdom 恒真，所以这里用真浏览器；webkit 腿是用户那台引擎。
 */

async function verticalOverflow(strip: Locator) {
  return strip.evaluate((el) => ({
    scrollHeight: el.scrollHeight,
    clientHeight: el.clientHeight,
    overflowY: getComputedStyle(el).overflowY,
  }))
}

/** 激活的页签整颗落在条的可视范围里 */
async function activeTabInView(strip: Locator) {
  return strip.evaluate((el) => {
    const tab = el.querySelector('[role="tab"][aria-selected="true"]') as HTMLElement
    const s = el.getBoundingClientRect()
    const r = tab.getBoundingClientRect()
    return {
      inView: r.left >= s.left - 1 && r.right <= s.right + 1,
      tab: [Math.round(r.left), Math.round(r.right)],
      strip: [Math.round(s.left), Math.round(s.right)],
      scrollLeft: el.scrollLeft,
      name: tab.title,
    }
  })
}

test('画布页签条没有纵向溢出、不画滚动条，页签多了仍能横滚', async ({ app, page }) => {
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

  // 新建的画布排在最后并被激活：它必须已经滚进视野
  const created = await activeTabInView(strip)
  expect(created.inView, JSON.stringify(created)).toBe(true)

  // 滚动条不占高度：条的内容盒仍是 35（36 减 border-b），每个页签都是这么高。
  // 画出 10px 横滚条时 WebKit 里这里是 25
  const box = await strip.evaluate((el) => ({
    clientHeight: el.clientHeight,
    tabHeights: [
      ...new Set(
        [...el.querySelectorAll('[role="tab"]')].map(
          (t) => (t as HTMLElement).getBoundingClientRect().height,
        ),
      ),
    ],
  }))
  expect(box.clientHeight, JSON.stringify(box)).toBe(35)
  expect(box.tabHeights, JSON.stringify(box)).toEqual([35])

  // 横向：放不下时仍然能滚——scrollWidth 超出，scrollLeft 真的改得动。
  // 光看 scrollLeft 不够：overflow-x: hidden 的盒子脚本照样能改 scrollLeft，用户却滚不动，
  // 所以一并钉住 overflowX 是可滚的那两档
  const h = await strip.evaluate((el) => {
    el.scrollLeft = 0
    const atStart = el.scrollLeft
    el.scrollLeft = el.scrollWidth
    const overflowX = getComputedStyle(el).overflowX
    return { sw: el.scrollWidth, cw: el.clientWidth, overflowX, atStart, atEnd: el.scrollLeft }
  })
  expect(['auto', 'scroll'], JSON.stringify(h)).toContain(h.overflowX)
  expect(h.sw, JSON.stringify(h)).toBeGreaterThan(h.cw)
  expect(h.atStart, JSON.stringify(h)).toBe(0)
  expect(h.atEnd, JSON.stringify(h)).toBeGreaterThan(0)

  // 横滚条藏起来之后，纵向照样不许溢出
  const many = await verticalOverflow(strip)
  expect(many.scrollHeight, JSON.stringify(many)).toBeLessThanOrEqual(many.clientHeight)

  // 真的滚轮输入也滚得动（触控板横滑 = 带 deltaX 的 wheel）
  await strip.evaluate((el) => (el.scrollLeft = 0))
  const sb = (await strip.boundingBox())!
  await page.mouse.move(sb.x + sb.width / 2, sb.y + sb.height / 2)
  await page.mouse.wheel(200, 0)
  await expect.poll(() => strip.evaluate((el) => el.scrollLeft)).toBeGreaterThan(0)
  // Shift+滚轮这里量不了：macOS 上是系统把它换成 deltaX 再交给浏览器（到这里就和上面的
  // 横滑是同一条路），合成的 Shift + deltaY 在 chromium / webkit 两腿实测都不滚

  // 回到第一页，再从「全部画布」菜单切到最后一个：它在条外，切过去要自动滚进来
  await strip.evaluate((el) => (el.scrollLeft = 0))
  await strip.getByRole('tab').first().click()
  await expect(strip.getByRole('tab').first()).toHaveAttribute('aria-selected', 'true')
  const lastName = (await strip.getByRole('tab').last().getAttribute('title'))!
  const before = await activeTabInView(strip)
  expect(before.scrollLeft, JSON.stringify(before)).toBe(0)
  await page.locator('[data-all-canvases]').click()
  await page.getByRole('menuitem', { name: lastName, exact: true }).click()
  await expect(strip.getByRole('tab').last()).toHaveAttribute('aria-selected', 'true')
  await expect.poll(async () => JSON.stringify(await activeTabInView(strip))).toContain('"inView":true')
})
