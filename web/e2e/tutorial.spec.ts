import AxeBuilder from '@axe-core/playwright'
import { expect, test } from './fixtures'
import type { Locator, Page, Request } from '@playwright/test'

/**
 * 三步原生新手教程（ADR 0136）——只有真浏览器 + 真后端才能回答的那几件：
 *
 *   * 第一次打开（主页、没有项目）欢迎页自己出现；从欢迎页走完三步，每一步都由**真实的
 *     指针拖放 / 点击**完成（拖卡片进投放框、拖图例、点标题再点 +），完成页的前后对比来自
 *     两份真实状态，「开始使用」落回真正的主页；
 *   * **P0：完成教程不执行 Python**——整个过程里没有一个运行 / 渲染 / 依赖准备 / 打开项目 /
 *     教程资源的请求（教程层只改本机状态）；
 *   * 外部文件拖进教程被拒收，不打开、不运行、不算完成；
 *   * 刷新回到同一步、演示改动还在；跳过不记完成；v2 进行中的老用户不被拽进新教程；
 *   * 小窗口 + reduced-motion 下主操作目标不用滚动就看得见；axe 无 critical / serious。
 *
 * **这些是演示层的端到端，不是图内编辑的端到端**：图例 / 标题改的是教程自己的状态，
 * 编辑器的真实编辑能力由它自己的 spec 负责，这里一条都不替它作证。
 */

test.use({ firstRunWelcome: true })

const tut = (page: Page) => page.locator('[data-native-tutorial]')
const step = (page: Page) => page.locator('[data-tutorial-step]')
const next = (page: Page) => page.locator('[data-tutorial-next]')
const onboarding = (page: Page) =>
  page.evaluate(() => JSON.parse(localStorage.getItem('tavotto.onboarding') ?? '{}'))

/**
 * 教程期间不许出现的请求：运行 / 渲染 / 准备环境 / 依赖 / 打开项目 / 示例项目 / 面板扫描 / 自动保存。
 * 判据写成正面清单（「这些路径一条都没有」），遥测与 SSE 心跳之类与教程无关的不在主语里。
 */
const FORBIDDEN =
  /\/api\/(?:render|run|native|prepare|preparation|deps|dependenc|env|engine|projects\/open|tutorial|panels|figures|scan|autosave)/

function watchRequests(page: Page) {
  const seen: string[] = []
  const on = (r: Request) => {
    const u = new URL(r.url())
    if (u.pathname.startsWith('/api/')) seen.push(`${r.method()} ${u.pathname}`)
  }
  page.on('request', on)
  return {
    forbidden: () => seen.filter((s) => FORBIDDEN.test(s)),
    all: () => [...seen],
    stop: () => page.off('request', on),
  }
}

async function center(loc: Locator) {
  const box = await loc.boundingBox()
  expect(box).not.toBeNull()
  return { x: box!.x + box!.width / 2, y: box!.y + box!.height / 2 }
}

/** 真实的指针拖动：按下 → 分步移动 → 松开 */
async function drag(page: Page, from: { x: number; y: number }, to: { x: number; y: number }) {
  await page.mouse.move(from.x, from.y)
  await page.mouse.down()
  await page.mouse.move(from.x + 8, from.y + 8, { steps: 2 })
  await page.mouse.move(to.x, to.y, { steps: 12 })
  await page.mouse.up()
}

async function dropSample(page: Page) {
  const card = page.locator('[data-tutorial-file-card]')
  const zone = page.locator('[data-tutorial-drop-zone]')
  await drag(page, await center(card), await center(zone))
  await rendered(page)
}

/** 放下之后先演一段「渲染中」，走完才出图 */
async function rendered(page: Page) {
  await expect(page.locator('[data-demo-figure]')).toBeVisible()
  await expect(page.locator('[data-tutorial-rendering]')).toHaveCount(0)
}

async function dragLegendUp(page: Page, dy = -140) {
  const from = await center(page.locator('[data-demo-legend]'))
  await drag(page, from, { x: from.x, y: from.y + dy })
}

