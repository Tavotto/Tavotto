/**
 * 画布渐变柄的几何（ADR 0186 第二期）：比例坐标 ↔ 页面（旋转算进去）、柄的位置、拖端点（吸附 / ⇧ 45° / 不许重合）、
 * 拖径向半径、拖色标（投影到柄上、夹在邻居之间）。
 */
import { describe, expect, it } from 'vitest'
import type { Gradient } from '@/types/document'
import { dragEndpoint, dragStop, handleGeometry, pageToRatio, ratioToPage } from './gradientHandles'

const box = { x: 10, y: 20, w: 40, h: 20 }
const LIN: Extract<Gradient, { kind: 'linear' }> = {
  kind: 'linear',
  stops: [
    { offset: 0, color: '#ff0000' },
    { offset: 0.25, color: '#00ff00' },
    { offset: 1, color: '#0000ff' },
  ],
  from: [0, 0.5],
  to: [1, 0.5],
}
const RAD: Gradient = { kind: 'radial', stops: LIN.stops, center: [0.5, 0.5], radius: 0.25 }
const near = (a: number[], b: number[]) => a.forEach((v, i) => expect(v).toBeCloseTo(b[i], 9))

describe('坐标', () => {
  it('比例 ↔ 页面互逆，旋转对象也一样', () => {
    for (const rotationDeg of [0, 30, -90]) {
      const o = { ...box, rotationDeg }
      near(pageToRatio(o, ratioToPage(o, [0.2, 0.7])), [0.2, 0.7])
    }
    near(ratioToPage({ ...box, rotationDeg: 90 }, [1, 0.5]), [30, 50]) // 右边中点转 90° 到正下方
  })
})

describe('柄的位置', () => {
  it('线性：两端 + 色标按位置插在中间', () => {
    const g = handleGeometry(box, LIN)
    near(g.a, [10, 30])
    near(g.b, [50, 30])
    near(g.stops[1].at, [20, 30])
  })
  it('径向：中心 + 半径柄在对象水平方向、半径 = 比例 × 长边', () => {
    const g = handleGeometry(box, RAD)
    near(g.a, [30, 30])
    near(g.b, [40, 30])
    expect(g.radiusMm).toBe(10)
    near(handleGeometry({ ...box, rotationDeg: 90 }, RAD).b, [30, 40])
  })
})

describe('拖端点', () => {
  it('吸到框的 0 / ½ / 1（容差按 mm）；容差外不吸', () => {
    const r = dragEndpoint(box, LIN, 'b', [49.6, 20.3], { tolMm: 0.5 })
    expect(r.snapped).toBe(true)
    expect((r.g as typeof LIN).to).toEqual([1, 0])
    const far = dragEndpoint(box, LIN, 'b', [45, 25], { tolMm: 0.5 })
    expect((far.g as typeof LIN).to).toEqual([0.875, 0.25])
    expect(far.snapped).toBe(false)
  })
  it('⇧：以另一端为原点锁 45°（页面方向）', () => {
    const r = dragEndpoint(box, LIN, 'b', [31, 9.5], { shift: true })
    const geo = handleGeometry(box, r.g)
    const ang = (Math.atan2(geo.b[1] - geo.a[1], geo.b[0] - geo.a[0]) * 180) / Math.PI
    expect(ang).toBeCloseTo(-45, 9)
  })
  it('起点终点不许拖到一起（那样的渐变读不成）', () => {
    expect(dragEndpoint(box, LIN, 'b', [10, 30]).g).toBe(LIN)
  })
  it('径向：拖中心改 center，拖半径柄改 radius（距离 / 长边）', () => {
    expect(dragEndpoint(box, RAD, 'a', [20, 25]).g).toMatchObject({ center: [0.25, 0.25] })
    expect(dragEndpoint(box, RAD, 'b', [30, 46]).g).toMatchObject({ radius: 0.4 })
  })
})

describe('拖色标', () => {
  it('投影到柄上得到位置、夹在左右邻居之间', () => {
    expect((dragStop(box, LIN, 1, [30, 99]).stops[1] as { offset: number }).offset).toBe(0.5)
    expect(dragStop(box, LIN, 1, [5, 30]).stops[1].offset).toBe(0)
    expect(dragStop(box, LIN, 1, [80, 30]).stops[1].offset).toBe(1)
  })
})
