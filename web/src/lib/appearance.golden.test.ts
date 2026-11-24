/**
 * 填充与描边补齐（ADR 0185 第二批）的**前端那一侧**：自定义虚线的判据、斜接上限的读法、形状描边内缩量（描边对齐）、
 * 一条描边实际生效的线型 / 线端 / 斜接上限。共享向量 `tests/golden/appearance_vectors.json`，后端 `tests/test_appearance.py`
 * 跑同一份；两侧独立实现，改判据两侧一起改、`scripts/dev/gen_appearance_vectors.py` 重录。
 */
import { describe, expect, it } from 'vitest'
import vectors from '../../../tests/golden/appearance_vectors.json'
import type { ShapeObject } from '@/types/document'
import { miterLimitOf, shapeInset, svgStroke, validDashPattern } from './shapeGeometry'

describe('validDashPattern', () => {
  it.each(vectors.dash)('$in', (v) => {
    expect(validDashPattern(v.in)).toEqual(v.out)
  })
})

describe('miterLimitOf', () => {
  it.each(vectors.miter)('$in', (v) => {
    expect(miterLimitOf(v.in as number | undefined)).toBe(v.out)
  })
})

describe('shapeInset（描边对齐）', () => {
  it.each(vectors.inset)('$in', (v) => {
    const i = v.in as { shape: string; stroke?: boolean; stroke_align?: string }
    const o = { shape: i.shape, stroke: i.stroke, strokeAlign: i.stroke_align } as unknown as ShapeObject
    expect(shapeInset(o, v.sw)).toBe(v.out)
  })
})

describe('svgStroke：生效的线型（pt）/ 线端 / 斜接上限', () => {
  it.each(vectors.stroke)('$in', (v) => {
    const i = v.in as Record<string, unknown>
    const s = svgStroke(
      {
        color: '#000000',
        dash: i.dash as never,
        dashPattern: i.dash_pattern as number[] | undefined,
        lineCap: i.line_cap as never,
        lineJoin: i.line_join as never,
        miterLimit: i.miter_limit as number | undefined,
        strokePt: i.stroke_pt as number,
      },
      i.stroke_pt as number, // 线宽按 pt 传：这一处 1 pt = 1
      'round',
    )
    const got = s.strokeDasharray ? s.strokeDasharray.split(' ').map(Number) : []
    expect(got.length).toBe(v.dash.length)
    got.forEach((x, k) => expect(x).toBeCloseTo(v.dash[k], 3))
    expect(s.strokeLinecap).toBe(v.cap)
    expect(s.strokeMiterlimit).toBe(v.miter_limit)
  })
})
