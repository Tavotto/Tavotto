import type { Page } from '@playwright/test'
import { execFileSync } from 'node:child_process'
import { mkdirSync, mkdtempSync, statSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { expect, openElementsTab, test } from './fixtures'

/**
 * 方向键微调的浏览器腿（ADR 0093）：**真 PanelView、真键盘事件、真后端渲染**。
 *
 * 起点（2026-09-26 实测，改动前的 main）：画布排版里进图内编辑、选中图例按方向键，挪的是
 * **整张图**，图例纹丝不动；快速编辑里按方向键什么都不发生。
 *
 * 独立尺：同一视图倍率下，先用方向键推**画布上的面板**（画布对象的坐标就是页面 mm，
 * 与图内元素无关的另一条路径）量出「一步 = 多少屏幕像素」，再推图内图例，**权威重渲染之后**
 * 图例墨迹框的位移必须等于同样步数 × 那个像素数。图例从 matplotlib 自动摆放换成显式位置那一下
 * 可能有与平台字形度量相关的跳动（#576），先热身一段不计入。
 *
 * 主语：`[data-element-svg] [id="axes_0.legend"]`（matplotlib 输出的 gid，稳定锚点）与
 * `[data-object-id]`（画布对象）；时刻：按键之前 vs 这一段收尾、权威 SVG 换上画布之后。
 */

const LEGEND = 'axes_0.legend'
const BUDGET_PX = 0.5
const N = 6

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

/** 图例在它那张图里的位置（按图宽 / 图高归一）：视口换了（重开后重新适配）也能比 */
const relOf = (page: Page, gid: string) =>
  page.evaluate((id) => {
    const host = document.querySelector('[data-element-svg] > svg')!
    const n = host.querySelector(`[id="${id}"]`)!
    const h = host.getBoundingClientRect()
    const r = (n as SVGGraphicsElement).getBoundingClientRect()
    return [(r.x - h.x) / h.width, (r.y - h.y) / h.height, r.width / h.width, r.height / h.height]
  }, gid)

const panelBox = (page: Page): Promise<Box> =>
  page.evaluate(() => {
    const all = document.querySelectorAll('[data-object-id]')
    if (all.length !== 1) throw new Error(`画布上应恰有一个对象，实际 ${all.length}`)
    const r = all[0].getBoundingClientRect()
    return { x: r.x, y: r.y, w: r.width, h: r.height }
  })

const timingsCount = (page: Page) =>
  page.evaluate(() => {
    const w = window as unknown as { __MM_PREVIEW_TIMINGS__?: unknown[] }
    return w.__MM_PREVIEW_TIMINGS__?.length ?? 0
  })

/** 按 n 下方向键（点按），然后等这一段收尾、权威 SVG 换上画布 */
async function nudgeAndSettle(page: Page, key: string, n: number) {
  const before = await timingsCount(page)
  for (let i = 0; i < n; i++) await page.keyboard.press(key)
  await expect
    .poll(() => timingsCount(page), { timeout: 60_000, message: '这一段收尾后权威 SVG 应当换上画布' })
    .toBeGreaterThan(before)
  await page.waitForTimeout(300)
}

async function addFigureToCanvas(page: Page, baseURL: string, file = 'Fig1_kinetics.pdf') {
  await page.goto(baseURL)
  // 素材卡认稳定锚点 data-card；Shift+Enter = 添加到画布
  const card = page.locator(`[data-card="${file}"]`)
  await card.focus({ timeout: 30_000 })
  await page.keyboard.press('Shift+Enter')
  await expect(page.locator('[data-object-id]')).toHaveCount(1, { timeout: 30_000 })
  await page.waitForTimeout(1500)
}

async function enterFigure(page: Page) {
  const p = await panelBox(page)
  await page.mouse.click(p.x + p.w / 2, p.y + p.h / 2)
  await page.keyboard.press('Enter')
  await expect(page.locator('[data-element-svg] > svg')).toBeVisible({ timeout: 60_000 })
  await page.waitForTimeout(1500)
}

async function selectLegend(page: Page) {
  const b = (await boxOf(page, LEGEND))!
  await page.mouse.click(b.x + b.w / 2, b.y + b.h / 2)
  await page.waitForTimeout(300)
}

test('画布里选中图内图例按 → N 次：图例挪 N 步、面板不动；撤销一次回原位；保存重开位置一致', async ({
  app,
  page,
}) => {
  const a = await app()
  const renders: string[] = []
  page.on('request', (r) => {
    if (r.url().includes('/api/engine/render')) renders.push(r.url())
  })
  await addFigureToCanvas(page, a.baseURL)

  // 尺子：画布对象的一步（页面 0.5 mm）在屏幕上是多少像素。之后不改视图倍率
  const p0 = await panelBox(page)
  await page.mouse.click(p0.x + p0.w / 2, p0.y + p0.h / 2)
  for (let i = 0; i < N; i++) await page.keyboard.press('ArrowRight')
  await page.waitForTimeout(600)
  const p1 = await panelBox(page)
  const stepPx = (p1.x - p0.x) / N
  console.log(`[arrow-nudge e2e] 画布对象一步 = ${stepPx.toFixed(3)} px；面板宽 ${((p0.w / stepPx) * 0.5).toFixed(2)} mm`)
  expect(stepPx, '画布对象一步应当大于 0（尺子是活的）').toBeGreaterThan(0.5)
  expect(Math.abs(p1.y - p0.y)).toBeLessThan(0.01)

  await enterFigure(page)
  await selectLegend(page)
  // 热身一段（不计入）：图例从自动摆放换成显式 loc_frac 那一下可能有平台相关的跳动（#576）
  await nudgeAndSettle(page, 'ArrowUp', 1)
  const panelBefore = await panelBox(page)
  const l0 = (await boxOf(page, LEGEND))!
  const rel0 = await relOf(page, LEGEND)

  // 这一段里零渲染：渲染请求只在收尾时发
  const rendersBefore = renders.length
  for (let i = 0; i < N; i++) await page.keyboard.press('ArrowRight')
  const during = renders.length - rendersBefore
  await expect
    .poll(async () => (await boxOf(page, LEGEND))!.x - l0.x, { timeout: 60_000 })
    .toBeGreaterThan(stepPx * N * 0.5)
  await page.waitForTimeout(1500)
  const l1 = (await boxOf(page, LEGEND))!
  const panelAfter = await panelBox(page)
  const err = [l1.x - l0.x - N * stepPx, l1.y - l0.y]
  console.log(
    `[arrow-nudge e2e] 图例 Δ=(${(l1.x - l0.x).toFixed(3)}, ${(l1.y - l0.y).toFixed(3)}) 期望 ${(N * stepPx).toFixed(3)} ` +
      `误差=(${err.map((v) => v.toFixed(3))}) 按键期间渲染请求 ${during} 收尾后 ${renders.length - rendersBefore}`,
  )
  expect(during, '按键期间不应发渲染请求').toBe(0)
  expect(renders.length - rendersBefore, '这一段收尾只发一次渲染').toBe(1)
  expect(Math.abs(err[0])).toBeLessThanOrEqual(BUDGET_PX)
  expect(Math.abs(err[1])).toBeLessThanOrEqual(BUDGET_PX)
  expect(panelAfter.x, '图内微调不能推整张图').toBeCloseTo(panelBefore.x, 2)
  expect(panelAfter.y).toBeCloseTo(panelBefore.y, 2)
  const hot = await relOf(page, LEGEND)

  // 撤销一次：这一段 N 下整个回去
  await page.keyboard.press('Control+z')
  await expect
    .poll(async () => Math.abs((await boxOf(page, LEGEND))!.x - l0.x), {
      timeout: 60_000,
      message: '撤销一次应当回到这一段之前',
    })
    .toBeLessThanOrEqual(BUDGET_PX)
  expect(Math.abs((await boxOf(page, LEGEND))!.y - l0.y)).toBeLessThanOrEqual(BUDGET_PX)
  expect((await relOf(page, LEGEND))[0]).toBeCloseTo(rel0[0], 3)

  // 重做回到热态，保存、重开：重放出来的位置 == 热态
  await page.keyboard.press('Control+Shift+z')
  await expect
    .poll(async () => Math.abs((await relOf(page, LEGEND))[0] - hot[0]), { timeout: 60_000 })
    .toBeLessThan(1e-3)
  await page.keyboard.press('Escape')
  await page.keyboard.press('Escape')
  await page.keyboard.press('Control+s')
  await page.waitForTimeout(1500)
  await page.reload()
  await expect(page.locator('[data-object-id]')).toHaveCount(1, { timeout: 30_000 })
  await page.waitForTimeout(1500)
  await enterFigure(page)
  const replay = await relOf(page, LEGEND)
  const w = (await page.locator('[data-element-svg] > svg').boundingBox())!.width
  console.log(
    `[arrow-nudge e2e] 热态 vs 重开 Δ=(${((replay[0] - hot[0]) * w).toFixed(3)}, ${((replay[1] - hot[1]) * w).toFixed(3)}) px`,
  )
  expect(Math.abs(replay[0] - hot[0]) * w).toBeLessThanOrEqual(BUDGET_PX)
  expect(Math.abs(replay[1] - hot[1]) * w).toBeLessThanOrEqual(BUDGET_PX)
})

test('快速编辑里选中图例按方向键：图例动（以前纹丝不动），一段一条撤销', async ({ app, page }) => {
  const a = await app()
  await page.goto(a.baseURL)
  await page.locator('[data-card="Fig1_kinetics.pdf"]').dblclick({ timeout: 30_000 })
  await expect(page.locator('[data-element-svg] > svg')).toBeVisible({ timeout: 60_000 })
  await page.waitForTimeout(2500)
  await selectLegend(page)
  const l0 = (await boxOf(page, LEGEND))!
  await nudgeAndSettle(page, 'ArrowDown', 4)
  const l1 = (await boxOf(page, LEGEND))!
  console.log(`[arrow-nudge e2e] 快速编辑 图例 Δ=(${(l1.x - l0.x).toFixed(3)}, ${(l1.y - l0.y).toFixed(3)})`)
  expect(l1.y - l0.y, '↓ 应当把图例往下挪').toBeGreaterThan(1)
  expect(Math.abs(l1.x - l0.x)).toBeLessThanOrEqual(BUDGET_PX)

  await page.keyboard.press('Control+z')
  await expect
    .poll(async () => Math.abs((await boxOf(page, LEGEND))!.y - l0.y), { timeout: 60_000 })
    .toBeLessThanOrEqual(BUDGET_PX)
})

/* ======================= 图内各类可拖对象（硬性验收） ======================= */

/**
 * 现造一张带各类图内对象的图：标题、轴标题、图例、自由文字、带文字的标注（annotate
 * 带箭头）、纯箭头（`annotate("", …)`）、子图。示例图库里没有标注与箭头，
 * 做法与 `element-path-selection.spec.ts` 的 markerOnlyLibrary 相同（worker 那一侧的解释器）。
 */
function annotatedLibrary(): string {
  const dir = path.join(mkdtempSync(path.join(os.tmpdir(), 'tavotto-e2e-nudge-')), 'figures')
  mkdirSync(dir)
  writeFileSync(
    path.join(dir, 'fig_annot.py'),
    [
      'import matplotlib',
      'matplotlib.use("Agg")',
      'import matplotlib.pyplot as plt',
      'import numpy as np',
      '',
      '',
      'def main():',
      '    t = np.linspace(0, 10, 100)',
      '    fig, ax = plt.subplots(figsize=(8 / 2.54, 8 / 2.54 * 0.72))',
      // 默认字号下 y 轴标题会被挤到图幅左边之外（编辑面的 SVG 按图幅裁掉它，点不中）；
      // 右边留出余量：子图贴到图幅边上会被钳住（与拖动同一条规则），挪不满 N 步
      '    fig.subplots_adjust(left=0.2, bottom=0.15, right=0.85, top=0.85)',
      '    ax.plot(t, np.sin(t), label="sin")',
      '    ax.plot(t, np.cos(t), label="cos")',
      '    ax.set_title("Annotated")',
      '    ax.set_ylabel("Signal")',
      '    ax.text(1.0, -0.8, "Region A")',
      '    ax.annotate("Peak", xy=(1.57, 1.0), xytext=(3.2, 0.55), arrowprops=dict(arrowstyle="->"))',
      '    ax.annotate("", xy=(7.5, -0.9), xytext=(5.5, -0.3), arrowprops=dict(arrowstyle="->"))',
      '    ax.set_ylim(-1.3, 1.3)',
      '    ax.legend(loc="upper right")',
      '    fig.savefig("Fig_annot.pdf", bbox_inches="tight", pad_inches=0.02)',
      '',
    ].join('\n'),
    'utf-8',
  )
  writeFileSync(
    path.join(dir, 'tavotto_registry.json'),
    JSON.stringify({
      scripts: { 'fig_annot.py': { entry: 'main', cost: 'light', stems: ['Fig_annot'] } },
    }),
    'utf-8',
  )
  const fallback = process.platform === 'win32' ? 'python' : 'python3'
  const py = process.env.TAVOTTO_WORKER_PYTHON || fallback
  execFileSync(py, ['-c', 'import fig_annot; fig_annot.main()'], { cwd: dir, timeout: 120_000 })
  return dir
}

/**
 * 被测对象：gid、在它**此刻**的墨迹框里点哪儿才只落在它身上（框内比例；null = 从元素树选），
 * 以及量哪条边。点的位置每次现量：先挪过的子图会把数据坐标里的文字、箭头一起带走，
 * 起手时量的点到后面就点空了。带文字的标注挪的是文字（xytext），箭头尖钉在原处，所以点文字
 * 那一角、量右边缘；其余对象整体平移，量左上角。纯箭头的框心就是直线中点。
 */
const TARGETS: { gid: string; name: string; at: [number, number] | null; edge: 'box' | 'right' }[] = [
  // 子图最先挪：之后别的对象带上显式位置，就不再跟着子图的 <g> 走了。子图里点空白处
  // 很难不落在曲线的命中带上，改从元素树选中——键盘用户本来就这么选
  { gid: 'axes_0', name: '子图', at: null, edge: 'box' },
  { gid: 'axes_0.legend', name: '图例', at: [0.3, 0.5], edge: 'box' },
  { gid: 'axes_0.texts_0', name: '文字', at: [0.5, 0.5], edge: 'box' },
  { gid: 'axes_0.texts_1', name: '带文字的标注', at: [0.8, 0.8], edge: 'right' },
  { gid: 'axes_0.texts_2.arrow', name: '纯箭头', at: [0.5, 0.5], edge: 'box' },
  { gid: 'axes_0.title', name: '标题', at: [0.5, 0.5], edge: 'box' },
  { gid: 'axes_0.ylabel', name: 'y 轴标题', at: [0.5, 0.5], edge: 'box' },
]

/** 子图里的曲线：只有子图挪了它才动——量「这一下挪的真是选中的那个，不是整个子图」 */
const REFERENCE = 'axes_0.lines_0'

/** 从元素树选中，再把焦点从树上挪开（焦点留在树里时方向键归树） */
async function selectInTree(page: Page, name: RegExp) {
  await openElementsTab(page)
  await page.getByRole('treeitem', { name }).first().click()
  await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur())
  await page.waitForTimeout(300)
}

