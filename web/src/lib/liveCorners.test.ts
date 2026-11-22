/**
 * 实时圆角（ADR 0185 M3 第二批）：哪些角能圆、圆出来的弧真的是圆（与两条边相切、到圆心距离 = r）、半径夹到相邻边的一半、
 * 矩形的圆点与拖动换算。
 */
import { describe, expect, it } from 'vitest'
import { cornerWidget, pathCorners, radiusFromPointer, rectRadiusFromPointer, rectWidgets, roundCorners } from './liveCorners'
import type { MmSubpath, XY } from './pathEdit'

const poly = (pts: XY[], closed = true): MmSubpath => ({
  closed,
  nodes: pts.map((p) => ({ p, in: p, out: p, smooth: false })),
})
const square = poly([
  [0, 0],
  [20, 0],
  [20, 20],
  [0, 20],
])
const bez = (p0: XY, c1: XY, c2: XY, p3: XY, t: number): XY => {
  const u = 1 - t
  return [0, 1].map((k) => u * u * u * p0[k] + 3 * u * u * t * c1[k] + 3 * u * t * t * c2[k] + t * t * t * p3[k]) as XY
}

describe('能圆的角', () => {
  it('闭合方形 4 个；开放折线不算两端；带手柄的锚点、相邻段是曲线的不算；接近一直线的不算', () => {
    expect(pathCorners([square]).map((c) => c.ref.i)).toEqual([0, 1, 2, 3])
    expect(pathCorners([poly([[0, 0], [10, 0], [10, 10]], false)]).map((c) => c.ref.i)).toEqual([1])
    const curved: MmSubpath = { ...square, nodes: square.nodes.map((n, i) => (i === 1 ? { ...n, out: [22, 5] as XY } : n)) }
    expect(pathCorners([curved]).map((c) => c.ref.i)).toEqual([0, 3])
    expect(pathCorners([poly([[0, 0], [10, 0.1], [20, 0]], false)])).toEqual([])
  })
})

describe('圆出来的弧', () => {
  it('一个角换两个切点，弧的中点到圆心距离 = r，切点在两条边上', () => {
    const r = 5
    const [out] = roundCorners([square], [{ sp: 0, i: 1 }], r)
    expect(out.nodes).toHaveLength(5)
    const [p1, p2] = [out.nodes[1], out.nodes[2]]
    p1.p.forEach((v, k) => expect(v).toBeCloseTo([15, 0][k], 12))
    p2.p.forEach((v, k) => expect(v).toBeCloseTo([20, 5][k], 12))
    const mid = bez(p1.p, p1.out, p2.in, p2.p, 0.5)
    expect(Math.hypot(mid[0] - 15, mid[1] - 5)).toBeCloseTo(r, 3)
    // 两端的切线沿着边：起点手柄水平、终点手柄竖直
    expect(p1.out[1]).toBeCloseTo(0, 12)
    expect(p2.in[0]).toBeCloseTo(20, 12)
  })

  it('半径夹到相邻边长的一半（两头都圆也不打架）；全部角一起圆', () => {
    const [all] = roundCorners([square], pathCorners([square]).map((c) => c.ref), 99)
    expect(all.nodes).toHaveLength(8)
    all.nodes[0].p.forEach((v, k) => expect(v).toBeCloseTo([0, 10][k], 12))
    all.nodes[1].p.forEach((v, k) => expect(v).toBeCloseTo([10, 0][k], 12))
  })

  it('r = 0 / 不能圆的角：原样', () => {
    expect(roundCorners([square], [{ sp: 0, i: 1 }], 0)[0].nodes).toHaveLength(4)
  })

  it('锐角：切点距离 = r / tan(θ/2)', () => {
    const tri = poly([[0, 0], [20, 0], [0, 10]])
    const c = pathCorners([tri]).find((k) => k.ref.i === 1)!
    const [out] = roundCorners([tri], [c.ref], 1)
    const d = 1 / Math.tan(c.theta / 2)
    expect(Math.hypot(out.nodes[1].p[0] - 20, out.nodes[1].p[1])).toBeCloseTo(d, 9)
  })
})

describe('圆点与拖动', () => {
  it('圆点在圆心上（离顶点 r / sin(θ/2)），至少离 minMm；拖到哪里半径就是那里的圆心距 × sin(θ/2)', () => {
    const c = pathCorners([square])[1]
    const w = cornerWidget(c, 5, 1)
    expect(w[0]).toBeCloseTo(15, 9)
    expect(w[1]).toBeCloseTo(5, 9)
    expect(radiusFromPointer(c, [15, 5])).toBeCloseTo(5, 9)
    expect(radiusFromPointer(c, [40, -20])).toBe(0) // 角外面
    expect(radiusFromPointer(c, [0, 20])).toBeCloseTo(c.maxR, 9)
  })

  it('矩形：四个圆点在 (r, r) 处；沿对角线拖改半径，夹到短边一半', () => {
    const o = { x: 10, y: 10, w: 40, h: 20, cornerRadius: 3 }
    expect(rectWidgets(o, 1).map((w) => w.at)).toEqual([
      [13, 13],
      [47, 13],
      [47, 27],
      [13, 27],
    ])
    expect(rectRadiusFromPointer(o, 'br', [44, 24])).toBe(6)
    expect(rectRadiusFromPointer(o, 'tl', [40, 40])).toBe(10)
    expect(rectRadiusFromPointer(o, 'tl', [0, 0])).toBe(0)
  })
})
