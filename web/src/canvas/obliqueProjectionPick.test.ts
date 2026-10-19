/**
 * 真实用户图的端到端命中：手搓斜投影的「三维线图」（2D 轴上的 Polygon 幕墙 + 截面 + 网格段）。
 *
 * 夹具 `__fixtures__/obliqueProjection.manifest.json` 是**真实引擎输出**（新引擎，带
 * `paint_order` / `fill_alpha`）裁掉非几何字段后的 manifest。图的结构（见 ADR 0086 修订）：
 *   - 5 条彩色系列线（zorder 6，8 点折线）；
 *   - 每条系列线下面一片同色半透明「幕墙」Polygon（alpha .12，zorder 2）；
 *   - 8 片灰色截面 Polygon（alpha .07–.10，zorder 1）、一个 28 面 PolyCollection（alpha .14）；
 *   - 几十条 ax.plot 画的短虚线网格段。
 *
 * 旧评分下沿系列线取点点击，只有 1–5 次选中线本身，其余被幕墙 / 截面（按填充面积，细长薄片
 * 恒胜）与短网格段（墨迹面积更小）抢走。这里断言：沿 5 条线的 29 个点击点 ≥ 27 次选中自己，
 * 点在幕墙空白处仍选中幕墙。
 */
import { describe, expect, it } from 'vitest'

import type { Manifest, ManifestElement } from '@/lib/api'
import { geomContains, geomDistMm } from '@/lib/pathGeom'
import fixture from './__fixtures__/obliqueProjection.manifest.json'
import { PATH_HIT_MM, pickElement, pickElementStack } from './interactions'

const manifest = fixture as unknown as Manifest
const els = manifest.elements as ManifestElement[]

/** 5 条彩色系列线：zorder 6、描边 1.8pt（其余线是 0.55–1.1pt 的短网格段 / 参考线） */
const series = els.filter((e) => e.role === 'line' && e.geometry?.stroke_pt === 1.8)

function samplesAlong(e: ManifestElement): [number, number][] {
  const pts = e.geometry!.paths.flatMap((p) => p.points)
  const out: [number, number][] = []
  for (let i = 1; i < pts.length; i++) {
    for (const t of [0.25, 0.5, 0.75]) {
      out.push([
        pts[i - 1][0] + (pts[i][0] - pts[i - 1][0]) * t,
        pts[i - 1][1] + (pts[i][1] - pts[i - 1][1]) * t,
      ])
    }
  }
  return [...out, ...pts]
}

describe('斜投影三维线图：沿系列线点击', () => {
  it('夹具带着引擎的两项绘制事实', () => {
    expect(series).toHaveLength(5)
    for (const e of series) expect(e.geometry?.paint_order).toBeTypeOf('number')
    const curtain = els.filter((e) => e.role === 'patch' && (e.geometry?.fill_alpha ?? 0) === 0.12)
    expect(curtain).toHaveLength(5)
  })

  it.each([0, 1, 2, 3, 4])('第 %i 条系列线：沿线取点，至少 27/29（折点数不同则按比例）', (k) => {
    const e = series[k]
    const pts = samplesAlong(e)
    expect(pts.length).toBeGreaterThanOrEqual(24)
    const wins = pts.filter(([fx, fy]) => pickElement(manifest, fx, fy)?.gid === e.gid).length
    expect(wins / pts.length).toBeGreaterThanOrEqual(27 / 29 - 1e-9)
  })

  it('点在幕墙空白处（离所有描边超出命中容差）仍选中幕墙，不是子图', () => {
    const curtains = els.filter(
      (e) => e.role === 'patch' && (e.geometry?.fill_alpha ?? 0) === 0.12,
    )
    const strokes = els.filter((e) => e.geometry && !e.geometry.fill)
    const tested: string[] = []
    for (const c of curtains) {
      // 幕墙内部、离全部描边最远的点
      let best: [number, number, number] | null = null
      const g = c.geometry!
      const xs = g.paths.flatMap((p) => p.points.map((q) => q[0]))
      const ys = g.paths.flatMap((p) => p.points.map((q) => q[1]))
      const [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)]
      for (let i = 0; i <= 24; i++) {
        for (let j = 0; j <= 24; j++) {
          const fx = x0 + ((x1 - x0) * i) / 24
          const fy = y0 + ((y1 - y0) * j) / 24
          if (!geomContains(g, fx, fy)) continue
          const d = Math.min(...strokes.map((s) => geomDistMm(s.geometry!, manifest.size_mm, fx, fy)))
          if (!best || d > best[2]) best = [fx, fy, d]
        }
      }
      // 其余几片幕墙夹在网格段之间，内部每一点都在某条网格段的容差里——那些点选网格段是对的
      if (!best || best[2] <= PATH_HIT_MM) continue
      tested.push(c.gid)
      const hit = pickElementStack(manifest, best![0], best![1])
      // 命中的就是这片幕墙：旧评分里它会被更细长的截面 / 28 面集合抢走
      expect(hit[0].gid).toBe(c.gid)
    }
    expect(tested).toContain('axes_0.patches_8')
  })
})
