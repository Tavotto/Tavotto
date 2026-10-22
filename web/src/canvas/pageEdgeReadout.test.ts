import { describe, expect, it } from 'vitest'
import { DIM_MIN_LINE, DIM_OUTSIDE_GAP, DIM_TEXT_PAD, dimSpan } from './pageEdgeReadout'

const VIEW = 1000
const TEXT = 70

describe('dimSpan：标尺里尺寸线的几何', () => {
  it('页面完全在可视区内：线段 = 页面起止，两端都有短竖线，文字居中于中点', () => {
    const g = dimSpan('r', 200, 600, VIEW, TEXT)!
    expect(g).toEqual({ a: 200, b: 600, startCap: true, endCap: true, center: 400, outside: false })
  })

  it('页面边超出可视区：线段夹在可见范围，文字居中于可见段，被夹的那端不画短竖线', () => {
    const g = dimSpan('r', -300, 1500, VIEW, TEXT)!
    expect(g).toMatchObject({ a: 0, b: 1000, startCap: false, endCap: false, center: 500, outside: false })
    const h = dimSpan('l', 300, 1500, VIEW, TEXT)!
    expect(h).toMatchObject({ a: 300, b: 1000, startCap: true, endCap: false, center: 650 })
  })

  it('页面整个在可视区之外：没有尺寸线', () => {
    expect(dimSpan('r', 1100, 1400, VIEW, TEXT)).toBeNull()
    expect(dimSpan('l', -400, -10, VIEW, TEXT)).toBeNull()
  })

  it('线段太短放不下文字：文字放到线段外侧、靠近被拖边的一侧，不压线', () => {
    const need = TEXT + 2 * DIM_TEXT_PAD + 2 * DIM_MIN_LINE
    const start = 400
    const end = start + need - 1 // 刚好放不下
    const half = TEXT / 2 + DIM_TEXT_PAD
    const r = dimSpan('r', start, end, VIEW, TEXT)!
    expect(r.outside).toBe(true)
    expect(r.center).toBe(end + DIM_OUTSIDE_GAP + half) // 右边：文字在终点之后
    expect(r.center - half).toBeGreaterThan(end)
    const l = dimSpan('l', start, end, VIEW, TEXT)!
    expect(l.center).toBe(start - DIM_OUTSIDE_GAP - half) // 左边：文字在起点之前
    expect(l.center + half).toBeLessThan(start)
    expect(dimSpan('t', start, end, VIEW, TEXT)!.center).toBe(l.center) // 上 = 起点一侧
    expect(dimSpan('b', start, end, VIEW, TEXT)!.center).toBe(r.center) // 下 = 终点一侧
    // 刚好放得下：留在里面
    expect(dimSpan('r', start, start + need, VIEW, TEXT)!.outside).toBe(false)
  })

  it('外侧那一侧没地方就换另一侧', () => {
    const half = TEXT / 2 + DIM_TEXT_PAD
    // 页面贴着右端：右边拖，文字放不到终点之后 → 换到起点之前
    const g = dimSpan('r', 975, 1000, VIEW, TEXT)!
    expect(g.outside).toBe(true)
    expect(g.center).toBe(975 - DIM_OUTSIDE_GAP - half)
  })

  it('位置与文字内容无关：同一页面矩形同一结果（数字位数变不抖）', () => {
    expect(dimSpan('r', 200, 599.9, VIEW, TEXT)!.center).toBeCloseTo(dimSpan('r', 200, 600, VIEW, TEXT)!.center, 0)
  })
})