const curvesOf = (l: Locator) => l.locator('polyline').evaluateAll((ps) => ps.map((p) => p.getAttribute('points')))

test('完整走完新手教程：欢迎页 → 拖入示例脚本 → 拖动图例 → 改标题字号 → 前后对比 → 开始使用', async ({
  app,
  page,
}) => {
  test.setTimeout(180_000)
  const a = await app({ noProject: true })
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto(a.baseURL)

  // 第一次打开：欢迎页自己盖上来；代码片段来自内置示例（逐行同源由 pytest 看护）
  await expect(tut(page)).toHaveAttribute('data-surface', 'welcome')
  await expect(page.locator('[data-tutorial-script-card]')).toContainText('fig1_kinetics.py')
  await expect(page.locator('[data-tutorial-script-card]')).toContainText('np.linspace(0, 60, 200)')
  const requests = watchRequests(page)

  await page.locator('[data-tutorial-begin]').click()
  await expect(step(page)).toHaveAttribute('data-tutorial-step', 'demo_import')
  await expect(page.locator('[data-tutorial-progress]')).toHaveAttribute('data-tutorial-progress', '1/3')
  await expect(next(page)).toBeDisabled()

  // ---- 第一步：拖错地方 → 回原位、不推进；拖进投放框 → 完成 ----
  const card = page.locator('[data-tutorial-file-card]')
  const home = await center(card)
  await drag(page, home, { x: home.x + 40, y: home.y + 10 })
  await expect(next(page)).toBeDisabled()
  await expect(page.locator('[data-tutorial-feedback]')).toHaveAttribute('data-tutorial-feedback', 'notice')
  await expect(page.locator('[data-tutorial-use-sample]')).toHaveCount(0) // 只教拖放，没有捷径
  await drag(page, await center(card), await center(page.locator('[data-tutorial-drop-zone]')))
  await expect(page.locator('[data-tutorial-rendering]')).toBeVisible()
  await expect(next(page)).toBeDisabled()
  await rendered(page)
  await expect(page.locator('[data-tutorial-feedback]')).toHaveAttribute('data-tutorial-feedback', 'success')
  await expect(page.locator('[data-demo-figure]')).toHaveCount(1)
  await next(page).click()

  // ---- 第二步：拖动图例（只有图例动，曲线不动） ----
  await expect(step(page)).toHaveAttribute('data-tutorial-step', 'demo_move_legend')
  await expect(page.locator('[data-tutorial-ring="guide"]')).toBeVisible()
  const figure = page.locator('[data-demo-figure]')
  const curves = await curvesOf(figure)
  const legendBefore = await page.locator('[data-demo-legend]').getAttribute('transform')
  await dragLegendUp(page)
  await expect(next(page)).toBeEnabled()
  expect(await page.locator('[data-demo-legend]').getAttribute('transform')).not.toBe(legendBefore)
  expect(await curvesOf(figure)).toEqual(curves)
  const legendAfter = await page.locator('[data-demo-legend]').getAttribute('transform')
  await next(page).click()

  // ---- 第三步：点标题 → 字号控件 → + → 完成；图例位置带过来 ----
  await expect(step(page)).toHaveAttribute('data-tutorial-step', 'demo_change_title')
  expect(await page.locator('[data-demo-legend]').getAttribute('transform')).toBe(legendAfter)
  await expect(page.locator('[data-demo-title]')).toHaveAttribute('font-size', '9')
  await page.locator('[data-demo-title]').click()
  await expect(page.locator('[data-tutorial-font-controls]')).toBeVisible()
  await expect(next(page)).toBeDisabled() // 只点了标题，不算
  await page.locator('[data-tutorial-size-increase]').click()
  await page.locator('[data-tutorial-size-increase]').click()
  await expect(page.locator('[data-demo-title]')).toHaveAttribute('font-size', '11')
  await expect(next(page)).toBeEnabled()
  await next(page).click()

  // ---- 完成页：同一组曲线；只差图例位置与标题字号 ----
  await expect(step(page)).toHaveAttribute('data-tutorial-step', 'done')
  const before = page.locator('[data-tutorial-compare-panel="before"] [data-demo-figure]')
  const after = page.locator('[data-tutorial-compare-panel="after"] [data-demo-figure]')
  expect(await curvesOf(before)).toEqual(await curvesOf(after))
  await expect(before.locator('[data-demo-title]')).toHaveAttribute('font-size', '9')
  await expect(after.locator('[data-demo-title]')).toHaveAttribute('font-size', '11')
  expect(await after.locator('[data-demo-legend]').getAttribute('transform')).toBe(legendAfter)
  expect(await before.locator('[data-demo-legend]').getAttribute('transform')).toBe(legendBefore)

  // P0：从「开始」到完成页，没有一个运行 / 渲染 / 准备 / 打开项目 / 教程资源的请求。
  // 主语是**教程期间**：「开始使用」之后落回的真实主页会照常探一次示例项目的资源（只读的
  // GET /api/tutorial，给老手版「使用示例脚本」按钮用），那已经不是教程了
  requests.stop()
  expect(requests.forbidden(), `教程期间的 API 请求：\n${requests.all().join('\n')}`).toEqual([])

  await page.locator('[data-tutorial-start-using]').click()
  await expect(tut(page)).toHaveCount(0)
  // 落回真正的主页（不是占位页）：走完了教程 = 老手版
  await expect(page.locator('main[data-home-variant="returning"]')).toBeVisible()
  expect((await onboarding(page)).status).toBe('completed')
})

