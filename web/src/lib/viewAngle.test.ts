/**
 * 视角 → 屏幕方向（三维子图的方向示意）。几个能手算的视角钉住约定：
 * 与 matplotlib `proj3d._view_axes` 同一套（右 = cross(竖直, 视线)，上 = cross(视线, 右)）。
 */
import { describe, expect, it } from 'vitest'
import { viewAxes2d } from './viewAngle'

const close = (a: [number, number], b: [number, number]) => {
  expect(a[0]).toBeCloseTo(b[0], 5)
  expect(a[1]).toBeCloseTo(b[1], 5)
}

describe('viewAxes2d', () => {
  it('从 +x 方向平视（elev 0, azim 0）：y 指右、z 指上、x 正对视线', () => {
    const v = viewAxes2d(0, 0)
    close(v.x, [0, 0])
    close(v.y, [1, 0])
    close(v.z, [0, 1])
  })

  it('从 -y 方向平视（azim -90）：x 指右、z 指上', () => {
    const v = viewAxes2d(0, -90)
    close(v.x, [1, 0])
    close(v.y, [0, 0])
    close(v.z, [0, 1])
  })

  it('matplotlib 默认视角（elev 30, azim -60）：x 向右下、y 向右上、z 向上', () => {
    const v = viewAxes2d(30, -60)
    expect(v.x[0]).toBeGreaterThan(0)
    expect(v.x[1]).toBeLessThan(0)
    expect(v.y[0]).toBeGreaterThan(0)
    expect(v.y[1]).toBeGreaterThan(0)
    close(v.z, [0, Math.cos(30 * (Math.PI / 180))])
  })

  it('roll 绕视线转：roll 90° 把「上」转到「右」', () => {
    const v = viewAxes2d(0, 0, 90)
    // 未转时 z 指上；转 90° 后 z 指向屏幕的一侧、y 转到竖直方向
    expect(Math.abs(v.z[0])).toBeCloseTo(1, 5)
    expect(v.z[1]).toBeCloseTo(0, 5)
    expect(Math.abs(v.y[1])).toBeCloseTo(1, 5)
  })

  it('正俯视（elev 90）不退化成 NaN：z 几乎正对视线，x / y 铺在屏幕上', () => {
    const v = viewAxes2d(90, -60)
    for (const k of ['x', 'y', 'z'] as const) {
      expect(Number.isFinite(v[k][0])).toBe(true)
      expect(Number.isFinite(v[k][1])).toBe(true)
    }
    expect(Math.hypot(...v.z)).toBeLessThan(0.01)
    expect(Math.hypot(...v.x)).toBeGreaterThan(0.99)
  })

  it('越过天顶（elev 120）后「上」翻过来，图不会左右镜像', () => {
    const above = viewAxes2d(60, -60)
    const over = viewAxes2d(120, -60)
    // 视线绕过天顶后 x 的左右分量反号（同一根轴从另一侧看）
    expect(Math.sign(over.x[0])).toBe(-Math.sign(above.x[0]))
  })
})