async function clickIn(page: Page, gid: string, at: [number, number]) {
  const b = (await boxOf(page, gid))!
  await page.mouse.click(b.x + at[0] * b.w, b.y + at[1] * b.h)
  await page.waitForTimeout(300)
}

const edgeX = (b: Box, edge: 'box' | 'right') => (edge === 'right' ? b.x + b.w : b.x)

async function relAll(page: Page) {
  const out: Record<string, number[]> = {}
  for (const t of TARGETS) out[t.gid] = await relOf(page, t.gid)
  return out
}

function expectSameLayout(a: Record<string, number[]>, b: Record<string, number[]>, w: number, what: string) {
  for (const t of TARGETS) {
    const d = [0, 1, 2, 3].map((i) => (b[t.gid][i] - a[t.gid][i]) * w)
    console.log(`[arrow-nudge e2e] ${what} ${t.name} Δ=(${d.map((v) => v.toFixed(3))}) px`)
    for (const v of d) expect(Math.abs(v), `${what}：${t.name}`).toBeLessThanOrEqual(BUDGET_PX)
  }
}

test('图内各类可拖对象都能用方向键微调：挪 N 步 = N × 页面步长；写回通过干净重放校验；重开 == 热态', async ({
  app,
  page,
}, testInfo) => {
  const dir = annotatedLibrary()
  const pdf = path.join(dir, 'Fig_annot.pdf')
  const a = await app({ figures: dir })
  await addFigureToCanvas(page, a.baseURL, 'Fig_annot.pdf')
  // 左栏先切到元素树（子图要从树里选）：抽屉开合会让画布视口平移，尺子与「面板不动」
  // 都要在同一个视口里量
  await openElementsTab(page)
  await page.waitForTimeout(500)

  // 尺子：画布对象的一步（页面 0.5 mm）在屏幕上是多少像素
  const p0 = await panelBox(page)
  await page.mouse.click(p0.x + p0.w / 2, p0.y + p0.h / 2)
  for (let i = 0; i < N; i++) await page.keyboard.press('ArrowRight')
  await page.waitForTimeout(600)
  const stepPx = ((await panelBox(page)).x - p0.x) / N
  expect(stepPx).toBeGreaterThan(0.5)

  await enterFigure(page)
  const host = page.locator('[data-element-svg]')
  // 截图留证（test-results/ 下，CI 随失败产物一起收）
  const shot = async (name: string) => {
    const file = testInfo.outputPath(`${name}.png`)
    await host.screenshot({ path: file })
    await testInfo.attach(name, { path: file, contentType: 'image/png' })
  }
  await shot('in-figure-before')
  const panelBefore = await panelBox(page)

  for (const t of TARGETS) {
    if (t.at) await clickIn(page, t.gid, t.at)
    else await selectInTree(page, /^子图 1/)
    // 热身（不计入）：从 matplotlib 自动摆放换成显式位置那一下可能有平台相关的跳动（#576）
    await nudgeAndSettle(page, 'ArrowUp', 1)
    const b0 = (await boxOf(page, t.gid))!
    const r0 = (await boxOf(page, REFERENCE))!
    await nudgeAndSettle(page, 'ArrowRight', N)
    const b1 = (await boxOf(page, t.gid))!
    const r1 = (await boxOf(page, REFERENCE))!
    if (t.gid !== 'axes_0') {
      expect(Math.abs(r1.x - r0.x), `挪${t.name}时子图不该动（选中的不是它？）`).toBeLessThanOrEqual(BUDGET_PX)
    }
    const dx = edgeX(b1, t.edge) - edgeX(b0, t.edge)
    console.log(
      `[arrow-nudge e2e] ${t.name}（${t.gid}）Δx=${dx.toFixed(3)} 期望 ${(N * stepPx).toFixed(3)} Δy=${(b1.y - b0.y).toFixed(3)}`,
    )
    expect(Math.abs(dx - N * stepPx), `${t.name} 应当挪 ${N} 步`).toBeLessThanOrEqual(BUDGET_PX)
    expect(Math.abs(b1.y + (t.edge === 'right' ? b1.h : 0) - b0.y - (t.edge === 'right' ? b0.h : 0))).toBeLessThanOrEqual(BUDGET_PX)
  }
  const panelAfter = await panelBox(page)
  expect(panelAfter.x, '图内微调不能推整张图').toBeCloseTo(panelBefore.x, 2)
  await shot('in-figure-after')
  const svgW = (await page.locator('[data-element-svg] > svg').boundingBox())!.width
  const hot = await relAll(page)

  // 写回原始文件（与拖动同一条写回路径：prepare → verify → commit，过不了就 409 且原件不动）
  await page.keyboard.press('Escape')
  await page.keyboard.press('Escape')
  const mtime0 = statSync(pdf).mtimeMs
  await page.locator('[data-write-back="open"]').first().click()
  const dialog = page.getByRole('dialog').first()
  await dialog.locator('[data-write-back="confirm"]').click()
  await expect(dialog.locator('[data-write-back="confirm"]')).toHaveCount(0, { timeout: 180_000 })
  // 写回事务的 verify 段：全量干净重放 + 几何比对 + 像素门都过了才会 commit
  await expect(dialog.getByText(/已通过干净重放校验/)).toBeVisible()
  await expect.poll(() => statSync(pdf).mtimeMs, { message: '写回应当改写原始 PDF' }).toBeGreaterThan(mtime0)
  await page.keyboard.press('Escape')

  // 重开同一个实例：重放 == 热态
  await page.reload()
  await expect(page.locator('[data-object-id]')).toHaveCount(1, { timeout: 30_000 })
  await page.waitForTimeout(1500)
  await enterFigure(page)
  expectSameLayout(hot, await relAll(page), svgW, '重开')

  // 写回后的原件（PDF 的栅格预览）留作截图证据：写回事务自己的 verify 段已经做过「热态 vs
  // 干净重放」的几何比对与像素门（上面的「已通过干净重放校验」），这里只把原件的样子留下来
  const png = await page.request.get(`${a.baseURL}/api/render?id=Fig_annot.pdf&w=1200&m=${Math.round(statSync(pdf).mtimeMs)}`)
  if (png.ok()) {
    const file = testInfo.outputPath('written-original.png')
    writeFileSync(file, await png.body())
    await testInfo.attach('written-original', { path: file, contentType: png.headers()['content-type'] })
  }
  await shot('reopened')
})
