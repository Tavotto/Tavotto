import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs'
import path from 'node:path'
import type { Locator, Page } from '@playwright/test'
import { expect, openElementsTab, test } from './fixtures'

/**
 * 功能登记表的核心路径用例（ADR 0097，`docs/features/registry.json`）。
 *
 * 每条用例都带 `@feature:<id>` 标签，登记表把它列在那个功能名下；门禁
 * （`scripts/ci/feature_registry.py`）判两件事：登记的用例在、没有被跳过（静态），
 * 以及合并态那次真跑里它确实执行并通过（posix-e2e 的运行报告）。
 *
 * 判据只认**用户看得见的结果**，不认「DOM 里有这个节点」：
 *   * 元素在视口内、点得到（`expectInViewport`）；
 *   * 操作之后画面真的变了——对象的屏幕位置、引擎重画出来的 SVG 里那个元素的框、
 *     落盘文件的文件头；
 *   * 引擎那一侧以 `data-display="exact"` + `data-display-key` 换版为准：画布此刻挂的
 *     是这一版文档自己的精确图，而不是上一张暂挂的。
 *
 * 选择器按 web/AGENTS.md 认稳定 `data-*`；只有产品没给锚点的地方才用可达名，
 * 并在旁边写明。
 */

// 画布对象拖动量的是「位移 = 鼠标位移」，吸附会有意把落点拽到对齐线上——与这把尺子
// 正交（吸附有自己的用例）。同 fake-realtime.spec.ts：在关掉吸附的画布上量。
test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    try {
      const key = 'tavotto.ui'
      const saved = JSON.parse(localStorage.getItem(key) || '{}')
      localStorage.setItem(key, JSON.stringify({ ...saved, prefsVersion: 2, snapEnabled: false }))
    } catch {
      /* 存储不可用时照常跑 */
    }
  })
})

const VIEWPORT = { width: 1440, height: 900 }
const CARD = '[data-card="Fig1_kinetics.pdf"]'

/** 元素整个框都在视口里（不是「DOM 里有」，也不是「有一个像素露出来」）。 */
async function expectInViewport(page: Page, loc: Locator, what: string) {
  await expect(loc, `${what} 应当可见`).toBeVisible()
  const b = await loc.boundingBox()
  expect(b, `${what} 没有布局框`).not.toBeNull()
  const vp = page.viewportSize()!
  expect(b!.width, `${what} 宽度为 0`).toBeGreaterThan(0)
  expect(b!.height, `${what} 高度为 0`).toBeGreaterThan(0)
  expect(b!.x, `${what} 左边出了视口`).toBeGreaterThanOrEqual(0)
  expect(b!.y, `${what} 上边出了视口`).toBeGreaterThanOrEqual(0)
  expect(b!.x + b!.width, `${what} 右边出了视口`).toBeLessThanOrEqual(vp.width + 0.5)
  expect(b!.y + b!.height, `${what} 下边出了视口`).toBeLessThanOrEqual(vp.height + 0.5)
}

/** 点得到：框中心那一点的最上层元素就是它（或它的后代），没有被浮层 / 遮罩挡住。 */
async function expectHittable(loc: Locator, what: string) {
  const hit = await loc.evaluate((el) => {
    const r = el.getBoundingClientRect()
    const top = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2)
    return !!top && (top === el || el.contains(top))
  })
  expect(hit, `${what} 中心被别的元素挡住了`).toBe(true)
}

/** 双击素材卡打开这张图（快速编辑，当场在图内编辑态），等引擎的 SVG 画出来。 */
async function openFigure(page: Page, baseURL: string) {
  await page.setViewportSize(VIEWPORT)
  await page.goto(baseURL)
  await page.locator(CARD).dblclick({ timeout: 30_000 })
  await expect(page.locator('[data-element-svg]')).toHaveCount(1, { timeout: 120_000 })
  await expect(page.locator('[data-element-svg] > svg')).toBeVisible({ timeout: 120_000 })
  await waitExact(page)
}

/** 画布上唯一那张图此刻挂的是这一版文档自己的精确图；返回它的版本键。 */
async function waitExact(page: Page, notKey?: string | null): Promise<string | null> {
  const host = page.locator('[data-display]')
  await expect(host).toHaveCount(1)
  await expect
    .poll(
      async () => {
        const kind = await host.getAttribute('data-display')
        const key = await host.getAttribute('data-display-key')
        return kind === 'exact' && (notKey === undefined || key !== notKey) ? 'ok' : `${kind}:${key}`
      },
      { timeout: 90_000, message: '画布没有换成这一版文档的精确图' },
    )
    .toBe('ok')
  return host.getAttribute('data-display-key')
}

