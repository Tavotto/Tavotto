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
  planEdgeGrow,
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

  it('#1647 P2-1：内容边缘离图边 ≤ 半径时，不按 ⌥ 也不能裁进内容（Codex 给的那组数）', () => {
    const near = { depth0: 0, oppDepth: 0, inkDepth: 0.005, tol: 0.016, alt: false }
    // want 0.018 落在 ink+tol 之内：旧实现闸不启动、直接返回 0.018 / content
    expect(planEdgeDepth({ ...near, want: 0.018 })).toEqual({ depth: 0.005, state: 'tight' })
    // 半径外：blocked，停在内容边缘
    expect(planEdgeDepth({ ...near, want: 0.03 })).toEqual({ depth: 0.005, state: 'blocked' })
    expect(planEdgeDepth({ ...near, want: 0.5 })).toEqual({ depth: 0.005, state: 'blocked' })
  })

  it('#1647 P2-1：两个停靠点重叠的一段，离谁近吸谁，且深度随指针单调不减、从不超过内容边缘', () => {
    const near = { depth0: 0, oppDepth: 0, inkDepth: 0.005, tol: 0.016, alt: false }
    expect(planEdgeDepth({ ...near, want: 0.002 })).toEqual({ depth: 0, state: 'full' })
    expect(planEdgeDepth({ ...near, want: 0.0025 })).toEqual({ depth: 0, state: 'full' })
    expect(planEdgeDepth({ ...near, want: 0.003 })).toEqual({ depth: 0.005, state: 'tight' })
    let prev = -1
    for (let w = -0.01; w <= 0.1; w += 0.0005) {
      const r = planEdgeDepth({ ...near, want: w })
      expect(r.depth).toBeGreaterThanOrEqual(prev - 1e-12)
      expect(r.depth).toBeLessThanOrEqual(0.005 + 1e-12)
      prev = r.depth
    }
  })

  it('#1647 P2-1：⌥ 按住时可以越过（content），不变', () => {
    const near = { depth0: 0, oppDepth: 0, inkDepth: 0.005, tol: 0.016, alt: true }
    expect(planEdgeDepth({ ...near, want: 0.2 })).toEqual({ depth: 0.2, state: 'content' })
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

describe('planEdgeGrow：拖图边 = 改图幅，往外往里都是（ADR 0161 修订 2026-11-06 / 2026-11-09）', () => {
  const box0 = { x: 10, y: 20, w: 50, h: 75 } // 宽按 0.5 倍摆放，高按 1 倍：老项目里被拉伸过的面板
  const base = { box0, sizeMm: [100, 75] as const, tolMm: 2 }

  it('吸附半径内算停在图边：null', () => {
    expect(planEdgeGrow({ ...base, side: 'r', outMm: 2 })).toBeNull()
    expect(planEdgeGrow({ ...base, side: 'r', outMm: -2 })).toBeNull()
  })

  it('右 / 下：左上角不动，只有被拖的那一维变，页面长度按这一轴的比例换算', () => {
    expect(planEdgeGrow({ ...base, side: 'r', outMm: 10 })).toEqual({
      sizeMm: [120, 75],
      pos: { x: 10, y: 20 },
      box: { x: 10, y: 20, w: 60, h: 75 },
    })
    expect(planEdgeGrow({ ...base, side: 'b', outMm: 10 })).toEqual({
      sizeMm: [100, 85],
      pos: { x: 10, y: 20 },
      box: { x: 10, y: 20, w: 50, h: 85 },
    })
  })

  it('左 / 上：对边不动，左上角往外挪', () => {
    expect(planEdgeGrow({ ...base, side: 'l', outMm: 10 })).toMatchObject({
      sizeMm: [120, 75],
      pos: { x: 0, y: 20 },
      box: { x: 0, w: 60 },
    })
    expect(planEdgeGrow({ ...base, side: 't', outMm: 10 })).toMatchObject({
      sizeMm: [100, 85],
      pos: { x: 10, y: 10 },
      box: { y: 10, h: 85 },
    })
  })

  it('钳到引擎上限；已在上限 = 没有变化 = null', () => {
    expect(planEdgeGrow({ ...base, side: 'r', outMm: 100, maxMm: 110 })?.sizeMm).toEqual([110, 75])
    expect(planEdgeGrow({ ...base, side: 'r', outMm: 100, maxMm: 100 })).toBeNull()
  })

  it('往里拖：同一条规则缩短那一维，对边不动，文字 pt 不变（缩放比不变）', () => {
    expect(planEdgeGrow({ ...base, side: 'r', outMm: -10 })).toEqual({
      sizeMm: [80, 75],
      pos: { x: 10, y: 20 },
      box: { x: 10, y: 20, w: 40, h: 75 },
    })
    expect(planEdgeGrow({ ...base, side: 'l', outMm: -10 })).toMatchObject({
      sizeMm: [80, 75],
      pos: { x: 20, y: 20 },
      box: { x: 20, w: 40 },
    })
    expect(planEdgeGrow({ ...base, side: 't', outMm: -15 })).toMatchObject({
      sizeMm: [100, 60],
      pos: { x: 10, y: 35 },
      box: { y: 35, h: 60 },
    })
  })

  it('往里钳到引擎下限；已在下限 = null；下限缺席时至少 1 mm', () => {
    expect(planEdgeGrow({ ...base, side: 'r', outMm: -100, minMm: 30 })?.sizeMm).toEqual([30, 75])
    expect(planEdgeGrow({ ...base, sizeMm: [30, 75], side: 'r', outMm: -100, minMm: 30 })).toBeNull()
    expect(planEdgeGrow({ ...base, side: 'b', outMm: -500 })?.sizeMm).toEqual([100, 1])
  })
})