test('外部 .py 拖进教程：拒收、只提示一句，不打开、不运行、不算完成', async ({ app, page }) => {
  const a = await app({ noProject: true })
  await page.goto(a.baseURL)
  await page.locator('[data-tutorial-begin]').click()
  await expect(step(page)).toHaveAttribute('data-tutorial-step', 'demo_import')
  const requests = watchRequests(page)
  const outcome = await page.locator('[data-tutorial-drop-zone]').evaluate((zone) => {
    const dt = new DataTransfer()
    dt.items.add(new File(['import matplotlib.pyplot as plt\n'], 'my_plot.py', { type: 'text/x-python' }))
    const fire = (type: string) => {
      const ev = new DragEvent(type, { bubbles: true, cancelable: true, dataTransfer: dt })
      zone.dispatchEvent(ev)
      return { prevented: ev.defaultPrevented, effect: dt.dropEffect }
    }
    return { enter: fire('dragenter'), over: fire('dragover'), drop: fire('drop') }
  })
  expect(outcome.over.prevented).toBe(true)
  expect(outcome.over.effect).toBe('none')
  await expect(page.locator('[data-tutorial-external-notice]')).toContainText('完成后可以导入自己的脚本')
  await expect(next(page)).toBeDisabled()
  expect((await onboarding(page)).completedSteps ?? []).not.toContain('demo_import')
  requests.stop()
  expect(requests.forbidden()).toEqual([])
})

