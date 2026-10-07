import path from 'node:path'
import { expect, test } from './fixtures'

/**
 * 排版时间线（ADR 0101）的真浏览器闭环：
 *
 *   编辑 → 等自动节点 → 用 ⌥⌘S 命名「投稿前」→ 在「命名」视图里找到它 → 继续改 →
 *   打开预览对话框（画布不动、默认焦点在「关闭」、并排对比）→ 在对话框里恢复 →
 *   时间线里出现「恢复前」节点 → 用它恢复回来 → ⌘Z。
 *
 * jsdom 那一侧（`VersionDialog.test.tsx`）看得见「调了哪个接口」，看不见三件只有
 * 真浏览器 + 真后端才回答得了的事：节点真的落了盘、拍的缩略图真的合成得出来并且
 * 挂得上（canvas 编码 / 不被 taint）、预览对话框后面的画布真的没有被改。
 *
 * 自动节点的间隔用 `__TAVOTTO_TIMELINE_TIMING__` **注入**调小，产品默认值
 * （15 s 停顿 / 2 分钟间隔）一个字不改。
 */

const SHOTS = process.env.TAVOTTO_E2E_SHOTS

type Page = import('@playwright/test').Page
type Locator = import('@playwright/test').Locator

/**
 * 定位一律认 `data-*` 钩子，不认译文 / 可达名 / role / first()（web/AGENTS.md）；
 * 指代单例的先断言**恰好一个**再用——匹配到两个时 Playwright 的严格模式会报错，
 * 但 0 个时 `click()` 只会干等到超时，报出来的原因看不出是钩子没了。
 * 按文字找的只有**用户自己敲进去的内容**（画布上的「甲版标注」、节点名「投稿前」）：
 * 它们是被断言的数据本身，不是界面文案，换语言不变。
 */
async function only(loc: Locator, timeout?: number): Promise<Locator> {
  await expect(loc).toHaveCount(1, timeout ? { timeout } : undefined)
  return loc
}

/** 单个按钮稳定不代表展开行下面的控件也稳定；只等有限动效，不等进度脉冲。 */
async function settleInspectorMotion(page: Page) {
  await (await only(page.locator('[data-inspector-panel]'))).evaluate(async (root) => {
    const finite = root.getAnimations({ subtree: true }).filter((a) =>
      Number.isFinite(a.effect?.getComputedTiming().iterations ?? Infinity),
    )
    await Promise.all(finite.map((a) => a.finished.catch(() => undefined)))
  })
}

/** 素材卡上画布，再回到排版 */
async function placeFigureAndBack(page: Page) {
  await (await only(page.locator('[data-card="Fig1_kinetics.pdf"]'), 30_000)).dblclick()
  await (await only(page.locator('[data-context-back]'))).click()
}

/** 画布上的文字对象（不含时间线预览里画的那一份） */
const canvasText = (page: Page, text: string) =>
  page.locator('[data-canvas-stage]').getByText(text, { exact: true })

/**
 * ⌥⌘S 命名当下：顶部就地小框出现并聚焦（不开抽屉）；回车存完小框关闭
 */
async function nameNow(page: Page, name: string) {
  await page.keyboard.press('ControlOrMeta+Alt+S')
  const quick = await only(page.locator('[data-timeline-quick-name-input]'))
  await expect(quick).toBeFocused()
  await quick.fill(name)
  await quick.press('Enter')
  await expect(quick).toHaveCount(0)
}

/** 保存后通过同一项目的版本读取端点核对落盘内容，不只看发出的 POST。 */
async function nameAndReadNow(page: Page, name: string): Promise<{
  page: { w: number; h: number }
  objects: Record<string, unknown>[]
}> {
  const saving = page.waitForResponse((response) => {
    const request = response.request()
    return request.method() === 'POST' && new URL(request.url()).pathname.startsWith('/api/versions/') &&
      request.postDataJSON()?.name === name
  })
  await nameNow(page, name)
  const response = await saving
  expect(response.ok()).toBe(true)
  const result = await response.json()
  expect(typeof result.version?.id).toBe('string')
  const url = new URL(response.url())
  url.pathname += `/${encodeURIComponent(result.version.id)}`
  const saved = await page.request.get(url.toString())
  expect(saved.ok()).toBe(true)
  return (await saved.json()).doc
}

