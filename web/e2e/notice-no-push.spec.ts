import AxeBuilder from '@axe-core/playwright'
import type { Page } from '@playwright/test'
import { lowContrastNodes } from './contrast'
import { expect, test } from './fixtures'

/**
 * UI 2.0「错误与警告」+「不推挤」（PR10）。
 *
 * 量的是画出来的盒子（jsdom 量不到几何）：
 *   1. 构建版本不一致：此前是工作面板顶上一条整宽提示条，画布标签行与画布整体下推 44px；
 *      现在是顶栏右侧的状态芯片——出现前后，标签行 / 画布 / 工作面板的 y 与高度一个像素都不许动。
 *   2. 左栏刷新失败：Notice 必须在「图」区头与网格之间长出来，下面的网格是**顺着滑下去**的（有过渡帧），
 *      不是瞬间跳一格；终态位移 = 这条 Notice 自己的高度，别无其它。
 *   3. 两处新皮肤在浅 / 深色下都过 axe（无 critical / serious）与自算对比度。
 *
 * 语言无关：chromium（zh-CN）与 chromium-en 各跑一遍；定位只认 data-* 钩子。
 */

async function openCanvasWithFigure(page: Page, baseURL: string) {
  await page.goto(baseURL)
  const card = page.locator('[data-card="Fig1_kinetics.pdf"]')
  await expect(card).toHaveCount(1, { timeout: 30_000 })
  await card.dblclick()
  const back = page.locator('[data-context-back]')
  await expect(back).toHaveCount(1)
  await back.click()
  await expect(page.locator('[data-canvas-tabs]')).toHaveCount(1)
  await expect(page.locator('[data-canvas-stage]')).toHaveCount(1)
}

const box = (page: Page, selector: string) =>
  page.locator(selector).evaluate((el) => {
    const r = el.getBoundingClientRect()
    return { x: r.x, y: r.y, w: r.width, h: r.height }
  })

async function layoutBoxes(page: Page) {
  return {
    topbar: await box(page, '[data-topbar]'),
    panel: await box(page, '[data-work-panel]'),
    tabs: await box(page, '[data-canvas-tabs]'),
    stage: await box(page, '[data-canvas-stage]'),
  }
}

async function axeSeriousViolations(page: Page) {
  await page.emulateMedia({ reducedMotion: 'reduce' })
  const results = await new AxeBuilder({ page }).analyze()
  return results.violations
    .filter((v) => v.impact === 'critical' || v.impact === 'serious')
    .map((v) => ({ id: v.id, nodes: v.nodes.slice(0, 3).map((n) => n.target.join(' ')) }))
}

test('构建版本不一致：是顶栏芯片，工作面板里的标签行与画布一个像素都不动', async ({ app, page }) => {
  const a = await app()
  await openCanvasWithFigure(page, a.baseURL)
  await expect(page.locator('[data-build-mismatch]')).toHaveCount(0)
  const before = await layoutBoxes(page)

  // 让下一次版本自检认为后端换了构建：芯片出现
  await page.route(/\/api\/version(\?|$)/, (route) => route.fulfill({ json: { build: 'index-someothernewbuild', version: 'x' } }))
  await page.evaluate(() => window.dispatchEvent(new Event('mm:sse-open')))
  const chip = page.locator('[data-topbar] [data-build-mismatch]')
  await expect(chip).toHaveCount(1)
  await expect(chip).toBeVisible()
  // 等它的淡入落定再量
  await page.waitForTimeout(400)

  const after = await layoutBoxes(page)
  expect(after, '芯片出现前后，顶栏 / 工作面板 / 画布标签行 / 画布舞台的盒子完全相同').toEqual(before)

  // 它是 24 高的芯片，坐在顶栏里；工作面板里没有任何提示条
  const c = await box(page, '[data-topbar] [data-build-mismatch]')
  expect(c.h).toBeCloseTo(24, 0)
  expect(c.y).toBeGreaterThanOrEqual(before.topbar.y)
  expect(c.y + c.h).toBeLessThanOrEqual(before.topbar.y + before.topbar.h)
  await expect(page.locator('[data-work-panel] [data-build-mismatch]')).toHaveCount(0)
  await expect(page.locator('[data-banner-stack]')).toHaveCount(0)

  // 点开：一句话 + 一个动作；浅 / 深色都过 axe 与自算对比度
  for (const scheme of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: scheme })
    await chip.click()
    const pop = page.locator('[data-build-mismatch-popover]')
    await expect(pop).toBeVisible()
    await expect(pop.locator('button')).toHaveCount(1)
    await page.waitForTimeout(300)
    expect(await axeSeriousViolations(page), `${scheme}：芯片与浮层`).toEqual([])
    expect(await lowContrastNodes(page, 'body'), `${scheme}：自算对比度`).toEqual([])
    await page.keyboard.press('Escape')
    await expect(pop).toHaveCount(0)
  }
})

