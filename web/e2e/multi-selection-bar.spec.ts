import { expect, test } from './fixtures'
import type { Page } from '@playwright/test'

/**
 * 多选时的浮动栏（ADR 0089）——只放 jsdom 量不到的那几件事：
 *
 *   * 浮动栏真的出现在屏幕上（不是 DOM 里有、却被裁掉 / 推出视口 / 盖在别的层下面）；
 *   * 画布多选：点栏上的「左对齐」，两个对象在屏幕上的左沿真的对齐了；
 *   * 图内多选（两个图例项）：栏上改字号，**真 matplotlib 重画回来的 manifest** 里两个元素都是
 *     新字号；撤销一次两个都回原样；重做后刷新页面，重新渲染出来的仍是新字号（写进了文档）。
 *
 * 判据的主语：图内那条量的是后端渲染响应里的 manifest（引擎按 override 重画的结果），
 * 不是前端 store，也不是控件显示的数。
 */

interface ManifestEl {
  gid: string
  editable: { prop: string; value: unknown }[]
}

const LEGEND = ['axes_0.legend.texts_0', 'axes_0.legend.texts_1']

const sizeOf = (els: ManifestEl[], gid: string) =>
  Number(els.find((e) => e.gid === gid)?.editable.find((f) => f.prop === 'fontsize')?.value)

/** 收集每一次成功的渲染响应；判据只看最新那份 */
function watchRenders(page: Page) {
  const seen: ManifestEl[][] = []
  page.on('response', async (res) => {
    if (!res.url().includes('/api/engine/render') || res.status() !== 200) return
    try {
      const body = await res.json()
      if (body?.manifest?.elements) seen.push(body.manifest.elements)
    } catch {
      /* 非 JSON：不是一次成功渲染 */
    }
  })
  return {
    count: () => seen.length,
    latestSizes: () => {
      const last = seen.at(-1)
      return last ? LEGEND.map((g) => sizeOf(last, g)) : []
    },
  }
}

const center = (page: Page, gid: string) =>
  page.evaluate((id) => {
    const n = document.querySelector(`[data-element-svg] svg [id="${id}"]`)
    if (!n) return null
    const r = (n as SVGGraphicsElement).getBoundingClientRect()
    return { x: r.x + r.width / 2, y: r.y + r.height / 2 }
  }, gid)

/**
 * 画面上两个图例项的字高（SVG 用户单位，与视图倍率无关）。撤销回到的是渲染缓存里的
 * 上一版，不一定再发一次渲染请求——所以「回原样」量的是画面，不是网络响应。
 */
const glyphHeights = (page: Page) =>
  page.evaluate((gids) => {
    return gids.map((id) => {
      const n = document.querySelector(`[data-element-svg] svg [id="${id}"]`)
      return n ? Math.round((n as SVGGraphicsElement).getBBox().height * 100) / 100 : NaN
    })
  }, LEGEND)

const blurActive = (page: Page) =>
  page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur())

test('画布多选：浮动栏出现，左对齐真的对齐了', async ({ app, page }) => {
  const a = await app()
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto(a.baseURL)
  await page.getByText('Fig1_kinetics.pdf').dblclick({ timeout: 30_000 })
  await expect(page.getByText('画布是空的')).toHaveCount(0)
  await page.getByRole('button', { name: /返回画布/ }).first().click()
  await expect(page.locator('[data-object-id]').first()).toBeVisible()

  for (const [x, y, s] of [
    [160, 140, 'alpha'],
    [360, 560, 'beta'],
  ] as const) {
    await page.getByRole('button', { name: '文字' }).click()
    await page.locator('[data-canvas-stage]').click({ position: { x, y } })
    await page.keyboard.type(s)
    await page.keyboard.press('Escape')
  }
  await page.keyboard.press('Escape')

  const texts = page.locator('[data-object-id]').filter({ hasText: /^(alpha|beta)$/ })
  await expect(texts).toHaveCount(2)
  await texts.nth(0).click()
  await page.keyboard.down('Shift')
  await texts.nth(1).click()
  await page.keyboard.up('Shift')

  const bar = page.locator('[data-multi-selection-context-bar]')
  await expect(bar).toBeVisible()
  await expect(bar).toBeInViewport()
  // 全是文字：多选栏里有字号（ADR 0089）
  await expect(bar.locator('[data-text-quick]')).toBeVisible()

  const lefts = () =>
    texts.evaluateAll((els) => els.map((e) => Math.round(e.getBoundingClientRect().left)))
  expect(new Set(await lefts()).size).toBe(2)
  await bar.locator('[data-align-mode="left"]').click()
  await expect.poll(async () => new Set(await lefts()).size).toBe(1)
})

test('图内多选两个图例项：浮动栏改字号两个都变，撤销一次回原样，重开后保持', async ({ app, page }) => {
  test.setTimeout(240_000)
  const a = await app()
  await page.setViewportSize({ width: 1440, height: 900 })
  const renders = watchRenders(page)
  await page.goto(a.baseURL)
  await page.getByText('Fig1_kinetics.pdf').dblclick({ timeout: 30_000 })
  await expect(page.locator('[data-element-svg] svg').first()).toBeVisible({ timeout: 60_000 })
  await expect(page.locator('[data-authority="ready"]').first()).toBeVisible({ timeout: 60_000 })
  await expect.poll(renders.count, { timeout: 60_000 }).toBeGreaterThan(0)
  const before = renders.latestSizes()
  expect(before[0]).toBe(before[1])
  const target = before[0] + 3
  const h0 = await glyphHeights(page)

  const c0 = (await center(page, LEGEND[0]))!
  const c1 = (await center(page, LEGEND[1]))!
  await page.mouse.click(c0.x, c0.y)
  await page.keyboard.down('Shift')
  await page.mouse.click(c1.x, c1.y)
  await page.keyboard.up('Shift')

  const bar = page.locator('[data-context-bar][data-context-bar-mode="elements"]')
  await expect(bar).toBeVisible()
  await expect(bar).toBeInViewport()
  await expect(bar.locator('[data-selection-count="2"]')).toBeVisible()

  const size = bar.getByLabel('字号')
  await size.fill(String(target))
  await size.press('Enter')
  await expect.poll(renders.latestSizes, { timeout: 60_000 }).toEqual([target, target])
  // 画面上两个都变大了（不只是 manifest 里的数）
  await expect
    .poll(async () => (await glyphHeights(page)).every((h, i) => h > h0[i] * 1.2), { timeout: 30_000 })
    .toBe(true)

  // 撤销一次：两个都回原样（一次改动 = 一条历史）
  await blurActive(page)
  await page.keyboard.press('ControlOrMeta+z')
  await expect.poll(() => glyphHeights(page), { timeout: 60_000 }).toEqual(h0)

  // 重做后刷新：文档里记着的就是新字号，重新渲染出来仍是它
  await page.keyboard.press('ControlOrMeta+Shift+z')
  await expect
    .poll(async () => (await glyphHeights(page)).every((h, i) => h > h0[i] * 1.2), { timeout: 60_000 })
    .toBe(true)
  await expect(page.getByText('已保存').first()).toBeVisible({ timeout: 30_000 })
  const n = renders.count()
  await page.reload()
  await expect.poll(renders.count, { timeout: 60_000 }).toBeGreaterThan(n)
  await expect.poll(renders.latestSizes, { timeout: 60_000 }).toEqual([target, target])
})