/** 窄栏仍容得下完整恢复按钮，且键盘动作确实改变保存的面板几何。 */
async function checkPanelRestoreActions(page: Page) {
  const inspector = await only(page.locator('[data-inspector-panel]'))
  const aspect = await only(inspector.locator('[data-panel-restore="aspect"]'))
  const size = await only(inspector.locator('[data-panel-restore="size"]'))
  await expect(inspector.locator('[data-panel-restore]')).toHaveCount(2)
  for (const button of [aspect, size]) {
    await expect(button).toBeVisible()
    await expect(button).toBeEnabled()
    await button.scrollIntoViewIfNeeded()
  }
  const layout = await inspector.evaluate((root) => {
    const panel = root.getBoundingClientRect()
    const buttons = [...root.querySelectorAll<HTMLElement>('[data-panel-restore]')].map((el) => {
      const r = el.getBoundingClientRect()
      const inside = (box: DOMRect) => box.left >= r.left - 1 && box.right <= r.right + 1 &&
        box.top >= r.top - 1 && box.bottom <= r.bottom + 1
      const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT)
      let textFits = true
      for (let text = walker.nextNode(); text; text = walker.nextNode()) {
        if (!text.textContent?.trim()) continue
        const range = document.createRange()
        range.selectNodeContents(text)
        if ([...range.getClientRects()].some((box) => !inside(box))) textFits = false
      }
      // 在中点与四边中点核真实命中；不拿圆角外面的矩形角点作假反例。
      const points = [
        [r.left + r.width / 2, r.top + r.height / 2],
        [r.left + 2, r.top + r.height / 2], [r.right - 2, r.top + r.height / 2],
        [r.left + r.width / 2, r.top + 2], [r.left + r.width / 2, r.bottom - 2],
      ]
      return {
        mode: el.dataset.panelRestore,
        x: r.x, y: r.y, w: r.width, h: r.height,
        inPanel: r.left >= panel.left && r.right <= panel.right && r.top >= panel.top && r.bottom <= panel.bottom,
        textFits,
        hittable: points.every(([x, y]) => el.contains(document.elementFromPoint(x, y))),
        tabIndex: el.tabIndex,
      }
    })
    return buttons
  })
  expect(layout.map((button) => button.mode)).toEqual(['aspect', 'size'])
  for (const button of layout) {
    expect(button.inPanel, JSON.stringify(layout)).toBe(true)
    expect(button.textFits, JSON.stringify(layout)).toBe(true)
    expect(button.hittable, JSON.stringify(layout)).toBe(true)
    expect(button.tabIndex).toBeGreaterThanOrEqual(0)
  }
  const [a, b] = layout
  const overlaps = Math.min(a.x + a.w, b.x + b.w) > Math.max(a.x, b.x) &&
    Math.min(a.y + a.h, b.y + b.h) > Math.max(a.y, b.y)
  expect(overlaps, JSON.stringify(layout)).toBe(false)

  // 边柄默认只改宽（角柄才跟宽高比锁），先把两个恢复动作的非空前提摆出来。
  const east = await only(page.locator('[data-canvas-stage] [data-handle="e"]'))
  const handle = (await east.boundingBox())!
  const x = handle.x + handle.width / 2
  const y = handle.y + handle.height / 2
  await page.mouse.move(x, y)
  await page.mouse.down()
  try {
    await page.mouse.move(x + 36, y, { steps: 6 })
  } finally {
    await page.mouse.up()
  }
  const distorted = await nameAndReadNow(page, 'restore-before')
  expect(distorted.objects).toHaveLength(1)
  const before = distorted.objects[0]
  expect(before.type).toBe('panel')
  expect(before.crop).toBeUndefined()
  expect(before.rotation ?? 0).toBe(0)
  const nw = Number(before.nativeW)
  const nh = Number(before.nativeH)
  expect(Math.abs(Number(before.w) / Number(before.h) - nw / nh)).toBeGreaterThan(0.05)
  expect(Math.abs(Number(before.w) - nw)).toBeGreaterThan(1)

  // focus 只设 Tab 流的起点，目标按钮必须由真正的键盘导航到达并执行。
  await size.focus()
  await page.keyboard.press('Shift+Tab')
  await expect(aspect).toBeFocused()
  await page.keyboard.press('Enter')
  const aspectDoc = await nameAndReadNow(page, 'restore-aspect')
  expect(aspectDoc.objects).toHaveLength(1)
  const restoredAspect = aspectDoc.objects[0]
  expect(restoredAspect.id).toBe(before.id)
  expect(Number(restoredAspect.w)).toBeCloseTo(Number(before.w), 8)
  expect(Number(restoredAspect.h)).toBeCloseTo(Number(before.w) * nh / nw, 8)
  expect(Math.abs(Number(restoredAspect.w) - nw)).toBeGreaterThan(1)

  await aspect.focus()
  await page.keyboard.press('Tab')
  await expect(size).toBeFocused()
  await page.keyboard.press('Enter')
  const sizeDoc = await nameAndReadNow(page, 'restore-size')
  expect(sizeDoc.objects).toHaveLength(1)
  const restoredSize = sizeDoc.objects[0]
  expect(restoredSize.id).toBe(before.id)
  expect(Number(restoredSize.w)).toBeCloseTo(nw, 8)
  expect(Number(restoredSize.h)).toBeCloseTo(nh, 8)
  console.log(`inspector-restore-actions ${JSON.stringify({ layout, before, restoredAspect, restoredSize })}`)
}

async function addText(page: Page, text: string, x: number, y: number) {
  await (await only(page.locator('[data-tool="text"]'))).click()
  await (await only(page.locator('[data-canvas-stage]'))).click({ position: { x, y } })
  await page.keyboard.type(text)
  await page.keyboard.press('Escape')
  await expect(canvasText(page, text)).toBeVisible()
}

/**
 * 预览对话框里「怎么看」的分段控件，与**实际画出来的**是不是同一个模式。
 *
 * 实际模式从内容推，不从控件读（从控件读就是自己验自己）：两格画框 = 并排，有当前描边 =
 * 叠加，否则 = 这一刻。然后两条都要对上：`aria-checked` 那一格就是它；选中底（滑动的
 * `data-segmented-thumb`）落定之后的中心落在那一格里——高亮停在别的格上时，用户看到的
 * 「选中了哪个」与画面不一致。
 */
async function expectViewMatches(preview: Locator) {
  const shown = async () => {
    const frames = await preview.locator('[data-timeline-frame]').count()
    if (frames === 2) return 'side'
    if (await preview.locator('[data-timeline-overlay-current]').count()) return 'overlay'
    return 'moment'
  }
  const mode = await shown()
  const group = preview.locator('[data-timeline-view]')
  // aria-checked 在这里是被断言的**状态**，不是定位手段：先按 data-value 找到那一格
  await expect(await only(group.locator(`[data-value="${mode}"]`))).toHaveAttribute('aria-checked', 'true')
  await expect(group.locator('[data-value][aria-checked="true"]')).toHaveCount(1)
  await expect
    .poll(async () => {
      const thumb = await group.locator('[data-segmented-thumb]').boundingBox()
      const cell = await group.locator(`[data-value="${mode}"]`).boundingBox()
      if (!thumb || !cell) return 'missing'
      const cx = thumb.x + thumb.width / 2
      return cx >= cell.x && cx <= cell.x + cell.width ? 'aligned' : `thumb@${Math.round(cx)} cell@${Math.round(cell.x)}`
    })
    .toBe('aligned')
  return mode
}