/** 引擎 SVG 里某个图内元素的屏幕框（图内编辑宿主是单例，先断言再取）。 */
function gidBox(page: Page, gid: string) {
  return page.evaluate((id) => {
    const hosts = document.querySelectorAll('[data-element-svg]')
    if (hosts.length !== 1) throw new Error(`图内编辑宿主应恰有一个，实际 ${hosts.length}`)
    const n = hosts[0].querySelector(`[id="${id}"]`) as SVGGraphicsElement | null
    if (!n) return null
    const r = n.getBoundingClientRect()
    return { x: r.x, y: r.y, w: r.width, h: r.height }
  }, gid)
}

/** 画布上（排版模式）的对象框，按文档里的对象 id 取。 */
function objectBoxes(page: Page) {
  return page.locator('[data-canvas-stage] [data-object-id]').evaluateAll((els) =>
    els.map((e) => {
      const r = e.getBoundingClientRect()
      return { id: e.getAttribute('data-object-id'), x: r.x, y: r.y, w: r.width, h: r.height }
    }),
  )
}

test(
  '功能：素材放上画布渲染出真图；点选出单选浮动栏；拖动真的移动；撤销 / 重做回到对应位置',
  {
    tag: [
      '@feature:canvas.place-and-render',
      '@feature:canvas.select-and-drag',
      '@feature:canvas.single-context-bar',
      '@feature:edit.undo-redo',
    ],
  },
  async ({ app, page }) => {
    const a = await app()
    await openFigure(page, a.baseURL)
    // 回到排版：上下文栏上唯一那颗返回入口
    await page.locator('[data-context-back]').click()
    await expect(page.locator('[data-element-svg]')).toHaveCount(0)

    const panel = page.locator('[data-canvas-stage] [data-object-id]')
    await expect(panel).toHaveCount(1, { timeout: 30_000 })
    await expectInViewport(page, panel, '画布上的面板')

    // ── 首次渲染：面板里是真图，不是空框 / 碎图标 / 占位 ──────────────────
    // 位图走像素：画进 canvas 数颜色；SVG 走几何：有面积的绘制节点数。两种都要「有内容」。
    await expect
      .poll(
        () =>
          panel.evaluate(async (el) => {
            const img = el.querySelector('img') as HTMLImageElement | null
            if (img) {
              if (!img.complete || img.naturalWidth === 0) return 'img-not-loaded'
              const c = document.createElement('canvas')
              c.width = Math.min(img.naturalWidth, 400)
              c.height = Math.min(img.naturalHeight, 300)
              const ctx = c.getContext('2d')!
              ctx.drawImage(img, 0, 0, c.width, c.height)
              const d = ctx.getImageData(0, 0, c.width, c.height).data
              const colors = new Set<number>()
              let ink = 0
              for (let i = 0; i < d.length; i += 4) {
                colors.add((d[i] << 16) | (d[i + 1] << 8) | d[i + 2])
                if (d[i] + d[i + 1] + d[i + 2] < 600 && d[i + 3] > 0) ink++
              }
              return colors.size > 8 && ink > 200 ? 'ok' : `img-blank:${colors.size}:${ink}`
            }
            const svg = el.querySelector('svg')
            if (svg) {
              const drawn = [...svg.querySelectorAll('path, text, use, image')].filter((n) => {
                const r = (n as SVGGraphicsElement).getBoundingClientRect()
                return r.width > 0 && r.height > 0
              })
              return drawn.length > 10 ? 'ok' : `svg-blank:${drawn.length}`
            }
            return 'nothing'
          }),
        { timeout: 60_000, message: '面板里应当画出一张真图' },
      )
      .toBe('ok')

    // ── 单选浮动栏：点一下出现、整条在视口内、「编辑图内元素」点得到 ───────────
    await panel.click()
    const bar = page.locator('[data-context-bar]')
    await expect(bar).toHaveCount(1)
    await expectInViewport(page, bar, '单选浮动栏')
    // 产品没给这颗按钮 data 锚点：按可达名认，收在浮动栏里（不取全页第一个）
    const editBtn = bar.getByRole('button', { name: /图内元素/ })
    await expect(editBtn).toHaveCount(1)
    await expectHittable(editBtn, '「编辑图内元素」')

    // ── 拖动：屏幕上真的移动了 Δ ────────────────────────────────────────────
    const [b0] = await objectBoxes(page)
    const dx = 120
    const dy = 60
    await page.mouse.move(b0.x + b0.w / 2, b0.y + b0.h / 2)
    await page.mouse.down()
    for (let i = 1; i <= 12; i++) await page.mouse.move(b0.x + b0.w / 2 + (dx * i) / 12, b0.y + b0.h / 2 + (dy * i) / 12)
    await page.mouse.up()
    await expect
      .poll(async () => {
        const [b] = await objectBoxes(page)
        return Math.abs(b.x - b0.x - dx) < 2 && Math.abs(b.y - b0.y - dy) < 2
      }, { message: '面板应当跟着鼠标平移 (120, 60)' })
      .toBe(true)
    const [b1] = await objectBoxes(page)
    // 松手后单选浮动栏仍在（选区没丢）
    await expect(bar).toBeVisible()

    // ── 撤销 / 重做：回到拖动前 / 拖动后的位置 ──────────────────────────────
    await page.keyboard.press('ControlOrMeta+z')
    await expect
      .poll(async () => {
        const [b] = await objectBoxes(page)
        return Math.hypot(b.x - b0.x, b.y - b0.y)
      }, { message: '撤销后面板应当回到拖动前的位置' })
      .toBeLessThan(1)
    await page.keyboard.press('ControlOrMeta+Shift+z')
    await expect
      .poll(async () => {
        const [b] = await objectBoxes(page)
        return Math.hypot(b.x - b1.x, b.y - b1.y)
      }, { message: '重做后面板应当回到拖动后的位置' })
      .toBeLessThan(1)
  },
)

