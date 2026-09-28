import { expect, test } from './fixtures'
import { horizontalOffenders } from './overflow'
import type { Page, Request } from '@playwright/test'

/**
 * 多选时的浮动栏（ADR 0089）——只放 jsdom 量不到的那几件事：
 *
 *   * 浮动栏真的出现在屏幕上（不是 DOM 里有、却被裁掉 / 推出视口 / 盖在别的层下面）；
 *   * 画布多选：点栏上的「左对齐」，两个对象在屏幕上的左沿真的对齐了；
 *   * 图内多选（两个图例项）：栏上改字号，**真 matplotlib 重画回来的 manifest** 里两个元素都是
 *     新字号；撤销一次两个都回原样；重做后刷新页面，重新渲染出来的仍是新字号（写进了文档）。
 *
 *   * 两侧之间放不下完整栏时（压缩档）：整条栏都在窗口里，右半截的控件够得着（Codex #666 P1）。
 *
 * 定位一律认稳定的 `data-*`（素材卡 `data-card`、返回 `data-context-back`、文字工具
 * `data-tool`、字号 `data-inspector-prop`、右栏关闭 `data-inspector-close`、适应画布
 * `data-fit-canvas`），不认界面文案 / 可达名。
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
  await page.locator('[data-card="Fig1_kinetics.pdf"]').dblclick({ timeout: 30_000 })
  await expect(page.locator('[data-exit-element-edit]')).toBeVisible({ timeout: 60_000 })
  await page.locator('[data-context-back]').click()
  await expect(page.locator('[data-object-id]').first()).toBeVisible()

  for (const [x, y, s] of [
    [160, 140, 'alpha'],
    [360, 560, 'beta'],
  ] as const) {
    await page.locator('[data-tool="text"]').click()
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
  await page.locator('[data-card="Fig1_kinetics.pdf"]').dblclick({ timeout: 30_000 })
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

  const size = bar.locator('[data-inspector-prop="sizePt"]')
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

  // 重做后刷新：文档里记着的就是新字号，重新渲染出来仍是它。
  // 「落盘了」的判据是**重做之后发出的**自动保存请求（PUT /api/autosave/…）成功返回——
  // 不读顶栏的保存文案（#674 起那句话换了措辞），也不认重做之前就在路上的那一次
  const saves: Promise<boolean>[] = []
  const onRequest = (req: Request) => {
    if (req.method() === 'PUT' && req.url().includes('/api/autosave/')) {
      saves.push(
        req.response().then(
          (r) => !!r?.ok(),
          () => false,
        ),
      )
    }
  }
  page.on('request', onRequest)
  await page.keyboard.press('ControlOrMeta+Shift+z')
  await expect
    .poll(async () => (await glyphHeights(page)).every((h, i) => h > h0[i] * 1.2), { timeout: 60_000 })
    .toBe(true)
  await expect
    .poll(async () => (await Promise.all(saves)).some(Boolean), {
      timeout: 30_000,
      message: '重做之后应当有一次成功的自动保存',
    })
    .toBe(true)
  page.off('request', onRequest)
  const n = renders.count()
  await page.reload()
  await expect.poll(renders.count, { timeout: 60_000 }).toBeGreaterThan(n)
  await expect.poll(renders.latestSizes, { timeout: 60_000 }).toEqual([target, target])
})

test('图内多选在两侧之间放不下完整栏时：压缩档，整条栏在窗口里、对齐与字号都够得着', async ({
  app,
  page,
}) => {
  const a = await app()
  // 宽屏里从素材卡进图内编辑，再把窗口收到窄断点：侧栏变成覆盖式抽屉，两侧之间
  // 就是整个窗口，不到 600 px。属性页不停靠，停靠缩减（textBarCompact）不成立，只剩
  // 「放不下」这一条判据在起作用
  await page.setViewportSize({ width: 1440, height: 860 })
  await page.goto(a.baseURL)
  await page.locator('[data-card="Fig1_kinetics.pdf"]').dblclick({ timeout: 30_000 })
  await expect(page.locator('[data-element-svg] svg').first()).toBeVisible({ timeout: 60_000 })
  await expect(page.locator('[data-authority="ready"]').first()).toBeVisible({ timeout: 60_000 })
  await page.setViewportSize({ width: 560, height: 860 })
  // 窄断点下右栏成了盖在画布上的抽屉：先收起，再适应画布把图放回眼前
  await page.locator('[data-inspector-close]').click()
  await page.locator('[data-fit-canvas]').click()
  // 适应画布带过渡：等图停稳再量点击位置
  await expect
    .poll(async () => {
      const p0 = await center(page, 'axes_0.title')
      await page.waitForTimeout(200)
      return JSON.stringify(p0) === JSON.stringify(await center(page, 'axes_0.title'))
    })
    .toBe(true)

  // 窄断点下每次选中都会把属性抽屉弹出来盖住画布（抽屉开着时浮动栏让位）：每点一次收一次
  const closeDrawer = async () => {
    await expect(page.locator('[data-inspector-close]')).toBeVisible()
    await page.locator('[data-inspector-close]').click()
    await expect(page.locator('[data-inspector-close]')).toHaveCount(0)
  }
  const c0 = (await center(page, 'axes_0.title'))!
  const c1 = (await center(page, 'axes_0.ylabel'))!
  await page.mouse.click(c0.x, c0.y)
  await closeDrawer()
  await page.keyboard.down('Shift')
  await page.mouse.click(c1.x, c1.y)
  await page.keyboard.up('Shift')
  await closeDrawer()

  const bar = page.locator('[data-context-bar][data-context-bar-mode="elements"]')
  await expect(bar).toBeVisible()
  await expect(bar.locator('[data-selection-count="2"]')).toBeVisible()
  // 场景自检：确实走到了「放不下」那一档，而不是停靠缩减
  await expect(bar).toHaveAttribute('data-variant', 'compact')
  await expect(bar).not.toHaveAttribute('data-context-bar-compact', '')
  // 整条栏都在窗口里（不是左沿夹住、右半截伸出屏幕外），自身也没有被撑破
  await expect(bar).toBeInViewport({ ratio: 1 })
  expect(await horizontalOffenders(page, '[data-context-bar]')).toEqual([])
  await expect(bar.locator('[data-inspector-prop="sizePt"]')).toBeInViewport({ ratio: 1 })
  // 六向对齐收进「对齐」弹层，打开后按钮在窗口里、点得到
  await bar.locator('[data-multi-menu="align"]').click()
  const left = page.locator('[data-radix-popper-content-wrapper] [data-align-mode="left"]')
  await expect(left).toBeInViewport({ ratio: 1 })
  await left.click()
})

test('画布多选全是文字、窗口很窄：压缩档仍放不下就降到最窄档，整条栏在窗口里、排列与文字都够得着', async ({
  app,
  page,
}) => {
  const a = await app()
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto(a.baseURL)
  await page.locator('[data-card="Fig1_kinetics.pdf"]').dblclick({ timeout: 30_000 })
  await expect(page.locator('[data-exit-element-edit]')).toBeVisible({ timeout: 60_000 })
  await page.locator('[data-context-back]').click()
  await expect(page.locator('[data-object-id]').first()).toBeVisible()
  for (const [x, y, s] of [
    [160, 140, 'alpha'],
    [360, 560, 'beta'],
  ] as const) {
    await page.locator('[data-tool="text"]').click()
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

  // 收到 400 宽（窄断点，两侧之间就是整个窗口）：压缩档的全文字栏量出来约 470 px，放不下。
  // 右栏在窄断点下成了盖住画布的抽屉（开着时浮动栏让位），收起它
  await page.setViewportSize({ width: 400, height: 900 })
  await expect(page.locator('[data-inspector-close]')).toBeVisible()
  await page.locator('[data-inspector-close]').click()

  const bar = page.locator('[data-multi-selection-context-bar]')
  await expect(bar).toBeVisible()
  await expect(bar.locator('[data-selection-count="2"]')).toBeVisible()
  // 场景自检：走到的是「压缩档量出来仍放不下」那一档
  await expect(bar).toHaveAttribute('data-variant', 'minimal')
  // 整条栏都在窗口里，自身也没有被撑破
  await expect(bar).toBeInViewport({ ratio: 1 })
  expect(await horizontalOffenders(page, '[data-context-bar]')).toEqual([])
  await expect(bar.locator('[data-multi-more]')).toBeInViewport({ ratio: 1 })

  // 文字弹层：字号在窗口里、改得动
  await bar.locator('[data-multi-menu="text"]').click()
  const size = page.locator('[data-radix-popper-content-wrapper] [data-inspector-prop="sizePt"]')
  await expect(size).toBeInViewport({ ratio: 1 })
  // 再点一次入口收起（Esc 会连浮动栏一起关掉本次显示）
  await bar.locator('[data-multi-menu="text"]').click()
  await expect(size).toHaveCount(0)

  // 排列弹层：左对齐在窗口里，点了真的对齐
  const lefts = () =>
    texts.evaluateAll((els) => els.map((e) => Math.round(e.getBoundingClientRect().left)))
  expect(new Set(await lefts()).size).toBe(2)
  await bar.locator('[data-multi-menu="arrange"]').click()
  const left = page.locator('[data-radix-popper-content-wrapper] [data-align-mode="left"]')
  await expect(left).toBeInViewport({ ratio: 1 })
  await left.click()
  await expect.poll(async () => new Set(await lefts()).size).toBe(1)
})