test('排版时间线：自动节点 → 命名 → 预览不改排版 → 恢复 → 用「恢复前」恢复回来', async ({
  app,
  page,
}) => {
  test.setTimeout(240_000)
  await page.addInitScript(() => {
    ;(window as unknown as Record<string, unknown>).__TAVOTTO_TIMELINE_TIMING__ = {
      debounceMs: 300,
      minGapMs: 800,
    }
    // 缩略图合成的诊断：每个面板走了哪条图源、画没画上（`lib/timelineThumb` 的 trace）
    ;(window as unknown as Record<string, unknown>).__TAVOTTO_THUMB_TRACE__ = []
  })
  const a = await app()
  await page.goto(a.baseURL)

  // 面板上画布，回到排版
  await placeFigureAndBack(page)
  await addText(page, '甲版标注', 420, 240)

  // ── 顶栏时钟钮打开时间线；等第一个自动节点 ──────────────────────────
  const clock = await only(page.locator('[data-timeline-button]'))
  await clock.click()
  const drawer = await only(page.locator('[data-timeline-drawer]'))
  await expect(drawer).toBeVisible()
  const nodes = drawer.locator('[data-timeline-node]')
  await expect.poll(() => nodes.count(), { timeout: 30_000 }).toBeGreaterThan(0)
  await expect(drawer.locator('[data-timeline-kind="auto"]')).not.toHaveCount(0)

  // ── ⌥⌘S 命名「投稿前」：就地小框，不打开抽屉 ──────────────────────
  await clock.click() // 先收起抽屉：命名不该依赖它先开着
  await expect(drawer).toHaveCount(0)
  await page.keyboard.press('ControlOrMeta+Alt+S')
  const quick = await only(page.locator('[data-timeline-quick-name-input]'))
  await expect(quick).toBeFocused()
  await expect(drawer).toHaveCount(0)
  // 小框在视口里、在顶部居中，不出屏
  const box = await only(page.locator('[data-timeline-quick-name]'))
  const bb = (await box.boundingBox())!
  const vp = page.viewportSize()!
  expect(bb.x).toBeGreaterThanOrEqual(0)
  expect(bb.x + bb.width).toBeLessThanOrEqual(vp.width)
  await quick.fill('投稿前')
  if (SHOTS) await page.screenshot({ path: path.join(SHOTS, 'timeline-quick-name.png') })
  await quick.press('Enter')
  await expect(box).toHaveCount(0)
  // Esc 关闭、点外面关闭
  await page.keyboard.press('ControlOrMeta+Alt+S')
  await expect(page.locator('[data-timeline-quick-name-input]')).toBeFocused()
  await page.keyboard.press('Escape')
  await expect(box).toHaveCount(0)
  // 居中于工作面板自己（不是整行）：只开左栏、只开属性栏两种布局下水平中心差 ≤ 2px
  const panel = await only(page.locator('[data-work-panel]'))
  const expectCentered = async () => {
    await page.keyboard.press('ControlOrMeta+Alt+S')
    const q = await only(page.locator('[data-timeline-quick-name]'))
    await expect(page.locator('[data-timeline-quick-name-input]')).toBeFocused()
    // aria-expanded 已切换时，侧栏的宽度动画仍可能在跑。两次 boundingBox
    // 跨帧会把不同布局相减：同一次求值读两份几何，并等连续两次采样落定。
    let previous = ''
    await expect
      .poll(async () => {
        const geometry = await panel.evaluate((el) => {
          const quick = document.querySelector('[data-timeline-quick-name]')
          if (!quick) return null
          const b = quick.getBoundingClientRect()
          const p = el.getBoundingClientRect()
          return { boxX: b.x, boxWidth: b.width, panelX: p.x, panelWidth: p.width }
        })
        const current = JSON.stringify(geometry)
        const settled = current === previous
        previous = current
        if (!geometry || !settled) return Infinity
        return Math.abs(geometry.boxX + geometry.boxWidth / 2 - (geometry.panelX + geometry.panelWidth / 2))
      })
      .toBeLessThanOrEqual(2)
    await page.keyboard.press('Escape')
    await expect(q).toHaveCount(0)
  }
  const leftOpen = page.locator('[data-rail][aria-expanded="true"]')
  const inspectorClose = page.locator('[data-inspector-close]')
  // 只开左栏：收起属性栏
  if ((await leftOpen.count()) === 0) await (await only(page.locator('[data-rail="elements"]'))).click()
  await expect(leftOpen.first()).toBeVisible()
  if ((await inspectorClose.count()) > 0) await inspectorClose.click()
  await expect(inspectorClose).toHaveCount(0)
  await expectCentered()
  // 只开属性栏：选中文字打开属性栏，收起左栏
  await page.keyboard.press('Escape') // 先取消选择，再点文字才会重新打开属性栏
  await canvasText(page, '甲版标注').click()
  await expect(inspectorClose).toHaveCount(1)
  await leftOpen.first().click()
  await expect(leftOpen).toHaveCount(0)
  await expectCentered()
  // 抽屉里的按钮照旧：点开展开，Esc 只收输入
  await clock.click()
  await expect(drawer).toBeVisible()
  await (await only(drawer.locator('[data-timeline-name-open]'))).click()
  const again = await only(drawer.locator('[data-timeline-name-input]'))
  await expect(again).toBeFocused()
  await again.press('Escape')
  await expect(again).toHaveCount(0)
  await expect(drawer).toBeVisible()
  if (SHOTS) await page.screenshot({ path: path.join(SHOTS, 'timeline-name-collapsed.png') })

  // ── 「命名」视图里找得到它（抽屉一直开着）──────────────────────────
  await (await only(drawer.locator('[data-timeline-filter] [data-value="named"]'))).click()
  // 命名节点只有这一个；名字是用户起的，按名字核对的是数据，不是定位
  const named = await only(drawer.locator('[data-timeline-node][data-timeline-named]'))
  await expect(named.locator('[data-timeline-name]')).toHaveText('投稿前')
  await expect(named).toBeVisible()
  // 命名视图里只有命名节点
  await expect(drawer.locator('[data-timeline-node]:not([data-timeline-named])')).toHaveCount(0)
  // 缩略图是拍那一刻在浏览器里合成、单独 PUT 上去的：真浏览器里它得挂得上
  await expect(named.locator('img[data-timeline-thumb]')).toBeVisible({ timeout: 30_000 })
  // 不只是「挂上了一张图」：图里真的画进了面板（不是一张白纸）。数深色像素——
  // 白底 + 文字几个像素远不到这个量，面板那条曲线与坐标轴才到得了
  const inked = await named.locator('img[data-timeline-thumb]').evaluate(async (img: HTMLImageElement) => {
    await img.decode().catch(() => {})
    if (!img.naturalWidth) return -1
    const c = document.createElement('canvas')
    c.width = img.naturalWidth
    c.height = img.naturalHeight
    const ctx = c.getContext('2d')!
    ctx.drawImage(img, 0, 0)
    const px = ctx.getImageData(0, 0, c.width, c.height).data
    let dark = 0
    for (let i = 0; i < px.length; i += 4) if (px[i] + px[i + 1] + px[i + 2] < 600) dark++
    return dark
  })
  // 没画上面板时，把每次合成走了哪条图源带进失败信息（Windows 的 WebKit 上量到过只剩文字）
  const thumbTrace = await page.evaluate(
    () => (window as unknown as Record<string, unknown>).__TAVOTTO_THUMB_TRACE__,
  )
  expect(inked, JSON.stringify(thumbTrace)).toBeGreaterThan(200)
  if (SHOTS) await page.screenshot({ path: path.join(SHOTS, 'timeline-named-view.png') })

  // ── 继续改：再加一段文字 ───────────────────────────────────────────
  await clock.click() // 收起抽屉，腾出画布
  await expect(drawer).toHaveCount(0)
  await addText(page, '乙版标注', 420, 320)
  await expect(canvasText(page, '甲版标注')).toBeVisible()

  // ── 预览「投稿前」：模态对话框，画布一个字都不动 ────────────────────
  await page.keyboard.press('ControlOrMeta+Shift+H')
  await expect(drawer).toBeVisible()
  await (await only(drawer.locator('[data-timeline-filter] [data-value="named"]'))).click()
  await (await only(named.locator('[data-timeline-preview-button]'))).click()
  const preview = await only(page.locator('[data-dialog="timeline-preview"]'))
  await expect(preview).toBeVisible()
  await expect(preview).toContainText('投稿前')
  // 默认焦点在「关闭」：回车不会误触恢复
  await expect(await only(preview.locator('[data-timeline-preview-close]'))).toBeFocused()
  // 对话框里是那一刻：有甲、没有乙
  await expect(preview.getByText('甲版标注', { exact: true })).toBeVisible()
  await expect(preview.getByText('乙版标注', { exact: true })).toHaveCount(0)
  // 遮罩后面的真画布原样：乙还在
  await expect(canvasText(page, '乙版标注')).toHaveCount(1)
  expect(await expectViewMatches(preview)).toBe('moment')
  if (SHOTS) await page.screenshot({ path: path.join(SHOTS, 'timeline-preview-dialog.png') })
  // 与当前对比：并排时两格，当前那一格里有乙
  await (await only(preview.locator('[data-timeline-view] [data-value="side"]'))).click()
  await expect(preview.getByText('乙版标注', { exact: true })).toBeVisible()
  expect(await expectViewMatches(preview)).toBe('side')
  if (SHOTS) await page.screenshot({ path: path.join(SHOTS, 'timeline-preview-side.png') })
  await (await only(preview.locator('[data-timeline-view] [data-value="overlay"]'))).click()
  await expect(preview.locator('[data-timeline-overlay-current]')).toHaveCount(1)
  expect(await expectViewMatches(preview)).toBe('overlay')
  if (SHOTS) await page.screenshot({ path: path.join(SHOTS, 'timeline-preview-overlay.png') })
  await expect(canvasText(page, '乙版标注')).toHaveCount(1)

  // ── 在对话框里恢复 ──────────────────────────────────────────────────
  await (await only(preview.locator('[data-timeline-preview-restore]'))).click()
  await expect(preview).toHaveCount(0)
  await expect(canvasText(page, '乙版标注')).toHaveCount(0)
  await expect(canvasText(page, '甲版标注')).toBeVisible()

  // ── 时间线里出现「恢复前」节点；用它恢复回来 ──────────────────────
  await (await only(drawer.locator('[data-timeline-filter] [data-value="all"]'))).click()
  const before = drawer.locator('[data-timeline-node]', {
    has: page.locator('[data-timeline-moment="before_restore"]'),
  })
  await expect(before).toHaveCount(1, { timeout: 15_000 })
  if (SHOTS) await page.screenshot({ path: path.join(SHOTS, 'timeline-before-restore.png') })
  await (await only(before.locator('[data-timeline-preview-button]'))).click()
  await expect(preview).toBeVisible()
  await expect(preview.getByText('乙版标注', { exact: true })).toBeVisible()
  await (await only(preview.locator('[data-timeline-preview-restore]'))).click()
  await expect(canvasText(page, '乙版标注')).toBeVisible()
  // 状态条说了「已恢复」：认消息键，不认译文
  await expect(await only(page.locator('[data-status-live]'))).toHaveAttribute(
    'data-status-key',
    'versions.restored',
  )

  // 恢复是一条历史：⌘Z 一步退回
  await page.keyboard.press('Escape') // 焦点离开抽屉里的按钮，交还全局快捷键
  await page.locator('[data-canvas-stage]').click({ position: { x: 40, y: 40 } })
  await page.keyboard.press('ControlOrMeta+z')
  await expect(canvasText(page, '乙版标注')).toHaveCount(0)

  // ── 重开（启动时恢复上次的项目）：时间线里多一个「打开项目」节点 ──────────
  // Workspace 挂上、文档恢复完之后才打这个点；在 projectStore 里打的那一下发生在
  // 时间线开始跑之前，是空的（Codex #679 P2）
  await expect(await only(page.locator('[data-topbar] [data-save-state]'))).toBeVisible()
  await page.waitForTimeout(1500) // 让撤销之后那次自动保存落盘
  await page.reload()
  await expect(canvasText(page, '甲版标注')).toBeVisible({ timeout: 30_000 })
  await page.keyboard.press('ControlOrMeta+Shift+H')
  await expect(drawer).toBeVisible()
  // 两个「打开」：第一次启动那一次（那时画布还是空的——关键时刻空画布也打，Codex #679）
  // 与这次重开
  await expect(drawer.locator('[data-timeline-moment="open"]')).toHaveCount(2, { timeout: 15_000 })
})