test(
  '功能：多选浮动栏在视口内，点「左对齐」后各对象左边真的对齐；撤销还原',
  { tag: ['@feature:canvas.multi-context-bar-align'] },
  async ({ app, page }) => {
    const a = await app()
    await page.setViewportSize(VIEWPORT)
    await page.goto(a.baseURL)
    const stage = page.locator('[data-canvas-stage]')
    await expect(stage).toBeVisible({ timeout: 30_000 })

    // 三段文字，左边各不相同（文字工具的快捷键是 T，见快捷键帮助）
    for (const [x, y, s] of [
      [300, 200, 'alpha'],
      [460, 320, 'beta'],
      [380, 440, 'gamma'],
    ] as const) {
      await page.keyboard.press('Escape')
      await page.keyboard.press('t')
      await stage.click({ position: { x, y } })
      await page.keyboard.type(s)
      await page.keyboard.press('Escape')
    }
    await page.keyboard.press('Escape')
    const objs = page.locator('[data-canvas-stage] [data-object-id]')
    await expect(objs).toHaveCount(3)
    const before = (await objectBoxes(page)).map((b) => Math.round(b.x))
    expect(new Set(before).size, '三段文字的左边应当各不相同').toBe(3)

    await page.keyboard.press('ControlOrMeta+a')
    const bar = page.locator('[data-multi-selection-context-bar]')
    await expect(bar).toHaveCount(1)
    await expectInViewport(page, bar, '多选浮动栏')
    await expect(bar.locator('[data-selection-count]')).toHaveAttribute('data-selection-count', '3')

    // 宽屏下对齐按钮直接在栏上；窄的变体收在「对齐」弹层里——两种都认
    let left = bar.locator('[data-align-mode="left"]')
    if (!(await left.count())) {
      await bar.locator('[data-multi-menu="align"]').click()
      left = page.locator('[data-align-mode="left"]')
    }
    await expect(left).toHaveCount(1)
    await expectHittable(left, '「左对齐」')
    await left.click()
    await expect
      .poll(async () => new Set((await objectBoxes(page)).map((b) => Math.round(b.x))).size, {
        message: '左对齐之后三段文字的左边应当是同一条线',
      })
      .toBe(1)
    // 对齐后选区还在、浮动栏还在
    await expect(bar).toBeVisible()

    await page.keyboard.press('ControlOrMeta+z')
    await expect
      .poll(async () => (await objectBoxes(page)).map((b) => Math.round(b.x)).sort().join(','), {
        message: '撤销后应当回到对齐前的位置',
      })
      .toBe([...before].sort().join(','))
  },
)

