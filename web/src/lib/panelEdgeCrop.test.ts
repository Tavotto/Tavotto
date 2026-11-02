/**
 * 排版里「边 = 裁剪」的停靠点（ADR 0161 §4）：图边 → 内容边缘 → ⌥ 放行 → 越过内容边缘。
 * 深度 = 这条边从图边往里走的分数；`ink_bbox` 与 `panel.crop` 同一套（相对图幅的分数、y 向下）。
 */
import { describe, expect, it } from 'vitest'
import {
  depthOf,
  fitContentCrop,
  inkDepths,
  isContentFit,
  planEdgeDepth,
  withEdgeDepth,
} from './panelEdgeCrop'

const base = { depth0: 0, oppDepth: 0, inkDepth: 0.1, tol: 0.01, alt: false }

describe('inkDepths / fitContentCrop', () => {
  it('ink_bbox [x0,y0,x1,y1] → 四边深度；超出图幅的夹回 [0,1]', () => {
    expect(inkDepths([0.1, 0.2, 0.9, 0.7])).toEqual({ l: 0.1, t: 0.2, r: expect.closeTo(0.1, 12), b: expect.closeTo(0.3, 12) })
    expect(inkDepths([-0.05, 0, 1.02, 1])).toEqual({ l: 0, t: 0, r: 0, b: 0 })
  })

  it('缺席 / 不成形 = 没有内容边缘', () => {
    expect(inkDepths(undefined)).toBeNull()
    expect(inkDepths([0.5, 0.5, 0.5, 0.9])).toBeNull()
    expect(fitContentCrop(undefined)).toBeNull()
  })

  it('贴合内容：一次把四边裁到内容边缘；已贴合则 isContentFit', () => {
    const ink: [number, number, number, number] = [0.1, 0.2, 0.9, 0.7]
    const c = fitContentCrop(ink)!
    expect(c.x).toBeCloseTo(0.1, 12)
    expect(c.y).toBeCloseTo(0.2, 12)
    expect(c.w).toBeCloseTo(0.8, 12)
    expect(c.h).toBeCloseTo(0.5, 12)
    expect(isContentFit(undefined, ink)).toBe(false)
    expect(isContentFit(c, ink)).toBe(true)
    expect(isContentFit(c, undefined)).toBe(false)
  })
})

describe('planEdgeDepth', () => {
  it('往外到未裁剪为止：越过图边仍是 0（full）', () => {
    expect(planEdgeDepth({ ...base, depth0: 0.05, want: -0.3 })).toEqual({ depth: 0, state: 'full' })
  })

  it('靠近图边半径内吸到 0', () => {
    expect(planEdgeDepth({ ...base, depth0: 0.05, want: 0.008 })).toEqual({ depth: 0, state: 'full' })
  })

  it('图边与内容边缘之间 = 裁掉留白（trim），不吸附', () => {
    expect(planEdgeDepth({ ...base, want: 0.05 })).toEqual({ depth: 0.05, state: 'trim' })
  })

  it('靠近内容边缘半径内吸上去（tight）', () => {
    expect(planEdgeDepth({ ...base, want: 0.095 })).toEqual({ depth: 0.1, state: 'tight' })
    expect(planEdgeDepth({ ...base, want: 0.108 })).toEqual({ depth: 0.1, state: 'tight' })
  })

  it('不按 ⌥ 越不过内容边缘：停在边缘上（blocked）', () => {
    expect(planEdgeDepth({ ...base, want: 0.2 })).toEqual({ depth: 0.1, state: 'blocked' })
    expect(planEdgeDepth({ ...base, want: 0.5 })).toEqual({ depth: 0.1, state: 'blocked' })
  })

  it('按住 ⌥ 放行：越过内容边缘裁掉内容（content），位置跟手', () => {
    expect(planEdgeDepth({ ...base, want: 0.2, alt: true })).toEqual({ depth: 0.2, state: 'content' })
  })

  it('⌥ 放行时内容边缘的吸附仍在', () => {
    expect(planEdgeDepth({ ...base, want: 0.104, alt: true })).toEqual({ depth: 0.1, state: 'tight' })
  })

  it('起手就已经裁进内容的边不设闸：不会一动就弹回内容边缘', () => {
    expect(planEdgeDepth({ ...base, depth0: 0.2, want: 0.25 })).toEqual({ depth: 0.25, state: 'content' })
  })

  it('没有内容边缘信息：照样能裁，没有紧凑停靠点也没有闸', () => {
    expect(planEdgeDepth({ ...base, inkDepth: null, want: 0.4 })).toEqual({ depth: 0.4, state: 'trim' })
    expect(planEdgeDepth({ ...base, inkDepth: null, want: 0.005 })).toEqual({ depth: 0, state: 'full' })
  })

  it('内容紧贴图边（ink≈0）：往里不按 ⌥ 就停在图边上', () => {
    expect(planEdgeDepth({ ...base, inkDepth: 0, want: 0.3 })).toEqual({ depth: 0, state: 'blocked' })
    expect(planEdgeDepth({ ...base, inkDepth: 0, want: 0.3, alt: true })).toEqual({ depth: 0.3, state: 'content' })
  })

  it('给对边留最小边长（5%）', () => {
    const r = planEdgeDepth({ ...base, inkDepth: null, oppDepth: 0.5, want: 0.9 })
    expect(r.depth).toBeCloseTo(0.45, 12)
  })
})

describe('withEdgeDepth / depthOf', () => {
  const c = { x: 0.1, y: 0.2, w: 0.7, h: 0.6 }
  it('四边各自只动自己这一边', () => {
    const l = withEdgeDepth(c, 'l', 0.3)
    expect(l).toEqual({ x: 0.3, y: 0.2, w: expect.closeTo(0.5, 12), h: 0.6 })
    const r = withEdgeDepth(c, 'r', 0.3)
    expect(r.x).toBe(0.1)
    expect(r.w).toBeCloseTo(0.6, 12)
    const t = withEdgeDepth(c, 't', 0.4)
    expect(t.y).toBe(0.4)
    expect(t.h).toBeCloseTo(0.4, 12)
    const b = withEdgeDepth(c, 'b', 0.1)
    expect(b.y).toBe(0.2)
    expect(b.h).toBeCloseTo(0.7, 12)
  })
  it('depthOf 与 withEdgeDepth 互逆', () => {
    for (const side of ['l', 'r', 't', 'b'] as const) {
      expect(depthOf(withEdgeDepth(c, side, 0.33), side)).toBeCloseTo(0.33, 12)
    }
  })
})
