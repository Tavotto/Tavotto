import type { Page } from '@playwright/test'
import { execFileSync } from 'node:child_process'
import { mkdirSync, mkdtempSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { expect, test } from './fixtures'

/**
 * matplotlib 对象拖动全族排查的浏览器腿（ADR 0100）：**真 PanelView、真指针、真 matplotlib**。
 *
 * 用户说「经常拖不动图里的框和图」。排查（PR 正文的表）找出的几类，这里各拖一次：
 * - 锚定框（`AnchoredText`「(a)」角标）与 `AnnotationBbox`：以前不宣称可拖，按下去什么都不发生；
 * - 插图（`ax.inset_axes`）：以前不宣称可拖；
 * - constrained 图上的色条：以前拖完整张图画不出来；
 * - 圆角框（`FancyBboxPatch`）与图例：对照组（以前就能拖），同一把尺子量一遍；
 * - 曲线（按设计不能拖）：以前拖了什么都不说，现在说出为什么。
 *
 * 独立尺：鼠标在屏幕上走了 (DX, DY) 像素，元素的 SVG 组（matplotlib 输出的 gid）在屏幕上
 * 也该走 (DX, DY)——视图倍率不变时，图内一个内容像素就是一个屏幕像素乘倍率，拖动与成图都按
 * 它换算，与 manifest / override 的写法无关。时刻：① 拖动途中（预览跟手）② 松手、权威 SVG
 * 换上画布之后（落点）③ 撤销之后（回原处）。另量一个**不相干的子图**：拖谁它都不该动
 * （constrained 排版不许把它挤开）。最后保存、重开：重放出来的位置 == 热态。
 *
 * 吸附关掉（与 fake-realtime 同一个偏好键）：吸附会有意把落点拽到对齐线上，与这把尺子正交。
 */
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

const DX = 24
const DY = 14
const BUDGET_PX = 0.75

function dragLibrary(): string {
  const dir = path.join(mkdtempSync(path.join(os.tmpdir(), 'tavotto-e2e-drag-')), 'figures')
  mkdirSync(dir)
  writeFileSync(
    path.join(dir, 'fig_drag.py'),
    [
      'import matplotlib',
      'matplotlib.use("Agg")',
      'import matplotlib.pyplot as plt',
      'import numpy as np',
      'from matplotlib.offsetbox import AnchoredText, AnnotationBbox, TextArea',
      'from matplotlib.patches import FancyBboxPatch',
      '',
      '',
      'def main():',
      '    t = np.linspace(0, 10, 100)',
      '    fig, (ax, ax2) = plt.subplots(1, 2, figsize=(16 / 2.54, 7 / 2.54), layout="constrained")',
      '    ax.plot(t, 0.6 * np.sin(t), label="sin")',
      '    ax.set_ylim(-1.3, 1.3)',
      '    ax.set_title("Drag audit")',
      '    ax.legend(loc="upper right")',
      '    ax.add_artist(AnchoredText("(a)", loc="upper left"))',
      '    ax.add_patch(FancyBboxPatch((0.4, -1.2), 2.0, 0.35, boxstyle="round,pad=0.08",',
      '                                facecolor="#E8F2FA", edgecolor="#8BB7DA"))',
      '    ins = ax.inset_axes([0.62, 0.06, 0.3, 0.26])',
      '    ins.plot([0, 1], [0, 1])',
      '    ins.set_xticks([])',
      '    ins.set_yticks([])',
      '    im = ax2.imshow(np.arange(64.0).reshape(8, 8))',
      '    fig.colorbar(im, ax=ax2)',
      '    ax2.add_artist(AnnotationBbox(TextArea("note"), (2, 2), xybox=(4.5, 1.2)))',
      '    fig.savefig("Fig_drag.pdf")',
      '',
    ].join('\n'),
    'utf-8',
  )
  writeFileSync(
    path.join(dir, 'tavotto_registry.json'),
    JSON.stringify({ scripts: { 'fig_drag.py': { entry: 'main', cost: 'light', stems: ['Fig_drag'] } } }),
    'utf-8',
  )
  const fallback = process.platform === 'win32' ? 'python' : 'python3'
  const py = process.env.TAVOTTO_WORKER_PYTHON || fallback
  execFileSync(py, ['-c', 'import fig_drag; fig_drag.main()'], { cwd: dir, timeout: 120_000 })
  return dir
}

type Box = { x: number; y: number; w: number; h: number }

const boxOf = (page: Page, gid: string): Promise<Box | null> =>
  page.evaluate((id) => {
    const hosts = document.querySelectorAll('[data-element-svg]')
    if (hosts.length !== 1) throw new Error(`图内编辑宿主应恰有一个，实际 ${hosts.length}`)
    const n = hosts[0].querySelector(`[id="${id}"]`)
    if (!n) return null
    const r = (n as SVGGraphicsElement).getBoundingClientRect()
    return { x: r.x, y: r.y, w: r.width, h: r.height }
  }, gid)

/** 元素在它那张图里的位置（按图宽 / 图高归一）：重开后视口重新适配也能比 */
const relOf = (page: Page, gid: string) =>
  page.evaluate((id) => {
    const host = document.querySelector('[data-element-svg] > svg')!
    const n = host.querySelector(`[id="${id}"]`)!
    const h = host.getBoundingClientRect()
    const r = (n as SVGGraphicsElement).getBoundingClientRect()
    return [(r.x - h.x) / h.width, (r.y - h.y) / h.height, r.width / h.width, r.height / h.height]
  }, gid)

const announced = (page: Page) =>
  page.evaluate(() => document.querySelector('[data-status-live]')?.textContent?.trim() ?? '')

/** 做一件会触发权威渲染的事，等那次 `/api/engine/render` 回来、新 SVG 换上画布 */
async function settleAfter(page: Page, action: () => Promise<void>) {
  const rendered = page.waitForResponse((r) => r.url().includes('/api/engine/render'), {
    timeout: 60_000,
  })
  await action()
  await rendered
  await page.waitForTimeout(600)
}

async function addFigureToCanvas(page: Page, baseURL: string) {
  await page.goto(baseURL)
  // 素材卡认稳定锚点 data-card；Shift+Enter = 添加到画布
  await page.locator('[data-card="Fig_drag.pdf"]').focus({ timeout: 30_000 })
  await page.keyboard.press('Shift+Enter')
  await expect(page.locator('[data-object-id]')).toHaveCount(1, { timeout: 30_000 })
  await page.waitForTimeout(1500)
}

async function enterFigure(page: Page) {
  const p = (await page.locator('[data-object-id]').boundingBox())!
  await page.mouse.click(p.x + p.width / 2, p.y + p.height / 2)
  await page.keyboard.press('Enter')
  await expect(page.locator('[data-element-svg] > svg')).toBeVisible({ timeout: 60_000 })
  await page.waitForTimeout(2000)
}

/**
 * 被测对象：按下的那个元素（`gid`，在它**此刻**的框里按 `at` 比例处按下）与该量的 SVG 组
 * （`measure`；色条的几何代理到色条轴）。`at` 选在只落在它身上的地方。`dir` 是拖动方向
 * （各分量 1 / 0 / -1，默认右下）：贴着图边的对象往里拖或不在那一维上拖——子图 / 色条贴到
 * 图边会被钳住（`axesMove`，按设计），往外拖量到的是钳位，不是落点。
 */
const TARGETS: { name: string; gid: string; measure: string; at: [number, number]; dir?: [number, number] }[] = [
  { name: 'AnchoredText 角标', gid: 'axes_0.artists_0', measure: 'axes_0.artists_0', at: [0.5, 0.5] },
  { name: 'AnnotationBbox 文字框', gid: 'axes_1.artists_0', measure: 'axes_1.artists_0', at: [0.5, 0.5] },
  { name: '插图（ax.inset_axes）', gid: 'axes_3', measure: 'axes_3', at: [0.2, 0.25] },
  // 色条轴那个 <g> 连着右边的刻度文字：按在左边那一截（色带本身）上。它贴着图的右边、上下
  // 几乎顶满，只往左拖（合并队列 posix-e2e / windows-exe-smoke：往右拖 24 px 时右边只剩
  // 23.5 px 的余量，钳住 0.54 px，落点误差 0.753 > 0.75；本机同一视口右边余量 0.8 px，往下
  // 拖的余量也只多 0.8 px）。y 照量：不拖的那一维也不许动
  { name: 'constrained 色条', gid: 'axes_2', measure: 'axes_2', at: [0.12, 0.5], dir: [-1, 0] },
  { name: '圆角框 FancyBboxPatch', gid: 'axes_0.patches_0', measure: 'axes_0.patches_0', at: [0.5, 0.5] },
  { name: '图例', gid: 'axes_0.legend', measure: 'axes_0.legend', at: [0.3, 0.5] },
]

/** 不相干的子图：左边那张的对象被拖时量右边那张，反之亦然 */
const bystanderOf = (gid: string) => (gid.startsWith('axes_0') || gid === 'axes_3' ? 'axes_1' : 'axes_0')

test('图里的框与图：锚定框 / 插框 / 插图 / 色条 / 圆角框 / 图例拖到位、跟手、撤销回原处、不挤动别的子图；重开 == 热态', async ({
  app,
  page,
}, testInfo) => {
  const dir = dragLibrary()
  const a = await app({ figures: dir })

  await addFigureToCanvas(page, a.baseURL)
  await enterFigure(page)
  const host = page.locator('[data-element-svg]')
  const shot = async (name: string) => {
    const file = testInfo.outputPath(`${name}.png`)
    await host.screenshot({ path: file })
    await testInfo.attach(name, { path: file, contentType: 'image/png' })
  }
  await shot('drag-before')

  const rows: string[] = []
  for (const t of TARGETS) {
    const b0 = (await boxOf(page, t.measure))!
    expect(b0, `${t.name}：SVG 里应当有 id=${t.measure} 的组`).not.toBeNull()
    const press = (await boxOf(page, t.gid))!
    const by = bystanderOf(t.gid)
    const o0 = (await boxOf(page, by))!
    const sx = press.x + t.at[0] * press.w
    const sy = press.y + t.at[1] * press.h
    const [ux, uy] = t.dir ?? [1, 1]
    const dx = ux * DX
    const dy = uy * DY
    // 前提：拖动方向上离图边的余量够（组框 ⊇ 子图框，按组框量偏保守）。不够的话图边会把它
    // 钳住，下面量到的是钳位而不是落点——换拖动方向，别放宽预算
    const fig = (await page.locator('[data-element-svg] > svg').boundingBox())!
    const room = [
      ux > 0 ? fig.x + fig.width - (b0.x + b0.w) : b0.x - fig.x,
      uy > 0 ? fig.y + fig.height - (b0.y + b0.h) : b0.y - fig.y,
    ]
    if (ux) expect(room[0], `${t.name}：x 方向离图边的余量（px）不够拖 ${DX} px`).toBeGreaterThan(DX + 2)
    if (uy) expect(room[1], `${t.name}：y 方向离图边的余量（px）不够拖 ${DY} px`).toBeGreaterThan(DY + 2)

    await page.mouse.move(sx, sy)
    await page.mouse.down()
    for (let i = 1; i <= 8; i++) await page.mouse.move(sx + (dx * i) / 8, sy + (dy * i) / 8)
    await page.waitForTimeout(150)
    // ① 拖动途中：SVG 组已经跟着手走了（预览平面），还没有任何渲染回来
    const mid = (await boxOf(page, t.measure))!
    await settleAfter(page, () => page.mouse.up())
    // ② 松手、权威成图之后：落点
    const b1 = (await boxOf(page, t.measure))!
    const o1 = (await boxOf(page, by))!
    const err = [b1.x - b0.x - dx, b1.y - b0.y - dy]
    const pre = [mid.x - b0.x - dx, mid.y - b0.y - dy]
    const drift = Math.max(Math.abs(o1.x - o0.x), Math.abs(o1.y - o0.y), Math.abs(o1.w - o0.w), Math.abs(o1.h - o0.h))
    // ③ 撤销一次回原处
    // 撤销回到的那一版可能直接取自渲染缓存（不发请求）：按画面等，等不到就是没回去
    const away = async (ref: Box) => {
      const b = (await boxOf(page, t.measure))!
      return Math.max(Math.abs(b.x - ref.x), Math.abs(b.y - ref.y))
    }
    await page.keyboard.press('Control+z')
    await expect
      .poll(() => away(b0), { timeout: 60_000, message: `${t.name}：撤销一次应当回原处` })
      .toBeLessThanOrEqual(BUDGET_PX)
    await page.waitForTimeout(400)
    const undoErr = await away(b0)
    const row =
      `${t.name}（${t.gid}）余量=(${room.map((v) => v.toFixed(1))}) px 预览误差=(${pre.map((v) => v.toFixed(2))}) 落点误差=(${err.map((v) => v.toFixed(2))}) px ` +
      `别的子图漂移=${drift.toFixed(2)} px 撤销误差=${undoErr.toFixed(2)} px`
    rows.push(row)
    console.log(`[drag-coverage e2e] ${row}`)
    expect(Math.abs(pre[0]), `${t.name}：拖动途中应当跟手（x）`).toBeLessThanOrEqual(1.5)
    expect(Math.abs(pre[1]), `${t.name}：拖动途中应当跟手（y）`).toBeLessThanOrEqual(1.5)
    expect(Math.abs(err[0]), `${t.name}：松手后应当落在拖到的位置（x）`).toBeLessThanOrEqual(BUDGET_PX)
    expect(Math.abs(err[1]), `${t.name}：松手后应当落在拖到的位置（y）`).toBeLessThanOrEqual(BUDGET_PX)
    expect(drift, `${t.name}：不相干的子图不该被挤动`).toBeLessThanOrEqual(BUDGET_PX)
    expect(undoErr, `${t.name}：撤销一次应当回原处`).toBeLessThanOrEqual(BUDGET_PX)

    // 重做回到热态，留着给「重开 == 热态」那一段
    await page.keyboard.press('Control+Shift+z')
    await expect.poll(() => away(b1), { timeout: 60_000, message: `${t.name}：重做` }).toBeLessThanOrEqual(BUDGET_PX)
    await page.waitForTimeout(400)
  }
  await shot('drag-after')
  testInfo.attach('drag-coverage-table', { body: rows.join('\n'), contentType: 'text/plain' })

  // 按设计不能拖的：拖曲线——不写项目、不发渲染，toast 说出为什么
  const renders: string[] = []
  page.on('request', (r) => {
    if (r.url().includes('/api/engine/render')) renders.push(r.url())
  })
  const onCurve = await page.evaluate(() => {
    const p = document.querySelector('[data-element-svg] [id="axes_0.lines_0"] path') as SVGPathElement
    const pt = p.getPointAtLength(p.getTotalLength() * 0.15)
    const m = p.getScreenCTM()!
    return { x: m.a * pt.x + m.c * pt.y + m.e, y: m.b * pt.x + m.d * pt.y + m.f }
  })
  const line0 = await relOf(page, 'axes_0.lines_0')
  await page.mouse.move(onCurve.x, onCurve.y)
  await page.mouse.down()
  for (let i = 1; i <= 6; i++) await page.mouse.move(onCurve.x + 5 * i, onCurve.y + 3 * i)
  await page.mouse.up()
  await expect.poll(() => announced(page), { message: '拖曲线应当说出为什么拖不动' }).toContain('位置由数据决定')
  await page.waitForTimeout(800)
  expect(renders, '拖不动的元素不该发渲染请求').toHaveLength(0)
  expect(await relOf(page, 'axes_0.lines_0')).toEqual(line0)

  // 方向键微调走同一份移动规则（ADR 0093）：新可拖的锚定框同样推得动；选中曲线按方向键
  // 说同一句「为什么」
  const at0 = (await boxOf(page, 'axes_0.artists_0'))!
  await page.mouse.click(at0.x + at0.w / 2, at0.y + at0.h / 2)
  await page.waitForTimeout(300)
  await settleAfter(page, async () => {
    for (let i = 0; i < 4; i++) await page.keyboard.press('ArrowRight')
  })
  const at1 = (await boxOf(page, 'axes_0.artists_0'))!
  console.log(`[drag-coverage e2e] 方向键 ×4 AnchoredText Δ=(${(at1.x - at0.x).toFixed(2)}, ${(at1.y - at0.y).toFixed(2)}) px`)
  expect(at1.x - at0.x, '方向键应当把锚定框往右推').toBeGreaterThan(1)
  expect(Math.abs(at1.y - at0.y)).toBeLessThanOrEqual(BUDGET_PX)
  await page.mouse.click(onCurve.x, onCurve.y)
  await page.waitForTimeout(300)
  await page.keyboard.press('ArrowRight')
  await expect.poll(() => announced(page), { message: '选中曲线按方向键也说为什么' }).toContain('位置由数据决定')

  // 保存、重开：重放出来的位置 == 热态
  const svgW = (await page.locator('[data-element-svg] > svg').boundingBox())!.width
  const hot: Record<string, number[]> = {}
  for (const t of TARGETS) hot[t.measure] = await relOf(page, t.measure)
  await page.keyboard.press('Escape')
  await page.keyboard.press('Escape')
  await page.keyboard.press('Control+s')
  await page.waitForTimeout(1500)
  await page.reload()
  await expect(page.locator('[data-object-id]')).toHaveCount(1, { timeout: 30_000 })
  await page.waitForTimeout(1500)
  await enterFigure(page)
  for (const t of TARGETS) {
    const r = await relOf(page, t.measure)
    const d = [0, 1, 2, 3].map((i) => (r[i] - hot[t.measure][i]) * svgW)
    console.log(`[drag-coverage e2e] 重开 ${t.name} Δ=(${d.map((v) => v.toFixed(3))}) px`)
    for (const v of d) expect(Math.abs(v), `重开：${t.name}`).toBeLessThanOrEqual(BUDGET_PX)
  }
  await shot('reopened')
})