test(
  '功能：图内点选标题改字号，引擎重画后标题真的变大；撤销回到原字号',
  { tag: ['@feature:figure.select-and-edit'] },
  async ({ app, page }) => {
    const a = await app()
    await openFigure(page, a.baseURL)
    const h0 = (await gidBox(page, 'axes_0.title'))!
    expect(h0, '样例图应当有标题').not.toBeNull()

    // 在图上直接点标题（图内元素的选中），属性栏换成标题的字号
    await page.mouse.click(h0.x + h0.w / 2, h0.y + h0.h / 2)
    const size = page.locator('[data-prop="fontsize"] input')
    await expect(size).toHaveCount(1, { timeout: 15_000 })
    await expectInViewport(page, size, '字号输入框')
    const v0 = Number(await size.inputValue())
    expect(v0).toBeGreaterThan(0)

    const key0 = await waitExact(page)
    await size.fill(String(v0 + 8))
    await size.press('Enter')
    const key1 = await waitExact(page, key0)
    const h1 = (await gidBox(page, 'axes_0.title'))!
    expect(h1.h, `字号 ${v0} → ${v0 + 8} 之后标题应当变高（${h0.h} → ${h1.h}）`).toBeGreaterThan(h0.h * 1.2)

    // 撤销：把焦点从输入框挪走再按（输入框里的 ⌘Z 归文本编辑管）
    await page.locator('[data-canvas-stage]').focus()
    await page.keyboard.press('ControlOrMeta+z')
    await waitExact(page, key1)
    await expect
      .poll(async () => Math.abs((await gidBox(page, 'axes_0.title'))!.h - h0.h), {
        message: '撤销后标题应当回到原来的高度',
      })
      .toBeLessThan(0.5)
  },
)

test(
  '功能：图例位置网格点「左上」，引擎重画后图例真的挪到坐标区左上角',
  { tag: ['@feature:legend.properties'] },
  async ({ app, page }) => {
    const a = await app()
    await openFigure(page, a.baseURL)
    const axes = (await gidBox(page, 'axes_0.patch')) ?? (await gidBox(page, 'axes_0'))
    expect(axes, '样例图应当有坐标区').not.toBeNull()
    const legend0 = (await gidBox(page, 'axes_0.legend'))!
    expect(legend0, '样例图应当有图例').not.toBeNull()
    const cx = (b: { x: number; w: number }) => b.x + b.w / 2
    const cy = (b: { y: number; h: number }) => b.y + b.h / 2
    // 起点：图例在坐标区的右半边（样例图默认右下）
    expect(cx(legend0)).toBeGreaterThan(cx(axes!))

    await openElementsTab(page)
    await page.locator('[data-el="axes_0.legend"]').click()
    const upperLeft = page.locator('[role="radiogroup"] [role="radio"][data-option="upper left"]')
    await expect(upperLeft).toHaveCount(1, { timeout: 15_000 })
    await expectInViewport(page, upperLeft, '图例位置「左上」')
    const key0 = await waitExact(page)
    await upperLeft.click()
    await expect(upperLeft).toHaveAttribute('aria-checked', 'true')
    await waitExact(page, key0)
    await expect
      .poll(async () => {
        const l = (await gidBox(page, 'axes_0.legend'))!
        return cx(l) < cx(axes!) && cy(l) < cy(axes!)
      }, { message: '图例应当落在坐标区的左上四分之一' })
      .toBe(true)
  },
)

test(
  '功能：左栏「样式」改标题字号，图跟着重画；「恢复原样」回到原来',
  { tag: ['@feature:style.panel'] },
  async ({ app, page }) => {
    const a = await app()
    await openFigure(page, a.baseURL)
    const h0 = (await gidBox(page, 'axes_0.title'))!
    expect(h0, '样例图应当有标题').not.toBeNull()

    const rail = page.locator('[data-rail="style"]')
    if ((await rail.getAttribute('aria-expanded')) !== 'true') await rail.click()
    const panel = page.locator('[data-style-panel]')
    await expect(panel).toBeVisible({ timeout: 30_000 })
    const size = panel.locator('[data-style-cell="title.size"] input')
    await expect(size).toHaveCount(1, { timeout: 30_000 })
    await expectInViewport(page, size, '样式面板「标题」字号')
    const v0 = Number(await size.inputValue())
    expect(v0).toBeGreaterThan(0)

    const key0 = await waitExact(page)
    await size.fill(String(v0 + 8))
    await size.press('Enter')
    const key1 = await waitExact(page, key0)
    const h1 = (await gidBox(page, 'axes_0.title'))!
    expect(h1.h, `样式面板把标题字号改大之后，图上的标题应当变高（${h0.h} → ${h1.h}）`).toBeGreaterThan(h0.h * 1.2)

    const restore = page.locator('[data-style-restore]')
    await expect(restore).toBeEnabled({ timeout: 30_000 })
    await restore.click()
    await waitExact(page, key1)
    await expect
      .poll(async () => Math.abs((await gidBox(page, 'axes_0.title'))!.h - h0.h), {
        message: '「恢复原样」之后标题应当回到原来的高度',
      })
      .toBeLessThan(0.5)
  },
)

