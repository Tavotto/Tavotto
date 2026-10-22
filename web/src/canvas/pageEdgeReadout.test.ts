import { describe, expect, it } from 'vitest'
import { DIM_INSET, dimSpan, MIN_DIM_LEN } from './pageEdgeReadout'

describe('dimSpan：尺寸线的可见段', () => {
  it('页面在可视区内：线段 = 页面起止，两端都有箭头，数字居中于中点', () => {
    expect(dimSpan(200, 600, 1000)).toEqual({ a: 200, b: 600, startArrow: true, endArrow: true, center: 400 })
  })
  it('页面边超出可视区：线段夹在可见范围，数字居中于可见段，被夹那端不画箭头', () => {
    expect(dimSpan(-300, 1500, 1000)).toEqual({ a: 0, b: 1000, startArrow: false, endArrow: false, center: 500 })
    expect(dimSpan(300, 1500, 1000)).toEqual({ a: 300, b: 1000, startArrow: true, endArrow: false, center: 650 })
    expect(dimSpan(-200, 400, 1000)).toEqual({ a: 0, b: 400, startArrow: false, endArrow: true, center: 200 })
  })
  it('整段在可视区之外：没有尺寸线', () => {
    expect(dimSpan(1100, 1400, 1000)).toBeNull()
    expect(dimSpan(-400, -10, 1000)).toBeNull()
  })
  it('只看页面矩形：页面边 0.1 px 的变化只让中点动 0.05 px（数字位数变不抖）', () => {
    expect(Math.abs(dimSpan(200, 599.9, 1000)!.center - dimSpan(200, 600, 1000)!.center)).toBeLessThan(0.06)
  })
  it('可见段 < 8 px（如 [0,5]）：内缩后端点会交叉，不画（null）', () => {
    expect(dimSpan(-100, 5, 1000)).toBeNull()
    expect(dimSpan(995, 1200, 1000)).toBeNull()
    expect(dimSpan(100, 107.9, 1000)).toBeNull()
  })
  it('可见段恰为 8 px 边界：仍画，内缩后退化为零长度而不反向', () => {
    const s = dimSpan(100, 100 + MIN_DIM_LEN, 1000)!
    expect(s).not.toBeNull()
    expect(s.b - DIM_INSET).toBeGreaterThanOrEqual(s.a + DIM_INSET)
  })
  it('只要返回非 null，内缩后的端点就不交叉且在可见段内', () => {
    for (const [st, en] of [[0, 9], [-50, 20], [990, 2000], [200, 600]] as const) {
      const s = dimSpan(st, en, 1000)!
      expect(s.a + DIM_INSET).toBeLessThanOrEqual(s.b - DIM_INSET)
      expect(s.a + DIM_INSET).toBeGreaterThanOrEqual(s.a)
      expect(s.b - DIM_INSET).toBeLessThanOrEqual(s.b)
    }
  })
})