test('刷新回到同一步、演示改动还在；键盘完成三步；Esc 只取消拖动、不关教程', async ({ app, page }) => {
  test.setTimeout(150_000)
  const a = await app({ noProject: true })
  await page.goto(a.baseURL)
  await page.locator('[data-tutorial-begin]').click()

  // 第一步：键盘拿起 → 方向键移进框 → 回车
  const card = page.locator('[data-tutorial-file-card]')
  await card.focus()
  await page.keyboard.press('Space')
  await page.keyboard.press('Escape') // 只取消这次拿起
  await expect(tut(page)).toBeVisible()
  await expect(card).toHaveAttribute('aria-pressed', 'false')
  const zone = await center(page.locator('[data-tutorial-drop-zone]'))
  const from = await center(card)
  await page.keyboard.press('Space')
  for (let i = 0; i < Math.ceil((zone.y - from.y) / 30); i++) await page.keyboard.press('ArrowDown')
  for (let i = 0; i < Math.ceil((zone.x - from.x) / 30); i++) await page.keyboard.press('ArrowRight')
  await page.keyboard.press('Enter')
  await expect(next(page)).toBeEnabled()
  await expect(next(page)).toBeFocused() // 焦点交给「下一步」
  await page.keyboard.press('Enter')

  // 第二步：图例键盘拿起、上移、放下
  const legend = page.locator('[data-demo-legend]')
  await legend.focus()
  await page.keyboard.press('Space')
  for (let i = 0; i < 6; i++) await page.keyboard.press('ArrowUp')
  await page.keyboard.press('Enter')
  await expect(next(page)).toBeEnabled()
  const moved = await legend.getAttribute('transform')

  // 刷新：回到同一步，图例位置还在
  await page.reload()
  await expect(step(page)).toHaveAttribute('data-tutorial-step', 'demo_move_legend')
  await expect(page.locator('[data-demo-legend]')).toHaveAttribute('transform', moved!)
  await expect(next(page)).toBeEnabled()
  await next(page).click()

  // 第三步：键盘选中标题 → 焦点进字号框 → 无效输入不改 → 越界的夹到上限
  await page.locator('[data-demo-title]').focus()
  await page.keyboard.press('Enter')
  const input = page.locator('[data-tutorial-font-controls] input')
  await expect(input).toBeFocused()
  await input.fill('abc')
  await page.keyboard.press('Enter')
  await expect(page.locator('[data-demo-title]')).toHaveAttribute('font-size', '9')
  // 越界的提交夹到上限（标题不会被顶出图幅），框闪一下告诉用户到头了
  await input.fill('30')
  await page.keyboard.press('Enter')
  await expect(page.locator('[data-demo-title]')).toHaveAttribute('font-size', '11')
  await expect(next(page)).toBeEnabled()
})

test('跳过不记完成；回到主页不再自己弹', async ({ app, page }) => {
  const a = await app({ noProject: true })
  await page.goto(a.baseURL)
  await page.locator('[data-tutorial-begin]').click()
  await page.locator('[data-tutorial-skip]').click()
  await expect(tut(page)).toHaveCount(0)
  const st = await onboarding(page)
  expect(st.status).toBe('skipped')
  expect(st.completedAt).toBeNull()
  await expect(page.locator('main[data-home-variant="returning"]')).toBeVisible()
  await page.reload()
  await expect(page.locator('main[data-home-variant="returning"]')).toBeVisible()
  await expect(tut(page)).toHaveCount(0)
})

test('欢迎页「直接进入工作台」：不启动教程，下次启动不再自己弹；主页入口还在', async ({ app, page }) => {
  const a = await app({ noProject: true })
  await page.goto(a.baseURL)
  await page.locator('[data-tutorial-open-workspace]').click()
  await expect(tut(page)).toHaveCount(0)
  await expect(page.locator('main[data-home-variant="newcomer"]')).toBeVisible()
  expect((await onboarding(page)).status).toBe('not_started')
  await page.reload()
  await expect(page.locator('main[data-home-variant="newcomer"]')).toBeVisible()
  await expect(tut(page)).toHaveCount(0)
  await page.locator('[data-onboarding-anchor="tutorial-entry"]').click()
  await expect(step(page)).toHaveAttribute('data-tutorial-step', 'demo_import')
})

test('flow v2 进行到一半的老用户：升级后不被拽进新教程，入口显示「继续」，点了从新第一步开始', async ({
  app,
  page,
}) => {
  await page.addInitScript(() =>
    window.localStorage.setItem(
      'tavotto.onboarding',
      JSON.stringify({
        schemaVersion: 1,
        flowVersion: 2,
        status: 'active',
        currentStep: 'export_canvas',
        completedSteps: ['open_fast_edit', 'select_text'],
        skippedSteps: [],
        tutorialProjectId: 'p_old',
        tutorialDocumentId: 'tavotto-tutorial',
      }),
    ),
  )
  const a = await app({ noProject: true })
  await page.goto(a.baseURL)
  await expect(page.locator('main[data-home-variant="newcomer"]')).toBeVisible()
  await expect(tut(page)).toHaveCount(0)
  const entry = page.locator('[data-onboarding-anchor="tutorial-entry"]')
  await expect(entry).toContainText('继续')
  await entry.click()
  await expect(step(page)).toHaveAttribute('data-tutorial-step', 'demo_import')
})