test('两张不透明、内容相同的面板完全重叠：上层不被误判成「白画了」（缩略图合成，Codex #679）', async ({
  app,
  page,
}) => {
  await page.addInitScript(() => {
    ;(window as unknown as Record<string, unknown>).__TAVOTTO_THUMB_TRACE__ = []
  })
  const a = await app()
  await page.goto(a.baseURL)
  // 放一张图，⌘D 复制一张（副本偏移 4 mm），再用方向键推回原位（每下 0.5 mm）：两张完全重叠
  await placeFigureAndBack(page)
  await page.keyboard.press('ControlOrMeta+d')
  for (let i = 0; i < 8; i++) await page.keyboard.press('ArrowLeft')
  for (let i = 0; i < 8; i++) await page.keyboard.press('ArrowUp')
  const panels = page.locator('[data-canvas-stage] [data-object-id^="p_"]')
  await expect(panels).toHaveCount(2)
  await expect
    .poll(async () => {
      const [b0, b1] = await Promise.all((await panels.all()).map((p) => p.boundingBox()))
      return JSON.stringify(b0) === JSON.stringify(b1)
    })
    .toBe(true) // 前提：真的完全重叠（判据的主语）
  // 存一个命名节点：合成它的缩略图
  await nameNow(page, '重叠')
  // 每一次面板合成都画上了（没有一步是 blank——上层画上去主画布不变，旧判据会判成白画）
  await expect
    .poll(async () => {
      const t = (await page.evaluate(
        () => (window as unknown as Record<string, unknown>).__TAVOTTO_THUMB_TRACE__,
      )) as { steps?: string[] }[]
      return t.filter((e) => e.steps).length >= 2 ? JSON.stringify(t) : 'pending'
    })
    .not.toBe('pending')
  const trace = (await page.evaluate(
    () => (window as unknown as Record<string, unknown>).__TAVOTTO_THUMB_TRACE__,
  )) as { panel: string; steps?: string[] }[]
  const composed = trace.filter((e) => e.steps)
  expect(composed.length, JSON.stringify(trace)).toBeGreaterThanOrEqual(2)
  for (const e of composed) {
    expect(e.steps!.some((st) => st.endsWith(':blank')), JSON.stringify(trace)).toBe(false)
    expect(e.steps!.at(-1), JSON.stringify(trace)).toMatch(/:ok$/)
  }
})