test(
  '功能：导出对话框一次导出 PDF / PNG / TIFF，三个文件真的落盘、文件头对得上',
  { tag: ['@feature:export.pdf', '@feature:export.png', '@feature:export.tiff'] },
  async ({ app, page }) => {
    test.setTimeout(300_000)
    const a = await app()
    await openFigure(page, a.baseURL)
    await page.locator('[data-context-back]').click()
    await expect(page.locator('[data-canvas-stage] [data-object-id]')).toHaveCount(1)

    await page.keyboard.press('ControlOrMeta+e')
    const dialog = page.getByRole('dialog')
    await expect(dialog).toBeVisible()
    await expectInViewport(page, dialog, '导出对话框')
    // 格式名不翻译（PDF / PNG / TIFF）；复选框的可达名就是它
    for (const fmt of ['PDF', 'PNG', 'TIFF']) {
      const box = dialog.getByRole('checkbox', { name: fmt, exact: true })
      await expect(box).toHaveCount(1)
      if (!(await box.isChecked())) await box.check()
      await expect(box).toBeChecked()
    }
    const eps = dialog.getByRole('checkbox', { name: 'EPS', exact: true })
    if (await eps.isChecked()) await eps.uncheck()

    const confirm = dialog.locator('input[data-export-confirm]')
    if (await confirm.count()) await confirm.check()

    // 导出走后台作业：`/api/export/start` 当场回作业（带导出目录），结果经 SSE 推给界面。
    // 端点形态不是这条用例的主语——导出目录取自 start 的回包，结果认界面那句话 + 磁盘上的文件
    const started = page.waitForResponse(
      (r) => new URL(r.url()).pathname === '/api/export/start' && r.request().method() === 'POST',
    )
    // 产品没给主按钮 data 锚点：按可达名认，收在对话框里
    const start = dialog.getByRole('button', { name: /开始导出/ })
    await expect(start).toBeEnabled()
    // 导出目录不一定是空的：夹具拷的是仓库里的 examples/figures，Windows 那条腿在 e2e 之前跑的
    // 冒烟会往它的 tavottofile/export 里导出 smoke_*.pdf（#676 full-ci 首跑就红在这里）。
    // 主语是**这一次导出写出的文件**：按修改时间认点「开始导出」之后写的（留 1 s 给文件系统的时间精度）
    const t0 = Date.now() - 1000
    await start.click()
    const job = (await (await started).json()) as { export_dir?: string }
    expect(job.export_dir, 'start 回包里应当有导出目录').toBeTruthy()
    const exportDir = job.export_dir!

    // 界面说出了结果（不是只有请求成功）
    await expect(dialog.getByText(/已保存到/)).toBeVisible({ timeout: 240_000 })
    // 文件按扩展名认（同目录里还有预检报告等别的产物）；文件头要对得上格式，不认「有个文件」
    const magic: Record<string, (b: Buffer) => boolean> = {
      '.pdf': (b) => b.subarray(0, 5).toString('latin1') === '%PDF-',
      '.png': (b) => b.subarray(0, 8).equals(Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])),
      '.tiff': (b) =>
        b.subarray(0, 4).equals(Buffer.from([0x49, 0x49, 0x2a, 0x00])) ||
        b.subarray(0, 4).equals(Buffer.from([0x4d, 0x4d, 0x00, 0x2a])),
    }
    const extOf = (f: string) => {
      const e = path.extname(f).toLowerCase()
      return e === '.tif' ? '.tiff' : e
    }
    const listed = () =>
      (existsSync(exportDir) ? readdirSync(exportDir) : []).filter(
        (f) => statSync(path.join(exportDir, f)).mtimeMs >= t0,
      )
    await expect
      .poll(() => Object.keys(magic).filter((e) => !listed().some((f) => extOf(f) === e)), {
        timeout: 60_000,
        message: `这次导出应当在 ${exportDir} 写出 PDF / PNG / TIFF 三个文件`,
      })
      .toEqual([])
    for (const ext of Object.keys(magic)) {
      const names = listed().filter((f) => extOf(f) === ext)
      expect(names, `${ext} 应当恰好一个：${names.join(', ')}`).toHaveLength(1)
      const bytes = readFileSync(path.join(exportDir, names[0]))
      expect(bytes.length, `${names[0]} 太小`).toBeGreaterThan(900)
      expect(magic[ext](bytes), `${names[0]} 的文件头不是 ${ext}`).toBe(true)
    }
  },
)