for (const size of [
  { width: 1024, height: 720 },
  { width: 1280, height: 800 },
]) {
  test(`${size.width}×${size.height} + reduced-motion：每一步的主操作目标不用滚动就看得见`, async ({ app, page }) => {
    await page.emulateMedia({ reducedMotion: 'reduce' })
    await page.setViewportSize(size)
    const a = await app({ noProject: true })
    await page.goto(a.baseURL)
    const inView = async (loc: Locator) => {
      const box = await loc.boundingBox()
      expect(box).not.toBeNull()
      expect(box!.y).toBeGreaterThanOrEqual(0)
      expect(box!.y + box!.height).toBeLessThanOrEqual(size.height + 0.5)
      expect(box!.x + box!.width).toBeLessThanOrEqual(size.width + 0.5)
    }
    await inView(page.locator('[data-tutorial-begin]'))
    // 欢迎页不被压扁：注释签不顶到功能行、不压到主按钮；蓝点落在图里的字外面（原型的要求）
    await page.evaluate(() => document.fonts.ready)
    const welcome = await page.evaluate(() => {
      const view = document.querySelector('[data-tutorial-view="welcome"]')!
      const r = (el: Element) => el.getBoundingClientRect()
      const chips = [...view.querySelectorAll('[data-welcome-callout]')].map(r)
      const dots = [...view.querySelectorAll('[data-welcome-callout-dot]')].map(r)
      const texts = [...view.querySelectorAll('[data-demo-stage] svg text')].map(r)
      const hit = (a: DOMRect, b: DOMRect) => a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top
      return {
        chips: chips.length,
        dots: dots.length,
        toFeatures: Math.min(...chips.map((c) => c.top)) - r(view.querySelector('ul')!).bottom,
        toButton: r(view.querySelector('[data-tutorial-begin]')!).top - Math.max(...chips.map((c) => c.bottom)),
        dotsOnText: dots.filter((d) => texts.some((t) => hit(d, t))).length,
      }
    })
    expect(welcome.chips).toBe(3)
    expect(welcome.dots).toBe(3)
    expect(welcome.toFeatures).toBeGreaterThanOrEqual(16)
    expect(welcome.toButton).toBeGreaterThanOrEqual(16)
    expect(welcome.dotsOnText).toBe(0)
    await page.locator('[data-tutorial-begin]').click()
    await inView(page.locator('[data-tutorial-file-card]'))
    await inView(page.locator('[data-tutorial-drop-zone]'))
    await inView(next(page))
    await dropSample(page)
    await next(page).click()
    await inView(page.locator('[data-demo-legend]'))
    // 任务标题与图之间留出空隙（演示图按内容区取框，不吃掉上留白）
    const gap = await page.evaluate(
      () => document.querySelector('[data-demo-stage]')!.getBoundingClientRect().top - document.querySelector('#tutorial-task-title')!.getBoundingClientRect().bottom,
    )
    expect(gap).toBeGreaterThanOrEqual(24)
    await inView(next(page))
    await dragLegendUp(page, -80)
    await next(page).click()
    await page.locator('[data-demo-title]').click()
    await inView(page.locator('[data-tutorial-size-increase]'))
    await page.locator('[data-tutorial-size-increase]').click()
    await inView(next(page))
    await next(page).click()
    await inView(page.locator('[data-tutorial-start-using]'))
  })
}

