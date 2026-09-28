import { execFileSync } from 'node:child_process'
import { mkdirSync, mkdtempSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import type { Page } from '@playwright/test'
import { expect, test } from './fixtures'

/**
 * 真浏览器里跑一遍「选中曲线沿真实路径、bbox 空白角不再误命中」。
 *
 * 为什么值得单独一条 e2e：jsdom 那两套用例喂的是手写的 manifest，验的是
 * 结构与算术；**只有真浏览器 + 真 matplotlib** 才能回答「引擎真的把
 * geometry 发过来了吗」「点在真曲线上真的选中它了吗」。这条用例把整条链
 * 走通：引擎算路径 → 响应带 geometry → 命中按路径 → 覆盖层画 path。
 *
 * 取点用 `SVGPathElement.getPointAtLength()` + `getScreenCTM()`——曲线上的
 * 精确一点只有浏览器算得出来，猜 bbox 中点在弯曲的曲线上会落空。
 */

interface Pt {
  x: number
  y: number
}
/** 一块墨迹：中心 + 半径（曲线的采样点半径为 0） */
interface Ink extends Pt {
  r: number
}
interface Box {
  x0: number
  x1: number
  y0: number
  y1: number
}

/** 一组点的包围盒 */
function bboxOf(pts: Pt[]): Box {
  const xs = pts.map((p) => p.x)
  const ys = pts.map((p) => p.y)
  return { x0: Math.min(...xs), x1: Math.max(...xs), y0: Math.min(...ys), y1: Math.max(...ys) }
}

/**
 * 坐标轴边框两侧各 `ZONE_PX.band` 屏幕像素是「切这一边刻度」的命中带
 * （`lib/tickSides.ts` 的 `ZONE_PX`，ADR 0035：按**屏幕像素**定宽、缩放不变）。
 * 点进去是一次刻度操作，选区照旧——那一下根本不是在验「空白处是否误命中」。
 * 这里不能 import 产品常量（e2e 的 tsconfig 不解析 `@/`），所以照抄一份并点名
 * 出处：产品那边把带加宽的话这里会**红**（点进带里），不会假绿。
 * 命中层用的是 manifest 的边框分数 × 布局 × zoom，这里量的是 SVG 的 DOM 矩形，
 * 两把尺子差一个亚像素与半个线宽，再留 4px。
 */
const SPINE_BAND_PX = 10
const SPINE_CLEARANCE_PX = SPINE_BAND_PX + 4

/**
 * 整张图里的墨迹与边框（屏幕坐标），从 SVG 的 DOM 上量、一次量齐——这把尺子
 * 与命中层（manifest 的 geometry）无关，所以「点在墨迹上 / 不在墨迹上」的结论
 * 不是产品自己验自己：
 *
 * - `ink`：每颗 marker 的中心与半径（散点 / 只有 marker 的曲线都是 `<defs>` 模板 +
 *   每颗一个 `<use>`）+ 每条曲线的沿线采样——点中任何一条都会在覆盖层画出 path；
 * - `spines`：边框线的采样点。边框是 matplotlib 留在 axes 组里的 `patch_N`（背景
 *   矩形 + 四条 spine，引擎不给它们改名），背景矩形的轮廓与四条 spine 重合
 *   （偏出去的边框另算一份），一起采无妨；
 * - `layer`：命中层（= 整张图）在屏幕上的矩形，manifest 的分数 bbox 靠它换算。
 */
function inkInBrowser(): { ink: Ink[]; spines: Pt[]; layer: Box } {
  const svg = document.querySelector('[data-element-svg] svg')
  const ink: Ink[] = []
  const spines: Pt[] = []
  const lr = document.querySelector('[data-authority]')?.getBoundingClientRect()
  const layer: Box = lr
    ? { x0: lr.x, x1: lr.x + lr.width, y0: lr.y, y1: lr.y + lr.height }
    : { x0: 0, x1: 0, y0: 0, y1: 0 }
  if (!svg) return { ink, spines, layer }
  const sample = (path: SVGPathElement, into: Pt[]) => {
    if (!path.getTotalLength) return
    const ctm = path.getScreenCTM()
    if (!ctm) return
    const len = path.getTotalLength()
    for (let i = 0; i <= 200; i++) {
      const q = path.getPointAtLength((len * i) / 200)
      into.push({ x: q.x * ctm.a + q.y * ctm.c + ctm.e, y: q.x * ctm.b + q.y * ctm.d + ctm.f })
    }
  }
  for (const g of svg.querySelectorAll('[id^="axes_"]')) {
    if (!g.id.includes('.')) {
      for (const patch of g.querySelectorAll(':scope > [id^="patch_"]')) {
        const path = patch.querySelector('path')
        if (path) sample(path as SVGPathElement, spines)
      }
      continue
    }
    if (/\.(lines|scatter)_\d+$/.test(g.id)) {
      for (const u of g.querySelectorAll('use')) {
        const r = u.getBoundingClientRect()
        ink.push({ x: r.x + r.width / 2, y: r.y + r.height / 2, r: Math.max(r.width, r.height) / 2 })
      }
      const path = g.querySelector('path')
      const pts: Pt[] = []
      if (path) sample(path as SVGPathElement, pts)
      for (const q of pts) ink.push({ ...q, r: 0 })
    }
  }
  return { ink, spines, layer }
}

/** render 响应里 manifest 的最小形状：这里只用得着 bbox 命中那几个字段 */
interface RenderManifest {
  elements: {
    gid: string
    role: string
    bbox: [number, number, number, number]
    geometry?: unknown
  }[]
}

/**
 * 从 `goto` 之前就挂上：记住最近一次 `/api/engine/render` 响应里的 manifest。
 * 命中层认的就是这一份（ADR 0017 的几何权威），取最后一次与它同步。
 */
function captureManifest(page: Page): () => RenderManifest | null {
  let latest: RenderManifest | null = null
  page.on('response', async (res) => {
    if (!res.url().includes('/api/engine/render')) return
    try {
      const body = (await res.json()) as { manifest?: RenderManifest }
      if (body?.manifest) latest = body.manifest
    } catch {
      /* 不是 JSON 就跳过 */
    }
  })
  return () => latest
}

/**
 * 命中层给 bbox 外扩的余量（`pickElementStack` 的 `PAD`，图幅分数），照抄一份；
 * 再加 1px 盖住换算的亚像素
 */
const HIT_PAD_FRAC = 0.004

/**
 * 其余元素（图例 / 标题 / 轴标题 / 文字 / 刻度……）的命中区，换到屏幕坐标。
 * 它们按 **manifest 的 bbox** 命中（图例的 bbox 含看不见的边框内边距，比 SVG 上
 * 画出来的那块大好几个像素），所以这一项**必须**用产品自己的尺子——问的是
 * 「点这儿会不会选中别的东西」，答案由命中层定义。容器（axes）不排除：点空白
 * 本来就该落到它身上。
 */
function hitBoxes(manifest: RenderManifest, layer: Box): Box[] {
  const w = layer.x1 - layer.x0
  const h = layer.y1 - layer.y0
  const pad = HIT_PAD_FRAC * Math.max(w, h) + 1
  return manifest.elements
    .filter((e) => e.gid !== 'figure' && e.role !== 'axes' && e.role !== 'axes3d' && !e.geometry)
    .map(({ bbox: [x, y, bw, bh] }) => ({
      x0: layer.x0 + x * w - pad,
      x1: layer.x0 + (x + bw) * w + pad,
      y0: layer.y0 + y * h - pad,
      y1: layer.y0 + (y + bh) * h + pad,
    }))
}

/**
 * 在候选点里挑「空白」：离每块墨迹最远，**且**不落进边框命中带、不落在别的元素
 * 的 bbox 里。回 null = 没有候选够得上「空白」。
 */
function blankSpot(
  candidates: Pt[],
  { ink, spines, boxes }: { ink: Ink[]; spines: Pt[]; boxes: Box[] },
): (Pt & { dist: number; spine: number }) | null {
  let best: (Pt & { dist: number; spine: number }) | null = null
  for (const c of candidates) {
    const spine = Math.min(Infinity, ...spines.map((q) => Math.hypot(q.x - c.x, q.y - c.y)))
    if (spine <= SPINE_CLEARANCE_PX) continue
    if (boxes.some((b) => c.x >= b.x0 && c.x <= b.x1 && c.y >= b.y0 && c.y <= b.y1)) continue
    const dist = Math.min(...ink.map((q) => Math.hypot(q.x - c.x, q.y - c.y) - q.r))
    if (!best || dist > best.dist) best = { ...c, dist, spine }
  }
  return best
}

/** 一块矩形范围里 39×39 个网格点（不含边界） */
function gridInside({ x0, x1, y0, y1 }: Box): Pt[] {
  const pts: Pt[] = []
  for (let i = 1; i < 40; i++) {
    for (let j = 1; j < 40; j++) {
      pts.push({ x: x0 + ((x1 - x0) * i) / 40, y: y0 + ((y1 - y0) * j) / 40 })
    }
  }
  return pts
}

/** 覆盖层里当前的描示：沿路径的 path 各有几段子路径、几个带底色的矩形选中框 */
const overlayOf = (page: Page) =>
  page.evaluate(() => {
    const svg = document.querySelector('[data-overlay-svg]') as SVGSVGElement | null
    const ds = [...(svg?.querySelectorAll('path[d]') ?? [])]
      .map((p) => p.getAttribute('d') ?? '')
      .filter((d) => d.startsWith('M'))
    const rects = svg?.querySelectorAll('rect[fill-opacity]').length ?? 0
    return {
      paths: ds.length,
      subpaths: ds.map((d) => d.match(/M/g)?.length ?? 0),
      closed: ds.map((d) => d.match(/Z/g)?.length ?? 0),
      rects,
    }
  })

test('图内曲线：沿真实路径选中，bbox 空白角不误命中', async ({ app, page }) => {
  const a = await app()

  // 引擎的 render 响应里必须真的带上 geometry（这一步断的是「后端有没有发」）
  let sawGeometry = false
  page.on('response', async (res) => {
    if (!res.url().includes('/api/engine/render')) return
    try {
      const body = await res.json()
      const els = body?.manifest?.elements ?? []
      if (els.some((e: { geometry?: unknown }) => e.geometry)) sawGeometry = true
    } catch {
      /* 不是 JSON 就跳过 */
    }
  })

  const manifestOf = captureManifest(page)

  await page.goto(a.baseURL)
  // Prompt 09 起，双击素材卡 = 打开这张图（快速编辑工作区），**当场就在图内
  // 编辑态**——不再需要先「加入画布」再点一次「编辑图内元素」。
  await page.getByText('Fig1_kinetics.pdf').dblclick({ timeout: 30_000 })
  const svgWrap = page.locator('[data-element-svg]').first()
  await expect(svgWrap.locator('svg')).toBeVisible({ timeout: 60_000 })
  await page.waitForTimeout(1500)
  expect(sawGeometry, 'manifest 里应当带上 geometry').toBe(true)

  /** 目标曲线上的一个精确点 + 它的 bbox（屏幕坐标） */
  const probe = await page.evaluate(() => {
    const svg = document.querySelector('[data-element-svg] svg')
    if (!svg) return null
    for (const g of svg.querySelectorAll('[id^="axes_"]')) {
      if (!/\.lines_\d+$/.test(g.id)) continue
      const path = g.querySelector('path') as SVGPathElement | null
      if (!path?.getTotalLength) continue
      const len = path.getTotalLength()
      if (len < 40) continue
      const ctm = path.getScreenCTM()
      if (!ctm) continue
      const p = path.getPointAtLength(len / 2)
      const r = path.getBoundingClientRect()
      if (r.width < 30 || r.height < 30) continue // 扁平线没有「空白角」可言
      return {
        id: g.id,
        mid: { x: p.x * ctm.a + p.y * ctm.c + ctm.e, y: p.x * ctm.b + p.y * ctm.d + ctm.f },
        box: { x0: r.x, x1: r.x + r.width, y0: r.y, y1: r.y + r.height },
      }
    }
    return null
  })
  expect(probe, '图里应当有一条有起伏的曲线').not.toBeNull()
  const ink = await page.evaluate(inkInBrowser)
  expect(ink.spines.length, '图里应当量得到坐标轴边框').toBeGreaterThan(0)
  expect(manifestOf(), '应当截到 render 响应里的 manifest').not.toBeNull()
  const boxes = hitBoxes(manifestOf()!, ink.layer)

  // 1) 点在曲线**本身**上 → 沿路径描示，没有带底色的矩形选中框
  await page.mouse.click(probe!.mid.x, probe!.mid.y)
  await page.waitForTimeout(300)
  const onCurve = await overlayOf(page)
  expect(onCurve.paths, '选中曲线应当画一条沿真实路径的 path').toBeGreaterThan(0)
  expect(onCurve.rects, '曲线不该再有带底色的矩形选中框').toBe(0)

  // 2) 点在曲线 bbox 里离曲线最远的空白（多半在某个角附近）→ 选中的不再是曲线。
  //    曲线不一定从哪个角附近经过，所以在 bbox 里搜而不是猜角；一条铺满绘图区的
  //    曲线，它的 bbox 四角就贴着边框——那儿是刻度命中带，点下去是切刻度
  const corner = blankSpot(gridInside(probe!.box), { ...ink, boxes })
  expect(corner, '曲线 bbox 里应当有既不在边框命中带里、也不压着别的元素的点').not.toBeNull()
  expect(corner!.dist, 'bbox 里的空白点到曲线应当有足够距离才算「空白」').toBeGreaterThan(20)

  await page.mouse.click(corner!.x, corner!.y)
  await page.waitForTimeout(300)
  const atCorner = await overlayOf(page)
  expect(
    atCorner.paths,
    `点 bbox 空白角（离曲线 ${Math.round(corner!.dist)}px、离边框 ${Math.round(corner!.spine)}px）不该还选中曲线`,
  ).toBe(0)
})

/**
 * 散点（PathCollection）：选中时描的是**每一颗 marker 的轮廓**，不是罩住整组
 * 的大矩形（用户反馈 2026-09-06）。jsdom 那条用例喂的是手写 geometry；这里要
 * 回答的是「引擎真的把每颗 marker 的轮廓发过来了吗」「点在两颗点之间的空白
 * （仍在整组 bbox 里）真的不再选中整组散点了吗」。
 *
 * marker 的屏幕位置从 matplotlib SVG 的 `<use>` 上量（散点的 SVG 是一个 `<defs>`
 * 模板 + 每颗一个 `<use>`），不猜 bbox 中点。
 *
 * 「空白」要同时避开 marker、拟合线**与边框命中带**：整组 bbox 的角落往往贴着
 * 边框，而命中带按屏幕像素定宽——图显示得越小，带盖住 bbox 的份额越大。
 * 「适应画布」成为模式之后（审计 B01）快速编辑里的图按侧栏展开后的舞台适配，
 * 比此前小了一截，原先那个「空白点」就落进了下边框的带里：那一下切了刻度、
 * 选区照旧，于是 60 段子路径原样留在覆盖层（PR #337 posix-e2e）。
 */
test('图内散点：描每颗 marker 的轮廓，两颗之间的空白不误命中', async ({ app, page }) => {
  const a = await app()
  const manifestOf = captureManifest(page)
  await page.goto(a.baseURL)
  await page.getByText('Fig2_correlation.pdf').dblclick({ timeout: 30_000 })
  const svgWrap = page.locator('[data-element-svg]').first()
  await expect(svgWrap.locator('svg')).toBeVisible({ timeout: 60_000 })
  await page.waitForTimeout(1500)

  /** 每颗 marker 的屏幕中心 */
  const probe = await page.evaluate(() => {
    const svg = document.querySelector('[data-element-svg] svg')
    if (!svg) return null
    const group = [...svg.querySelectorAll('[id^="axes_"]')].find((g) =>
      /\.scatter_\d+$/.test(g.id),
    )
    if (!group) return null
    const markers = [...group.querySelectorAll('use')].map((u) => {
      const r = u.getBoundingClientRect()
      return { x: r.x + r.width / 2, y: r.y + r.height / 2, r: Math.max(r.width, r.height) / 2 }
    })
    return { id: group.id, markers }
  })
  expect(probe, '图里应当有一组散点').not.toBeNull()
  expect(probe!.markers.length).toBeGreaterThan(10)
  const ink = await page.evaluate(inkInBrowser)
  expect(ink.spines.length, '图里应当量得到坐标轴边框').toBeGreaterThan(0)
  expect(manifestOf(), '应当截到 render 响应里的 manifest').not.toBeNull()
  const boxes = hitBoxes(manifestOf()!, ink.layer)

  // 1) 点在某一颗 marker 的正中 → 一条 path 里有「每颗一段」的闭合子路径，没有矩形框
  const m = probe!.markers[Math.floor(probe!.markers.length / 2)]
  await page.mouse.click(m.x, m.y)
  await page.waitForTimeout(300)
  const onMarker = await overlayOf(page)
  expect(onMarker.rects, '散点不该再有罩住整组的矩形选中框').toBe(0)
  expect(onMarker.paths, '选中散点应当画出沿 marker 轮廓的 path').toBeGreaterThan(0)
  expect(
    Math.max(...onMarker.subpaths),
    '子路径数应当等于 marker 数（每颗一条），且全部收在一个 path 节点里',
  ).toBe(probe!.markers.length)

  // 2) 点在整组 bbox 里、离每颗 marker 与拟合线都最远、又不在边框命中带里、
  //    不压着图例的空白处 → 不再选中散点
  const gap = blankSpot(gridInside(bboxOf(probe!.markers)), { ...ink, boxes })
  expect(gap, '整组 bbox 里应当有既不在边框命中带里、也不压着别的元素的点').not.toBeNull()
  expect(gap!.dist, '散点之间应当有一块足够大的空白才算得上「空白」').toBeGreaterThan(12)
  await page.mouse.click(gap!.x, gap!.y)
  await page.waitForTimeout(300)
  const atGap = await overlayOf(page)
  expect(
    Math.max(0, ...atGap.subpaths),
    `点两颗 marker 之间的空白（离最近墨迹 ${Math.round(gap!.dist)}px、离边框 ${Math.round(gap!.spine)}px）不该还选中整组散点`,
  ).toBeLessThan(probe!.markers.length)
})

/**
 * 只有 marker 没有连线的 Line2D（`plot(x, y, ls="None", marker="o")`）：用户这样画
 * 出来的「散点图」选中时同样要**逐颗描 marker 的轮廓**，不是一个大矩形，更不是
 * 那条图上并不存在的折线（用户反馈 2026-09-06 第 2 条的延伸）。
 *
 * 示例图库里没有这样画的图，所以图库在这里现造：一个脚本、一张真图，用 worker
 * 那一侧的解释器跑出来（`large-figure.spec.ts` 同一条路——`TAVOTTO_PYTHON`
 * 是 Flask 侧的解释器，按依赖边界它刻意不装 matplotlib）。
 * marker 的屏幕位置照旧从 matplotlib SVG 的 `<use>` 上量。
 */
function markerOnlyLibrary(): string {
  const dir = path.join(mkdtempSync(path.join(os.tmpdir(), 'tavotto-e2e-markers-')), 'figures')
  mkdirSync(dir)
  writeFileSync(
    path.join(dir, 'fig_markers.py'),
    [
      'import matplotlib',
      'matplotlib.use("Agg")',
      'import matplotlib.pyplot as plt',
      'import numpy as np',
      '',
      '',
      'def main():',
      '    rng = np.random.RandomState(7)',
      '    fig, ax = plt.subplots(figsize=(4.0, 3.0))',
      '    ax.plot(rng.uniform(0, 10, 24), rng.uniform(0, 10, 24), ls="None", marker="o", ms=7)',
      '    ax.set_xlim(-1, 11)',
      '    ax.set_ylim(-1, 11)',
      '    fig.savefig("Fig_markers.pdf")',
      '',
    ].join('\n'),
    'utf-8',
  )
  writeFileSync(
    path.join(dir, 'tavotto_registry.json'),
    JSON.stringify({
      scripts: { 'fig_markers.py': { entry: 'main', cost: 'light', stems: ['Fig_markers'] } },
    }),
    'utf-8',
  )
  const fallback = process.platform === 'win32' ? 'python' : 'python3'
  const py = process.env.TAVOTTO_WORKER_PYTHON || fallback
  execFileSync(py, ['-c', 'import fig_markers; fig_markers.main()'], { cwd: dir, timeout: 120_000 })
  return dir
}

test('图内只有 marker 的曲线：也描每颗 marker 的轮廓，不描那条不存在的折线', async ({
  app,
  page,
}) => {
  const a = await app({ figures: markerOnlyLibrary() })
  const manifestOf = captureManifest(page)
  await page.goto(a.baseURL)
  await page.getByText('Fig_markers.pdf').dblclick({ timeout: 30_000 })
  const svgWrap = page.locator('[data-element-svg]').first()
  await expect(svgWrap.locator('svg')).toBeVisible({ timeout: 60_000 })
  await page.waitForTimeout(1500)

  /** 每颗 marker 的屏幕中心与半径（SVG 里是一个 `<defs>` 模板 + 每颗一个 `<use>`） */
  const probe = await page.evaluate(() => {
    const svg = document.querySelector('[data-element-svg] svg')
    if (!svg) return null
    const group = [...svg.querySelectorAll('[id^="axes_"]')].find((g) =>
      /\.lines_\d+$/.test(g.id),
    )
    if (!group) return null
    const markers = [...group.querySelectorAll('use')].map((u) => {
      const r = u.getBoundingClientRect()
      return { x: r.x + r.width / 2, y: r.y + r.height / 2, r: Math.max(r.width, r.height) / 2 }
    })
    return { id: group.id, markers }
  })
  expect(probe, '图里应当有一条只有 marker 的曲线').not.toBeNull()
  expect(probe!.markers.length).toBe(24)
  const ink = await page.evaluate(inkInBrowser)
  expect(ink.spines.length, '图里应当量得到坐标轴边框').toBeGreaterThan(0)
  expect(manifestOf(), '应当截到 render 响应里的 manifest').not.toBeNull()
  const boxes = hitBoxes(manifestOf()!, ink.layer)

  // 1) 点在某一颗 marker 的正中 → 一条 path、每颗一段**闭合**子路径、没有矩形框
  const m = probe!.markers[Math.floor(probe!.markers.length / 2)]
  await page.mouse.click(m.x, m.y)
  await page.waitForTimeout(300)
  const onMarker = await overlayOf(page)
  expect(onMarker.rects, '只有 marker 的曲线不该再有罩住整组的矩形选中框').toBe(0)
  expect(onMarker.paths, '选中后应当画出沿 marker 轮廓的 path').toBeGreaterThan(0)
  const k = onMarker.subpaths.indexOf(Math.max(...onMarker.subpaths))
  expect(onMarker.subpaths[k], '子路径数应当等于 marker 数（每颗一条）').toBe(
    probe!.markers.length,
  )
  expect(onMarker.closed[k], '每颗轮廓都该闭合——描的是 marker，不是穿过它们的折线').toBe(
    probe!.markers.length,
  )

  // 2) 点在整组 bbox 里、离每颗 marker 最远、又不在边框命中带里的空白处 → 不再选中这条曲线
  const gap = blankSpot(gridInside(bboxOf(probe!.markers)), { ...ink, boxes })
  expect(gap, '整组 bbox 里应当有既不在边框命中带里、也不压着别的元素的点').not.toBeNull()
  expect(gap!.dist, 'marker 之间应当有一块足够大的空白才算得上「空白」').toBeGreaterThan(12)
  await page.mouse.click(gap!.x, gap!.y)
  await page.waitForTimeout(300)
  const atGap = await overlayOf(page)
  expect(
    Math.max(0, ...atGap.subpaths),
    `点两颗 marker 之间的空白（离最近墨迹 ${Math.round(gap!.dist)}px、离边框 ${Math.round(gap!.spine)}px）不该还选中整条曲线`,
  ).toBeLessThan(probe!.markers.length)
})

/**
 * 误差棒（ErrorbarContainer 伪元素）：选中时描的是**误差线 + 帽 + 数据点**的真实轮廓，
 * 不是罩住整组的矩形（2026-09-26 用户反馈，ADR 0086）。两组误差棒交错摆放，并集
 * bbox 互相罩住——从前点这组的误差线，选中的常是 bbox 更小的另一组。
 *
 * 独立的一侧是 matplotlib 自己画的 SVG：误差线是 `LineCollection_*` 组里的 `<path>`，
 * 数据点与帽是 `line2d_*` 组里的 `<use>`（成员没有登记成独立元素，保留 matplotlib 的
 * 默认 id）。覆盖层描出来的那条 path 要贴着这些墨迹走，不能落进空白。
 */
function errorbarLibrary(): string {
  const dir = path.join(mkdtempSync(path.join(os.tmpdir(), 'tavotto-e2e-errorbar-')), 'figures')
  mkdirSync(dir)
  writeFileSync(
    path.join(dir, 'fig_errorbar.py'),
    [
      'import matplotlib',
      'matplotlib.use("Agg")',
      'import matplotlib.pyplot as plt',
      'import numpy as np',
      '',
      '',
      'def main():',
      '    fig, ax = plt.subplots(figsize=(4.0, 3.0))',
      '    x = np.arange(1.0, 9.0, 1.5)',
      '    ax.errorbar(x, 5 + np.sin(x), yerr=1.2, fmt="o", capsize=4, ms=5)',
      '    ax.errorbar(x + 0.6, 4 + np.cos(x), yerr=1.0, fmt="s", capsize=3, ms=4)',
      '    ax.set_xlim(0, 10)',
      '    ax.set_ylim(0, 8)',
      '    fig.savefig("Fig_errorbar.pdf")',
      '',
    ].join('\n'),
    'utf-8',
  )
  writeFileSync(
    path.join(dir, 'tavotto_registry.json'),
    JSON.stringify({
      scripts: { 'fig_errorbar.py': { entry: 'main', cost: 'light', stems: ['Fig_errorbar'] } },
    }),
    'utf-8',
  )
  const fallback = process.platform === 'win32' ? 'python' : 'python3'
  const py = process.env.TAVOTTO_WORKER_PYTHON || fallback
  execFileSync(py, ['-c', 'import fig_errorbar; fig_errorbar.main()'], { cwd: dir, timeout: 120_000 })
  return dir
}

test('图内误差棒：描误差线 + 帽 + 数据点的轮廓，贴着墨迹，点这组不会选中那组', async ({
  app,
  page,
}) => {
  const a = await app({ figures: errorbarLibrary() })
  const manifestOf = captureManifest(page)
  await page.goto(a.baseURL)
  await page.getByText('Fig_errorbar.pdf').dblclick({ timeout: 30_000 })
  const svgWrap = page.locator('[data-element-svg]').first()
  await expect(svgWrap.locator('svg')).toBeVisible({ timeout: 60_000 })
  await page.waitForTimeout(1500)

  /**
   * 两组误差棒的墨迹（屏幕坐标）：误差线沿线采样、marker / 帽取 `<use>` 的中心与半径。
   * 按 SVG 里的绘制顺序分组：每组一个 `LineCollection_*`，其后的 `line2d_*` 是帽与数据点。
   */
  const probe = await page.evaluate(() => {
    const svg = document.querySelector('[data-element-svg] svg')
    if (!svg) return null
    const toScreen = (el: SVGGraphicsElement, x: number, y: number) => {
      const m = el.getScreenCTM()!
      return { x: x * m.a + y * m.c + m.e, y: x * m.b + y * m.d + m.f }
    }
    const groups: { bars: { x: number; y: number }[][]; marks: { x: number; y: number; r: number }[] }[] = []
    for (const g of svg.querySelectorAll('g[id^="LineCollection_"], g[id^="line2d_"]')) {
      if (g.closest('[id^="matplotlib.axis"]')) continue
      if (g.id.startsWith('LineCollection_')) {
        const bars = [...g.querySelectorAll('path')].map((p) => {
          const len = (p as SVGPathElement).getTotalLength()
          return Array.from({ length: 21 }, (_, i) => {
            const q = (p as SVGPathElement).getPointAtLength((len * i) / 20)
            return toScreen(p as SVGGraphicsElement, q.x, q.y)
          })
        })
        groups.push({ bars, marks: [] })
      } else if (groups.length) {
        for (const u of g.querySelectorAll('use')) {
          const r = u.getBoundingClientRect()
          groups.at(-1)!.marks.push({
            x: r.x + r.width / 2,
            y: r.y + r.height / 2,
            r: Math.max(r.width, r.height) / 2,
          })
        }
      }
    }
    return groups
  })
  expect(probe, '图里应当有两组误差棒').not.toBeNull()
  expect(probe!.length).toBe(2)
  const man = manifestOf()
  expect(man, '应当截到 render 响应里的 manifest').not.toBeNull()
  const ebs = man!.elements.filter((e) => e.role === 'errorbar')
  expect(ebs.length).toBe(2)
  const expectPaths = (gid: string) =>
    (ebs.find((e) => e.gid === gid)!.geometry as { paths: unknown[] }).paths.length
  const inkOf = (g: NonNullable<typeof probe>[number]) => [
    ...g.bars.flat().map((q) => ({ ...q, r: 0 })),
    ...g.marks,
  ]

  /** 覆盖层里那条最长的 path 沿线采样（屏幕坐标） */
  const overlaySamples = () =>
    page.evaluate(() => {
      const svg = document.querySelector('[data-overlay-svg]')
      const paths = [...(svg?.querySelectorAll('path[d]') ?? [])].filter((p) =>
        (p.getAttribute('d') ?? '').startsWith('M'),
      ) as SVGPathElement[]
      if (!paths.length) return []
      const p = paths.sort(
        (a, b) => (b.getAttribute('d')!.match(/M/g)!.length - a.getAttribute('d')!.match(/M/g)!.length),
      )[0]
      const m = p.getScreenCTM()!
      const len = p.getTotalLength()
      return Array.from({ length: 400 }, (_, i) => {
        const q = p.getPointAtLength((len * i) / 399)
        return { x: q.x * m.a + q.y * m.c + m.e, y: q.x * m.b + q.y * m.d + m.f }
      })
    })

  for (const [k, gid] of ['axes_0.errorbar_0', 'axes_0.errorbar_1'].entries()) {
    const g = probe![k]
    // 点在第二根误差线 30% 处（离数据点与帽都有距离，是纯粹的误差线墨迹）
    const bar = g.bars[1]
    const at = bar[6]
    await page.mouse.click(at.x, at.y)
    await page.waitForTimeout(300)
    const ov = await overlayOf(page)
    expect(ov.rects, `${gid}：误差棒不该再有罩住整组的矩形选中框`).toBe(0)
    expect(
      Math.max(0, ...ov.subpaths),
      `${gid}：点它自己的误差线要选中它自己（子路径数 = 误差线 + 帽 + 数据点）`,
    ).toBe(expectPaths(gid))

    // 描出来的轮廓处处贴着这组的墨迹：每个采样点离最近墨迹不超过 3px
    const ink = inkOf(g)
    const samples = await overlaySamples()
    expect(samples.length).toBeGreaterThan(0)
    const worst = Math.max(
      ...samples.map((s) => Math.min(...ink.map((q) => Math.hypot(q.x - s.x, q.y - s.y) - q.r))),
    )
    expect(worst, `${gid}：选中轮廓离墨迹最远 ${worst.toFixed(1)}px`).toBeLessThan(3)
  }

  // 第一组并集 bbox 里、离两组全部墨迹都最远、又不在边框命中带里的空白：不再选中第一组
  const allInk = [...inkOf(probe![0]), ...inkOf(probe![1])]
  const frame = await page.evaluate(inkInBrowser)
  const blank = blankSpot(gridInside(bboxOf(inkOf(probe![0]))), {
    ink: allInk,
    spines: frame.spines,
    boxes: hitBoxes(man!, frame.layer),
  })
  expect(blank, '第一组的并集 bbox 里应当有一块空白').not.toBeNull()
  const gap = blank!.dist
  const mid = blank!
  expect(gap, '误差线之间应当有一块足够大的空白').toBeGreaterThan(12)
  await page.mouse.click(mid.x, mid.y)
  await page.waitForTimeout(300)
  const atGap = await overlayOf(page)
  expect(
    Math.max(0, ...atGap.subpaths),
    `点两根误差线之间的空白（离最近墨迹 ${Math.round(gap)}px）不该还选中误差棒`,
  ).toBeLessThan(expectPaths('axes_0.errorbar_0'))
})