test('带 overrides 的面板：缩略图走 SVG 那一路画上（不因重复属性被拒、退回素材图，Codex #679）', async ({
  app,
  page,
}) => {
  test.setTimeout(240_000)
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.addInitScript(() => {
    ;(window as unknown as Record<string, unknown>).__TAVOTTO_THUMB_TRACE__ = []
  })
  const a = await app()
  await page.goto(a.baseURL)
  // 进图内编辑，给图例第一项改个字号：面板从此带着 overrides，renderStore 里是带改动的 SVG
  await (await only(page.locator('[data-card="Fig1_kinetics.pdf"]'), 30_000)).dblclick()
  await expect(page.locator('[data-element-svg] svg').first()).toBeVisible({ timeout: 60_000 })
  await expect(page.locator('[data-authority="ready"]').first()).toBeVisible({ timeout: 60_000 })
  const gid = 'axes_0.legend.texts_0'
  const c = await page.evaluate((id) => {
    const n = document.querySelector(`[data-element-svg] svg [id="${id}"]`)
    if (!n) return null
    const r = (n as SVGGraphicsElement).getBoundingClientRect()
    return { x: r.x + r.width / 2, y: r.y + r.height / 2 }
  }, gid)
  expect(c).not.toBeNull()
  await page.mouse.click(c!.x, c!.y)
  // 右栏属性页的字号（`data-inspector-prop="fontsize"`；浮动栏里那份是 sizePt）
  const size = await only(page.locator('[data-inspector-prop="fontsize"]'))
  const was = Number(await size.inputValue())
  await size.fill(String(was + 3))
  await size.press('Enter')
  await expect(page.locator('[data-authority="ready"]').first()).toBeVisible({ timeout: 60_000 })
  await (await only(page.locator('[data-context-back]'))).click()
  // 存一个命名节点：它的缩略图要画带改动的那份 SVG
  await nameNow(page, '改过字号')
  const trace = async () =>
    (await page.evaluate(() => (window as unknown as Record<string, unknown>).__TAVOTTO_THUMB_TRACE__)) as {
      steps?: string[]
    }[]
  // 带 SVG 的那几次合成：SVG 那一路本身就画上了（`svg:ok`），不是载入失败后退回素材图
  await expect
    .poll(async () => (await trace()).filter((e) => e.steps?.some((st) => st.startsWith('svg'))).length, {
      timeout: 30_000,
    })
    .toBeGreaterThan(0)
  const withSvg = (await trace()).filter((e) => e.steps?.some((st) => st.startsWith('svg')))
  for (const e of withSvg) expect(e.steps, JSON.stringify(withSvg)).toEqual(['svg:ok'])
})