test(
  '功能：命令面板 ⌘K 搜索并执行命令——加一段文字、打开快捷键帮助',
  { tag: ['@feature:command.palette'] },
  async ({ app, page }) => {
    const a = await app()
    await page.setViewportSize(VIEWPORT)
    await page.goto(a.baseURL)
    await expect(page.locator('[data-canvas-stage]')).toBeVisible({ timeout: 30_000 })
    const objs = page.locator('[data-canvas-stage] [data-object-id]')
    const n0 = await objs.count()

    const openPalette = async () => {
      await page.keyboard.press('ControlOrMeta+k')
      const list = page.locator('[role="listbox"]:has([data-cmd-id])')
      await expect(list).toHaveCount(1)
      await expectInViewport(page, list, '命令面板')
      return list
    }

    // 搜「text」收窄到「加文字」，回车执行：画布上真的多了一个对象
    let list = await openPalette()
    await page.keyboard.type('text')
    const addText = list.locator('[data-cmd-id="add-text"]')
    await expect(addText).toHaveCount(1)
    await expect(list.locator('[data-cmd-id="export"]')).toHaveCount(0)
    await expect(addText).toHaveAttribute('aria-selected', 'true')
    await page.keyboard.press('Enter')
    await expect(list).toHaveCount(0)
    await expect(objs).toHaveCount(n0 + 1)
    await page.keyboard.press('Escape')
    await page.keyboard.press('Escape')

    // 再开一次，点「快捷键帮助」：帮助真的打开、分组都在
    list = await openPalette()
    await page.keyboard.type('shortcut')
    const help = list.locator('[data-cmd-id="shortcut-help"]')
    await expect(help).toHaveCount(1)
    await help.click()
    await expect(page.locator('[data-shortcut-group]').first()).toBeVisible()
    await expect(page.locator('[data-shortcut-group="file"]')).toBeVisible()
  },
)

test(
  '功能：设置里把界面语言切成英文，整页当场换语言，刷新后仍是英文',
  { tag: ['@feature:i18n.language'] },
  async ({ app, page }) => {
    const a = await app()
    await page.setViewportSize(VIEWPORT)
    await page.goto(a.baseURL)
    await expect(page.locator('html')).toHaveAttribute('lang', 'zh-CN', { timeout: 30_000 })

    await page.locator('[data-rail="settings"]').click()
    const shell = page.locator('[data-settings-shell]')
    await expect(shell).toBeVisible()
    await shell.locator('[data-section="general"]').click()
    // 语言下拉没有 data 锚点；它是「常规」分区里的一个组合框，可达名按当前语言认
    const lang = page.locator('[data-settings-content]').getByRole('combobox', { name: '语言' })
    await expectInViewport(page, lang, '语言下拉')
    await lang.click()
    // 选项名是各语言的自称（LOCALE_LABELS），不随界面语言变
    await page.getByRole('option', { name: 'English' }).click()

    // 当场生效：<html lang> 与设置里的分区名一起换成英文
    await expect(page.locator('html')).toHaveAttribute('lang', 'en-US')
    await expect(shell.locator('[data-section="general"]')).toContainText('General')
    await page.keyboard.press('Escape')

    await page.reload()
    await expect(page.locator('html')).toHaveAttribute('lang', 'en-US', { timeout: 30_000 })
    expect(await page.evaluate(() => localStorage.getItem('tavotto.locale'))).toBe('en-US')
    await page.locator('[data-rail="settings"]').click()
    await expect(page.locator('[data-settings-shell] [data-section="general"]')).toContainText('General')
  },
)
