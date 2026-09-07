import { execFileSync } from 'node:child_process'
import { mkdirSync, mkdtempSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
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

  await page.goto(a.baseURL)
  // Prompt 09 起，双击素材卡 = 打开这张图（快速编辑工作区），**当场就在图内
  // 编辑态**——不再需要先「加入画布」再点一次「编辑图内元素」。
  await page.getByText('Fig1_kinetics.pdf').dblclick({ timeout: 30_000 })
  const svgWrap = page.locator('[data-element-svg]').first()
  await expect(svgWrap.locator('svg')).toBeVisible({ timeout: 60_000 })
  await page.waitForTimeout(1500)
  expect(sawGeometry, 'manifest 里应当带上 geometry').toBe(true)

  /** 曲线上的一个精确点 + 它 bbox 的四个角（屏幕坐标） */
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
      const at = (frac: number) => {
        const p = path.getPointAtLength(len * frac)
        return { x: p.x * ctm.a + p.y * ctm.c + ctm.e, y: p.x * ctm.b + p.y * ctm.d + ctm.f }
      }
      const r = path.getBoundingClientRect()
      if (r.width < 30 || r.height < 30) continue   // 扁平线没有「空白角」可言
      return { id: g.id, mid: at(0.5), rect: { x: r.x, y: r.y, w: r.width, h: r.height } }
    }
    return null
  })
  expect(probe, '图里应当有一条有起伏的曲线').not.toBeNull()

  /** 覆盖层里当前有几条沿路径的描示 / 几个带底色的矩形选中框 */
  const overlay = () =>
    page.evaluate(() => {
      const svg = document.querySelector('[data-overlay-svg]') as SVGSVGElement | null
      const paths = [...(svg?.querySelectorAll('path[d]') ?? [])].filter(
        (p) => (p.getAttribute('d') ?? '').startsWith('M'),
      ).length
      const rects = svg?.querySelectorAll('rect[fill-opacity]').length ?? 0
      return { paths, rects }
    })

  // 1) 点在曲线**本身**上 → 沿路径描示，没有带底色的矩形选中框
  await page.mouse.click(probe!.mid.x, probe!.mid.y)
  await page.waitForTimeout(300)
  const onCurve = await overlay()
  expect(onCurve.paths, '选中曲线应当画一条沿真实路径的 path').toBeGreaterThan(0)
  expect(onCurve.rects, '曲线不该再有带底色的矩形选中框').toBe(0)

  // 2) 点在曲线 bbox 的角上（离曲线很远的空白）→ 选中的不再是曲线
  //    四个角里挑一个离曲线最远的：曲线不一定从哪个角附近经过
  const corner = await page.evaluate((p) => {
    const svg = document.querySelector('[data-element-svg] svg')!
    const path = svg.querySelector(`[id="${p.id}"] path`) as SVGPathElement
    const ctm = path.getScreenCTM()!
    const len = path.getTotalLength()
    const pts: { x: number; y: number }[] = []
    for (let i = 0; i <= 200; i++) {
      const q = path.getPointAtLength((len * i) / 200)
      pts.push({ x: q.x * ctm.a + q.y * ctm.c + ctm.e, y: q.x * ctm.b + q.y * ctm.d + ctm.f })
    }
    const inset = 6
    const corners = [
      { x: p.rect.x + inset, y: p.rect.y + inset },
      { x: p.rect.x + p.rect.w - inset, y: p.rect.y + inset },
      { x: p.rect.x + inset, y: p.rect.y + p.rect.h - inset },
      { x: p.rect.x + p.rect.w - inset, y: p.rect.y + p.rect.h - inset },
    ]
    let best = corners[0]
    let bestD = -1
    for (const c of corners) {
      const d = Math.min(...pts.map((q) => Math.hypot(q.x - c.x, q.y - c.y)))
      if (d > bestD) {
        bestD = d
        best = c
      }
    }
    return { ...best, dist: bestD }
  }, probe!)
  expect(corner.dist, 'bbox 角到曲线应当有足够距离才算「空白」').toBeGreaterThan(20)

  await page.mouse.click(corner.x, corner.y)
  await page.waitForTimeout(300)
  const atCorner = await overlay()
  expect(
    atCorner.paths,
    `点 bbox 空白角（离曲线 ${Math.round(corner.dist)}px）不该还选中曲线`,
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
 */
test('图内散点：描每颗 marker 的轮廓，两颗之间的空白不误命中', async ({ app, page }) => {
  const a = await app()
  await page.goto(a.baseURL)
  await page.getByText('Fig2_correlation.pdf').dblclick({ timeout: 30_000 })
  const svgWrap = page.locator('[data-element-svg]').first()
  await expect(svgWrap.locator('svg')).toBeVisible({ timeout: 60_000 })
  await page.waitForTimeout(1500)

  /** 每颗 marker 的屏幕中心 + 拟合线上的采样点 */
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
    const avoid: { x: number; y: number }[] = []
    for (const g of svg.querySelectorAll('[id^="axes_"]')) {
      if (!/\.lines_\d+$/.test(g.id)) continue
      const path = g.querySelector('path') as SVGPathElement | null
      if (!path?.getTotalLength) continue
      const ctm = path.getScreenCTM()
      if (!ctm) continue
      const len = path.getTotalLength()
      for (let i = 0; i <= 200; i++) {
        const q = path.getPointAtLength((len * i) / 200)
        avoid.push({ x: q.x * ctm.a + q.y * ctm.c + ctm.e, y: q.x * ctm.b + q.y * ctm.d + ctm.f })
      }
    }
    return { id: group.id, markers, avoid }
  })
  expect(probe, '图里应当有一组散点').not.toBeNull()
  expect(probe!.markers.length).toBeGreaterThan(10)

  const overlay = () =>
    page.evaluate(() => {
      const svg = document.querySelector('[data-overlay-svg]') as SVGSVGElement | null
      const ds = [...(svg?.querySelectorAll('path[d]') ?? [])]
        .map((p) => p.getAttribute('d') ?? '')
        .filter((d) => d.startsWith('M'))
      const rects = svg?.querySelectorAll('rect[fill-opacity]').length ?? 0
      return { paths: ds.length, subpaths: ds.map((d) => d.match(/M/g)?.length ?? 0), rects }
    })

  // 1) 点在某一颗 marker 的正中 → 一条 path 里有「每颗一段」的闭合子路径，没有矩形框
  const m = probe!.markers[Math.floor(probe!.markers.length / 2)]
  await page.mouse.click(m.x, m.y)
  await page.waitForTimeout(300)
  const onMarker = await overlay()
  expect(onMarker.rects, '散点不该再有罩住整组的矩形选中框').toBe(0)
  expect(onMarker.paths, '选中散点应当画出沿 marker 轮廓的 path').toBeGreaterThan(0)
  expect(
    Math.max(...onMarker.subpaths),
    '子路径数应当等于 marker 数（每颗一条），且全部收在一个 path 节点里',
  ).toBe(probe!.markers.length)

  // 2) 点在整组 bbox 里、离每颗 marker 与拟合线都最远的空白处 → 不再选中散点
  const gap = (() => {
    const xs = probe!.markers.map((p) => p.x)
    const ys = probe!.markers.map((p) => p.y)
    const [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)]
    let best = { x: x0, y: y0, dist: -1 }
    for (let i = 1; i < 40; i++) {
      for (let j = 1; j < 40; j++) {
        const c = { x: x0 + ((x1 - x0) * i) / 40, y: y0 + ((y1 - y0) * j) / 40 }
        const d = Math.min(
          ...probe!.markers.map((q) => Math.hypot(q.x - c.x, q.y - c.y) - q.r),
          ...probe!.avoid.map((q) => Math.hypot(q.x - c.x, q.y - c.y)),
        )
        if (d > best.dist) best = { ...c, dist: d }
      }
    }
    return best
  })()
  expect(gap.dist, '散点之间应当有一块足够大的空白才算得上「空白」').toBeGreaterThan(12)
  await page.mouse.click(gap.x, gap.y)
  await page.waitForTimeout(300)
  const atGap = await overlay()
  expect(
    Math.max(0, ...atGap.subpaths),
    `点两颗 marker 之间的空白（离最近墨迹 ${Math.round(gap.dist)}px）不该还选中整组散点`,
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

  const overlay = () =>
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

  // 1) 点在某一颗 marker 的正中 → 一条 path、每颗一段**闭合**子路径、没有矩形框
  const m = probe!.markers[Math.floor(probe!.markers.length / 2)]
  await page.mouse.click(m.x, m.y)
  await page.waitForTimeout(300)
  const onMarker = await overlay()
  expect(onMarker.rects, '只有 marker 的曲线不该再有罩住整组的矩形选中框').toBe(0)
  expect(onMarker.paths, '选中后应当画出沿 marker 轮廓的 path').toBeGreaterThan(0)
  const k = onMarker.subpaths.indexOf(Math.max(...onMarker.subpaths))
  expect(onMarker.subpaths[k], '子路径数应当等于 marker 数（每颗一条）').toBe(
    probe!.markers.length,
  )
  expect(onMarker.closed[k], '每颗轮廓都该闭合——描的是 marker，不是穿过它们的折线').toBe(
    probe!.markers.length,
  )

  // 2) 点在整组 bbox 里、离每颗 marker 最远的空白处 → 不再选中这条曲线
  const gap = (() => {
    const xs = probe!.markers.map((p) => p.x)
    const ys = probe!.markers.map((p) => p.y)
    const [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)]
    let best = { x: x0, y: y0, dist: -1 }
    for (let i = 1; i < 40; i++) {
      for (let j = 1; j < 40; j++) {
        const c = { x: x0 + ((x1 - x0) * i) / 40, y: y0 + ((y1 - y0) * j) / 40 }
        const d = Math.min(...probe!.markers.map((q) => Math.hypot(q.x - c.x, q.y - c.y) - q.r))
        if (d > best.dist) best = { ...c, dist: d }
      }
    }
    return best
  })()
  expect(gap.dist, 'marker 之间应当有一块足够大的空白才算得上「空白」').toBeGreaterThan(12)
  await page.mouse.click(gap.x, gap.y)
  await page.waitForTimeout(300)
  const atGap = await overlay()
  expect(
    Math.max(0, ...atGap.subpaths),
    `点两颗 marker 之间的空白（离最近墨迹 ${Math.round(gap.dist)}px）不该还选中整条曲线`,
  ).toBeLessThan(probe!.markers.length)
})