test('首次渲染在水平翻转按下与松开间完成：控件不挪动，保存保留翻转', async ({
  app,
  page,
}) => {
  test.setTimeout(240_000)
  await page.setViewportSize({ width: 1440, height: 900 })
  // 首次渲染的回包精确夹在按下与松开之间：原来冷启动提示消失会把下方控件
  // 上移 21px，mouseup 落到透明度行，原生 click 根本没有提交水平翻转。
  let held: import('@playwright/test').Route | undefined
  await page.route('**/api/engine/render**', async (route) => {
    if (!held) held = route
    else await route.continue()
  })
  const a = await app()
  await page.goto(a.baseURL)
  // 首次渲染仍在途时回到排版：这条只量原生点击和保存状态。
  await placeFigureAndBack(page)
  await expect.poll(() => !!held).toBe(true)
  await nameNow(page, '冷启动中')
  // 属性页「更多」里的「水平翻转」（面板放上来之后是选中的）
  const more = await only(page.locator('[data-panel-more]'))
  if ((await more.getAttribute('aria-expanded')) !== 'true') await more.click()
  const flip = await only(page.locator('[data-panel-flip="h"]'))
  await flip.click({ trial: true }) // 滚到可操作位置；不发 pointerdown / click
  await settleInspectorMotion(page)
  const controls = () => page.locator('[data-inspector-panel]').evaluate((root) =>
    [...root.querySelectorAll('button, input')].map((el) => {
      const r = el.getBoundingClientRect()
      return { tag: el.tagName, x: r.x, y: r.y, w: r.width, h: r.height }
    }),
  )
  await expect(page.locator('[data-panel-engine-progress]:visible')).toHaveCount(1)
  const beforeControls = await controls()
  const box = (await flip.boundingBox())!
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2)
  await page.mouse.down()
  let afterControls: Awaited<ReturnType<typeof controls>>
  try {
    await held!.continue()
    await expect(page.locator('[data-canvas-stage] [data-display="exact"]')).toHaveCount(1, { timeout: 60_000 })
    // SSE 的 building[fileId] 与 HTTP 渲染响应分开抵达；两者都落定才真的撤下提示。
    await expect(page.locator('[data-panel-engine-progress]:visible')).toHaveCount(0)
    // React 的派生尺寸与提示一并落地后再松手，量实际按钮 / 输入框位置，不量 class。
    await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))))
    afterControls = await controls()
  } finally {
    await page.mouse.up()
  }
  expect.soft(afterControls!, '渲染就绪不应把正在操作的属性控件挪走').toEqual(beforeControls)
  const savedDoc = await nameAndReadNow(page, '翻转')
  expect(savedDoc.objects).toHaveLength(1)
  expect(savedDoc.objects[0].flipH, '原生点击必须真正写进命名节点的文档').toBe(true)
  await expect(flip).toHaveAttribute('data-active', 'true')
})