test('左栏刷新失败：Notice 顺着滑出来（有过渡帧），终态位移 = 它自己的高度；浅 / 深色过 axe', async ({ app, page }) => {
  const a = await app()
  await page.goto(a.baseURL)
  const rail = page.locator('[data-rail="assets"]')
  await expect(rail).toHaveCount(1)
  if ((await rail.getAttribute('aria-expanded')) !== 'true') await rail.click()
  const grid = page.locator('#asset-figures-section')
  await expect(page.locator('[data-card="Fig1_kinetics.pdf"]')).toHaveCount(1, { timeout: 30_000 })
  await expect(page.locator('[data-asset-refresh-failed]')).toHaveCount(0)
  const top0 = (await box(page, '#asset-figures-section')).y

  // 后端那一轮成了、/api/panels 失败：load() 吞错，唯一的出口就是这条 Notice
  await page.route(/\/api\/panels(\?|$)/, (route) =>
    route.fulfill({ status: 500, json: { error: 'panels 读不回来', code: 'boom' } }),
  )
  // 每一帧采样网格顶边，直到 1.2s
  await page.evaluate(() => {
    const w = window as unknown as { __tops: number[] }
    w.__tops = []
    const t0 = performance.now()
    const tick = () => {
      const el = document.querySelector('#asset-figures-section')
      if (el) w.__tops.push(Math.round(el.getBoundingClientRect().y * 10) / 10)
      if (performance.now() - t0 < 1200) requestAnimationFrame(tick)
    }
    requestAnimationFrame(tick)
  })
  await page.locator('[data-asset-refresh]').click()
  const notice = page.locator('[data-asset-refresh-failed]')
  await expect(notice).toHaveCount(1)
  await page.waitForTimeout(1300)
  const tops = (await page.evaluate(() => (window as unknown as { __tops: number[] }).__tops)) as number[]
  const final = tops[tops.length - 1] - top0
  const nb = await box(page, '[data-asset-refresh-failed]')
  // 一行（30 左右）、不是两三行的大块；终态位移不超过这条 Notice 自己的高度 + 容器 pb-2 的 8
  // （同一时刻「正在刷新…」那一行收走了，所以只会小不会大）
  expect(nb.h, 'Notice 是一行中性提示').toBeLessThanOrEqual(32)
  expect(final).toBeGreaterThan(10)
  expect(final, `终态位移 ${final}，Notice 高 ${nb.h}`).toBeLessThanOrEqual(nb.h + 8 + 1.5)
  // 过渡：起点 0 → 终点 final 之间至少有两帧停在中间（不是瞬间跳一格）
  const mid = new Set(tops.map((t) => Math.round(t - top0)).filter((d) => d > 1 && d < final - 1))
  expect(mid.size, `网格顶边的逐帧轨迹：${tops.join(',')}`).toBeGreaterThanOrEqual(2)
  // 它是中性的一行：底不是语义色，图标才是 danger 色
  expect(await grid.count()).toBe(1)
  const icon = notice.locator('[data-notice-icon]')
  await expect(icon).toHaveAttribute('data-notice-icon', 'attention')

  for (const scheme of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: scheme })
    await page.waitForTimeout(200)
    expect(await axeSeriousViolations(page), `${scheme}：Notice`).toEqual([])
    expect(await lowContrastNodes(page, '[data-asset-refresh-failed]'), `${scheme}：Notice 对比度`).toEqual([])
  }
})