test('axe：欢迎页、第二步、完成页无 critical / serious', async ({ app, page }) => {
  const a = await app({ noProject: true })
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto(a.baseURL)
  const scan = async (label: string) => {
    // 换屏的淡入还在播时半透明的字会被量成对比度不足：等动画都落地再扫
    await page.evaluate(() => Promise.all(document.getAnimations().map((a) => a.finished.catch(() => undefined))))
    const res = await new AxeBuilder({ page }).include('[data-native-tutorial]').analyze()
    const bad = res.violations.filter((v) => v.impact === 'critical' || v.impact === 'serious')
    expect(bad.map((v) => `${label}: ${v.id} ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`)).toEqual([])
  }
  await expect(tut(page)).toBeVisible()
  await scan('welcome')
  await page.locator('[data-tutorial-begin]').click()
  await dropSample(page)
  await next(page).click()
  await scan('legend')
  await dragLegendUp(page)
  await next(page).click()
  await page.locator('[data-demo-title]').click()
  await page.locator('[data-tutorial-size-increase]').click()
  await next(page).click()
  await scan('done')
})

test('欢迎页：标注气泡不压标签行 / 图里文字 / 卡片边界，内容块不出滚动条且上下留白均衡', async ({ app, page }) => {
  const a = await app({ noProject: true })
  await page.goto(a.baseURL)
  await expect(tut(page)).toHaveAttribute('data-surface', 'welcome')
  for (const [w, h] of [
    [1280, 800],
    [1440, 900],
    [1600, 1000],
    [1280, 720],
  ]) {
    await page.setViewportSize({ width: w, height: h })
    await expect(page.locator('[data-welcome-callout]')).toHaveCount(3)
    const measure = () =>
      page.evaluate(() => {
        const r = (el: Element) => {
          const b = el.getBoundingClientRect()
          return { x: b.left, y: b.top, w: b.width, h: b.height }
        }
        const view = document.querySelector<HTMLElement>('[data-tutorial-view="welcome"]')!
        const card = document.querySelector('[data-welcome-callouts]')!.closest('[class*="rounded-panel"]')!
        const title = document.querySelector('[data-demo-title] text')!
        return {
          scroll: view.scrollHeight - view.clientHeight,
          card: r(card),
          tags: r(view.querySelector('main ul')!),
          title: r(title),
          begin: r(document.querySelector('[data-tutorial-begin]')!),
          logo: r(view.querySelector('main')!.firstElementChild!),
          link: r(document.querySelector('[data-tutorial-open-workspace]')!),
          vh: innerHeight,
          bubbles: [...document.querySelectorAll('[data-welcome-callout]')].map(r),
        }
      })

    // 视口刚变时图框要等一次 ResizeObserver 才跟上：轮询到稳定，而不是量到半路的一帧
    await expect
      .poll(async () => {
        const m = await measure()
        const bad: string[] = []
        const hit = (p: { x: number; y: number; w: number; h: number }, q: typeof p) =>
          p.x < q.x + q.w && q.x < p.x + p.w && p.y < q.y + q.h && q.y < p.y + p.h
        if (m.scroll !== 0) bad.push('出滚动条')
        for (const b of m.bubbles) {
          if (hit(b, m.title)) bad.push('气泡压标题')
          if (hit(b, m.tags)) bad.push('气泡压标签行')
          if (hit(b, m.begin)) bad.push('气泡压主按钮')
          if (b.x < m.card.x || b.x + b.w > m.card.x + m.card.w || b.y < m.card.y || b.y + b.h > m.card.y + m.card.h)
            bad.push('气泡探出卡片')
        }
        // 上下留白均衡：内容块（logo 顶 → 出口链接底）距窗口上沿与下沿之差不超过窗口高度的 4%
        const top = m.logo.y
        const bottom = m.vh - (m.link.y + m.link.h)
        if (Math.abs(top - bottom) > m.vh * 0.04) bad.push(`上下留白 ${top}/${bottom}`)
        return bad
      }, { message: `${w}x${h}` })
      .toEqual([])
  }
})
