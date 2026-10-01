import path from 'node:path'
import { expect, test } from './fixtures'

/**
 * 排版时间线（ADR 0101）的真浏览器闭环：
 *
 *   编辑 → 等自动节点 → 用顶栏书签钮命名「投稿前」→ 在「命名」视图里找到它 → 继续改 →
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

/** 素材卡上画布，再回到排版 */
async function placeFigureAndBack(page: Page) {
  await (await only(page.locator('[data-card="Fig1_kinetics.pdf"]'), 30_000)).dblclick()
  await (await only(page.locator('[data-context-back]'))).click()
}

/** 画布上的文字对象（不含时间线预览里画的那一份） */
const canvasText = (page: Page, text: string) =>
  page.locator('[data-canvas-stage]').getByText(text, { exact: true })

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

  // ── 顶栏书签钮命名「投稿前」（不经抽屉）──────────────────────────
  await clock.click() // 先收起抽屉：命名不该依赖它
  await expect(drawer).toHaveCount(0)
  await (await only(page.locator('[data-timeline-name-button]'))).click()
  const quick = await only(page.locator('[data-timeline-quick-name-input]'))
  await expect(quick).toBeFocused()
  await quick.fill('投稿前')
  if (SHOTS) await page.screenshot({ path: path.join(SHOTS, 'timeline-quick-name.png') })
  await quick.press('Enter')
  await expect(quick).toHaveCount(0)

  // ── 「命名」视图里找得到它 ─────────────────────────────────────────
  await clock.click()
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
  await (await only(page.locator('[data-timeline-name-button]'))).click()
  const quick = await only(page.locator('[data-timeline-quick-name-input]'))
  await quick.fill('重叠')
  await quick.press('Enter')
  await expect(quick).toHaveCount(0)
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
  await (await only(page.locator('[data-timeline-name-button]'))).click()
  const quick = await only(page.locator('[data-timeline-quick-name-input]'))
  await quick.fill('改过字号')
  await quick.press('Enter')
  await expect(quick).toHaveCount(0)
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

test('只差水平翻转的两个节点：缩略图不一样，而且是左右镜像（缩略图带翻转，Codex #679）', async ({
  app,
  page,
}) => {
  test.setTimeout(240_000)
  await page.setViewportSize({ width: 1440, height: 900 })
  // 缩略图合成走了哪一路图源（失败信息里带上）
  await page.addInitScript(() => {
    ;(window as unknown as Record<string, unknown>).__TAVOTTO_THUMB_TRACE__ = []
  })
  const a = await app()
  await page.goto(a.baseURL)
  // 放一张图（按页面居中落位：整张缩略图左右镜像时，面板映到它自己身上）
  await placeFigureAndBack(page)
  const nameNow = async (name: string) => {
    await (await only(page.locator('[data-timeline-name-button]'))).click()
    const quick = await only(page.locator('[data-timeline-quick-name-input]'))
    await quick.fill(name)
    await quick.press('Enter')
    await expect(quick).toHaveCount(0)
  }
  await nameNow('原样')
  // 属性页摘要行「旋转、翻转、透明度」里的「水平翻转」（面板放上来之后是选中的）。
  // `data-panel-more` 在摘要行的外壳上，开关是它里面的那颗按钮
  const more = await only(page.locator('[data-panel-more] > button'))
  if ((await more.getAttribute('aria-expanded')) !== 'true') await more.click()
  await (await only(page.locator('[data-panel-flip="h"]'))).click()
  await nameNow('翻转')
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
  // 判据量的是**面板内容的左右分布**，不是逐像素相等：两个节点的缩略图可能取自不同的图源
  // （先拍的那张还是素材图、后拍的那张已经换成引擎 SVG——笔画粗细、抗锯齿、字形都不一样），
  // 逐像素镜像差在 Windows 的 WebKit 上量到过 0.51（CI run 36654291346），而两张图明明互为
  // 镜像。按列统计「墨量」（每一列暗了多少）得到一条横向分布曲线：翻转之后的曲线应当与原样
  // 的曲线**倒过来**高度相关、与原样本身明显不相关。面板按页面居中落位，整张缩略图的左右
  // 镜像就是面板自己的镜像（前提在下面断言）。
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