test('同一 SVG、同一几何只差水平翻转：节点缩略图是左右镜像', async ({ app, page }) => {
  test.setTimeout(240_000)
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.addInitScript(() => {
    ;(window as unknown as Record<string, unknown>).__TAVOTTO_THUMB_TRACE__ = []
  })
  let renderRequests = 0
  page.on('request', (request) => {
    if (request.method() === 'POST' && new URL(request.url()).pathname === '/api/engine/render') renderRequests++
  })
  const a = await app()
  await page.goto(a.baseURL)
  await placeFigureAndBack(page)
  const display = await only(page.locator('[data-canvas-stage] [data-display]'))
  await expect(display).toHaveAttribute('data-display', 'exact', { timeout: 60_000 })
  await expect(page.locator('[data-panel-engine-progress]:visible')).toHaveCount(0)
  await settleInspectorMotion(page)
  await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))))
  // 素材 PDF 与本机重建 SVG 的字体 / tight 图幅可能不同。先完成原生尺寸同步，
  // 再通过真正的「相对画布水平居中」动作落位，两次快照才只差 flipH。
  await (await only(page.locator('[data-single-align] [data-inspector-align-mode="hcenter"]'))).click()
  await page.evaluate(() => {
    ;(window as unknown as Record<string, unknown>).__TAVOTTO_THUMB_TRACE__ = []
  })
  const sourceRequests = renderRequests
  const sourceKey = await display.getAttribute('data-display-key')
  expect(sourceKey).not.toBeNull()
  const plainDoc = await nameAndReadNow(page, '原样')
  expect(plainDoc.objects).toHaveLength(1)
  const plainPanel = plainDoc.objects[0]
  expect(plainPanel.flipH ?? false).toBe(false)
  expect(Number(plainPanel.x) + Number(plainPanel.w) / 2).toBeCloseTo(plainDoc.page.w / 2, 8)
  const more = await only(page.locator('[data-panel-more]'))
  if ((await more.getAttribute('aria-expanded')) !== 'true') await more.click()
  await settleInspectorMotion(page)
  const flip = await only(page.locator('[data-panel-flip="h"]'))
  await flip.click()
  const flippedDoc = await nameAndReadNow(page, '翻转')
  expect(flippedDoc.page).toEqual(plainDoc.page)
  expect(flippedDoc.objects).toEqual([{ ...plainPanel, flipH: true }])
  await expect(flip).toHaveAttribute('data-active', 'true')
  await expect(display).toHaveAttribute('data-display-key', sourceKey!)
  await page.keyboard.press('ControlOrMeta+Shift+H')
  const drawer = await only(page.locator('[data-timeline-drawer]'))
  const thumbOf = (name: string) =>
    drawer.locator('[data-timeline-node][data-timeline-named]', {
      has: page.locator('[data-timeline-name]', { hasText: name }),
    }).locator('img[data-timeline-thumb]')
  await expect(await only(thumbOf('原样'), 30_000)).toBeVisible({ timeout: 30_000 })
  await expect(await only(thumbOf('翻转'), 30_000)).toBeVisible({ timeout: 30_000 })
  const [plainSrc, flippedSrc] = [
    await thumbOf('原样').getAttribute('src'),
    await thumbOf('翻转').getAttribute('src'),
  ]
  // 判据只量同一图源、同一居中几何的翻转。冷启动前素材 PDF → 本机 SVG 的
  // 比较还同时改变字形与原生图幅，不满足这条相关性门槛的前提。
  expect(renderRequests, '两个命名节点之间不应有另一份引擎产物').toBe(sourceRequests)
  const trace = (await page.evaluate(
    () => (window as unknown as Record<string, unknown>).__TAVOTTO_THUMB_TRACE__,
  )) as { steps?: string[] }[]
  const composed = trace.filter((entry) => entry.steps)
  expect(composed.length).toBeGreaterThanOrEqual(2)
  for (const entry of composed) expect(entry.steps, JSON.stringify(trace)).toEqual(['svg:ok'])
  const m = await page.evaluate(
    async ([p, f]) => {
      const load = async (src: string) => {
        const img = new Image()
        img.src = src
        await img.decode()
        const c = document.createElement('canvas')
        c.width = img.naturalWidth
        c.height = img.naturalHeight
        const ctx = c.getContext('2d')!
        ctx.drawImage(img, 0, 0)
        return { data: ctx.getImageData(0, 0, c.width, c.height).data, w: c.width, h: c.height }
      }
      const A = await load(p!)
      const B = await load(f!)
      const profile = (x: { data: Uint8ClampedArray; w: number; h: number }) => {
        const cols = new Array<number>(x.w).fill(0)
        for (let y = 0; y < x.h; y++)
          for (let c = 0; c < x.w; c++) {
            const i = (y * x.w + c) * 4
            cols[c] += 765 - (x.data[i] + x.data[i + 1] + x.data[i + 2])
          }
        return cols
      }
      const corr = (u: number[], v: number[]) => {
        const n = u.length
        const mu = u.reduce((s, x) => s + x, 0) / n
        const mv = v.reduce((s, x) => s + x, 0) / n
        let num = 0
        let du = 0
        let dv = 0
        for (let i = 0; i < n; i++) {
          num += (u[i] - mu) * (v[i] - mv)
          du += (u[i] - mu) ** 2
          dv += (v[i] - mv) ** 2
        }
        return num / Math.sqrt(du * dv)
      }
      let plain = 0
      for (let i = 0; i < A.data.length; i += 4)
        for (let k = 0; k < 3; k++) plain += Math.abs(A.data[i + k] - B.data[i + k])
      const pa = profile(A)
      const pb = profile(B)
      // 面板在页面上的左右位置（有墨的列的中点）：居中才能拿整张图的镜像当面板的镜像
      const inked = pa.map((v, i) => (v > 0 ? i : -1)).filter((i) => i >= 0)
      const center = (inked[0] + inked[inked.length - 1]) / 2
      return {
        plain,
        mirrored: corr(pb, [...pa].reverse()),
        unmirrored: corr(pb, pa),
        center,
        size: [A.w, A.h, B.w, B.h],
      }
    },
    [plainSrc, flippedSrc],
  )
  const why = JSON.stringify({
    m,
    trace: await page.evaluate(() => (window as unknown as Record<string, unknown>).__TAVOTTO_THUMB_TRACE__),
  })
  expect(m.size[0], why).toBe(m.size[2])
  expect(Math.abs(m.center - m.size[0] / 2), why).toBeLessThan(3) // 前提：面板在缩略图里左右居中
  expect(m.plain, why).toBeGreaterThan(50_000) // 两张确实不一样
  expect(m.mirrored, why).toBeGreaterThan(0.85) // 翻转那张的左右分布 = 原样的倒过来
  expect(m.mirrored - m.unmirrored, why).toBeGreaterThan(0.3) // 而不是原样本身
})

// 协议驱动状态，真实浏览器量布局：同一段文案的中英文换行、两档合法栏宽都要量。
// 只替换 SSE 的时序，不碰 documentStore / renderStore，也不注入第二份状态判据。
for (const locale of ['zh-CN', 'en-US']) {
  for (const width of [360, 320]) {
    test(`属性栏引擎提示不挪动控件：冷 / 热 / stale / ready (${locale}, ${width}px)`, async ({ app, page }) => {
      test.setTimeout(180_000)
      await page.setViewportSize({ width: 1440, height: 1000 })
      await page.addInitScript(({ locale, width }) => {
        localStorage.setItem('tavotto.locale', locale)
        localStorage.setItem('tavotto.ui', JSON.stringify({ prefsVersion: 2, rightWidth: width }))
        const sources: EventTarget[] = []
        class TestEvents extends EventTarget {
          readonly url: string
          readyState = 1
          constructor(url: string) { super(); this.url = url; sources.push(this) }
          close() { this.readyState = 2 }
        }
        window.EventSource = TestEvents as unknown as typeof EventSource
        ;(window as unknown as Record<string, unknown>).__TAVOTTO_TEST_EVENT__ = (kind: string, data: unknown) => {
          for (const source of sources) source.dispatchEvent(new MessageEvent(kind, { data: JSON.stringify(data) }))
        }
      }, { locale, width })
      const held: import('@playwright/test').Route[] = []
      const started = Date.now()
      const requests = new Map<import('@playwright/test').Request, {
        heldAt: number; releasedAt?: number; status?: number; failed?: string
      }>()
      await page.route('**/api/engine/render**', (route) => {
        held.push(route)
        requests.set(route.request(), { heldAt: Date.now() - started })
      })
      page.on('requestfailed', (request) => {
        const record = requests.get(request)
        const reason = request.failure()?.errorText ?? ''
        if (record) record.failed = /^net::[A-Z_]+$/.test(reason) ? reason : 'requestfailed'
      })
      page.on('response', (response) => {
        const record = requests.get(response.request())
        if (record) record.status = response.status()
      })
      const releaseRender = async (index: number) => {
        requests.get(held[index].request())!.releasedAt = Date.now() - started
        await held[index].continue()
      }
      const expectExact = async () => {
        try {
          await expect(page.locator('[data-canvas-stage] [data-display="exact"]')).toHaveCount(1, { timeout: 60_000 })
        } catch (error) {
          const displays = await page.locator('[data-canvas-stage] [data-display]').evaluateAll((nodes) =>
            nodes.map((node) => node.getAttribute('data-display')),
          ).catch(() => [])
          console.error('inspector-render-readiness', JSON.stringify({
            locale, width, held: held.length, requests: [...requests.values()].slice(-16), displays,
          }))
          throw error
        }
      }
      const emit = (kind: string, data: Record<string, unknown>) => page.evaluate(({ kind, data }) => {
        const send = (window as unknown as { __TAVOTTO_TEST_EVENT__: (kind: string, data: unknown) => void }).__TAVOTTO_TEST_EVENT__
        send(kind, data)
      }, { kind, data })
      const a = await app()
      await page.goto(a.baseURL)
      await placeFigureAndBack(page)
      await expect.poll(() => held.length).toBe(1)
      const more = await only(page.locator('[data-panel-more]'))
      if ((await more.getAttribute('aria-expanded')) !== 'true') await more.click()
      await (await only(page.locator('[data-panel-flip="h"]'))).click({ trial: true })
      await settleInspectorMotion(page)
      const inspector = await only(page.locator('[data-inspector-panel]'))
      expect((await inspector.boundingBox())!.width).toBe(width)
      const measure = () => inspector.evaluate((root) => {
        const rect = (el: Element) => {
          const r = el.getBoundingClientRect()
          return { x: r.x, y: r.y, w: r.width, h: r.height }
        }
        const status = root.querySelector('[data-panel-engine-status]')!
        return { status: rect(status), controls: [...root.querySelectorAll('button, input')].map(rect) }
      })
      const baseline = await measure()
      const measurements: { state: string; geometry: Awaited<ReturnType<typeof measure>> }[] = []
      const check = async (state: string, visible: string[]) => {
        await expect.poll(() => inspector.locator('[data-panel-engine-message]:visible').evaluateAll((nodes) =>
          nodes.map((el) => (el as HTMLElement).dataset.panelEngineMessage),
        )).toEqual(visible)
        const geometry = await measure()
        expect(geometry, state).toEqual(baseline)
        measurements.push({ state, geometry })
      }
      await check('warm', ['building'])
      await emit('render.started', { id: 'Fig1_kinetics.pdf', cold: true, cost: 'light' })
      await check('cold', ['cold'])
      await emit('render.started', { id: 'Fig1_kinetics.pdf', cold: false, cost: 'light' })
      await check('warm again', ['building'])
      await releaseRender(0)
      await expectExact()
      await emit('render.done', { id: 'Fig1_kinetics.pdf' })
      await check('ready', [])
      await emit('panel.file_changed', { stems: ['Fig1_kinetics'], reason: 'watcher' })
      await expect.poll(() => held.length).toBe(2)
      await check('stale while rebuilding', ['building', 'stale'])
      await emit('render.started', { id: 'Fig1_kinetics.pdf', cold: true, cost: 'light' })
      await check('cold and stale', ['cold', 'stale'])
      await releaseRender(1)
      await expectExact()
      await emit('render.done', { id: 'Fig1_kinetics.pdf' })
      await check('rebuilt', [])
      // 留下真实高度而不是把预测值写成通过；CI 日志在成功时也能取回这些数字。
      console.log(`inspector-engine-layout ${locale} ${width}px ${JSON.stringify(measurements)}`)
      const { horizontalOffenders } = await import('./overflow')
      expect(await horizontalOffenders(page, '[data-inspector-panel]')).toEqual([])
      await checkPanelRestoreActions(page)
      expect(await horizontalOffenders(page, '[data-inspector-panel]')).toEqual([])
    })
  }
}
