/**
 * 形状几何两条严格同源对的**前端那一侧**（`polygonPoints` / `dashArray` ↔
 * `rendercore/geometry.py` 的 `polygon_points` / `dash_pattern`）。
 *
 * 数字由**共享向量** `tests/golden/shape_geometry_vectors.json` 接住：它是 U10 退役
 * PyMuPDF 之前从旧 facade（当年的权威）记下的（ADR 0072），pytest
 * （`tests/test_rendercore_geometry.py`）与这里各跑一遍同一份。两侧改公式时一起改、
 * 重录那份向量——在 vitest 里手抄一份「大概长这样」的期望值，抄错了就是自己捏出来
 * 的假红 / 假绿。
 *
 * `pdf_floor` 的格：dotted 在很细的线上，PDF 侧把间断段夹到 0.01 pt 下限（0 长度的
 * dash 在 PDF 里不合法），SVG 侧没有这条——那是两个宿主各自的合法差异，不是漂移。
 */
import { describe, expect, it } from 'vitest'
import vectors from '../../../tests/golden/shape_geometry_vectors.json'
import {
  arrowGeometry,
  dashArray,
  defaultLineJoin,
  HEAD_SIZE_FACTOR,
  polygonPoints,
  strokeCap,
} from './shapeGeometry'
import type { ArrowObject, DashStyle, LineCap, ShapeKind } from '@/types/document'

describe('polygonPoints：与共享向量逐位相同', () => {
  it.each(vectors.polygon)('$sides 边 @ $w×$h inset $inset', (vec) => {
    const got = polygonPoints(vec.sides, vec.w, vec.h, vec.inset).map(([x, y]) => [
      Number(x.toFixed(12)),
      Number(y.toFixed(12)),
    ])
    expect(got).toEqual(vec.points)
  })

  it('向量集覆盖了公式会夹的边界（边数 2 / 13 → 3 / 12）', () => {
    const sides = new Set(vectors.polygon.map((v) => v.sides))
    expect(sides.has(2) && sides.has(13)).toBe(true)
    expect(polygonPoints(2, 10, 10, 0)).toHaveLength(3)
    expect(polygonPoints(13, 10, 10, 0)).toHaveLength(12)
  })
})

describe('dashArray：与共享向量的比例相同', () => {
  it.each(vectors.dash.filter((v) => !v.pdf_floor))('$dash @ $stroke_pt', (vec) => {
    const got = dashArray((vec.dash ?? undefined) as DashStyle | undefined, vec.stroke_pt)
    const nums = got === undefined ? [] : got.split(' ').map(Number)
    expect(nums).toHaveLength(vec.pattern.length)
    nums.forEach((n, i) => expect(n).toBeCloseTo(vec.pattern[i], 3))
  })

  it('pdf_floor 的格只在 PDF 侧有下限：这里的比例仍是 sw×0.01（不是 0.01 常量）', () => {
    const floored = vectors.dash.filter((v) => v.pdf_floor)
    expect(floored.length).toBeGreaterThan(0)
    for (const vec of floored) {
      const got = dashArray('dotted', vec.stroke_pt)!.split(' ').map(Number)
      expect(got[0]).toBeCloseTo(vec.stroke_pt * 0.01, 6)
      expect(got[1]).toBeCloseTo(vec.pattern[1], 3)
    }
  })
})

describe('线端 / 拐角 / 端型大小：与共享向量逐格相同（ADR 0174）', () => {
  it.each(vectors.cap)('线端 $cap + 线型 $dash', (vec) => {
    expect(strokeCap((vec.cap ?? undefined) as LineCap | undefined, (vec.dash ?? undefined) as DashStyle | undefined)).toBe(
      vec.expect,
    )
  })

  it('点线无论选什么线端都是圆头（点靠圆线帽才有面积）', () => {
    for (const cap of ['butt', 'round', 'square'] as const) expect(strokeCap(cap, 'dotted')).toBe('round')
  })

  it('缺省拐角按形状族', () => {
    for (const [shape, join] of Object.entries(vectors.join_default)) {
      expect(defaultLineJoin(shape as ShapeKind)).toBe(join)
    }
  })

  it('端型三档系数', () => {
    expect(HEAD_SIZE_FACTOR).toEqual(vectors.head_size_factor)
  })
})

type Head = ReturnType<typeof arrowGeometry>['endHead']

/** 前端端型 → 向量里的写法（三种实心多边形都叫 poly；open / bar 的路径 d 拆成点） */
function encodeHead(h: Head): unknown {
  if (!h) return null
  const nums = (src: string) => (src.match(/-?\d+(?:\.\d+)?(?:e-?\d+)?/g) ?? []).map(Number)
  const pairs = (xs: number[]) => xs.reduce<number[][]>((acc, v, i) => (i % 2 ? (acc[acc.length - 1].push(v), acc) : [...acc, [v]]), [])
  const r9 = (v: number) => Number(v.toFixed(9))
  if (h.kind === 'dot') return { shape: 'dot', cx: r9(h.cx), cy: r9(h.cy), r: r9(h.r) }
  if (h.kind === 'open' || h.kind === 'bar') {
    return { shape: h.kind, points: pairs(nums(h.d)).map((p) => p.map(r9)) }
  }
  return { shape: 'poly', points: pairs(nums((h as { points: string }).points)).map((p) => p.map(r9)) }
}

describe('arrowGeometry：杆端点与端型几何与共享向量逐点相同（ADR 0174）', () => {
  it.each(vectors.arrow)('$name', (vec) => {
    const obj = {
      start: { rx: 0.1, ry: 0.2 },
      end: { rx: 0.9, ry: 0.8 },
      head: vec.head,
      headStart: vec.head_start ?? undefined,
      headEnd: vec.head_end ?? undefined,
      headSizeStart: vec.head_size_start ?? undefined,
      headSizeEnd: vec.head_size_end ?? undefined,
    } as unknown as Pick<ArrowObject, 'start' | 'end' | 'head' | 'headStart' | 'headEnd' | 'headSizeStart' | 'headSizeEnd'>
    const g = arrowGeometry(obj, vec.w, vec.h, vec.sw)
    const close = (a: number[], b: number[]) => a.forEach((v, i) => expect(v).toBeCloseTo(b[i], 7))
    close([g.p1.x, g.p1.y], vec.p1)
    close([g.p2.x, g.p2.y], vec.p2)
    for (const [got, want] of [
      [encodeHead(g.startHead), vec.start_head],
      [encodeHead(g.endHead), vec.end_head],
    ] as const) {
      if (want == null) {
        expect(got).toBeNull()
        continue
      }
      const a = got as { shape: string; points?: number[][]; cx?: number; cy?: number; r?: number }
      const b = want as typeof a
      expect(a.shape).toBe(b.shape)
      if (b.points) {
        expect(a.points).toHaveLength(b.points.length)
        b.points.forEach((pt, i) => close(a.points![i], pt))
      } else {
        close([a.cx!, a.cy!, a.r!], [b.cx!, b.cy!, b.r!])
      }
    }
  })

  it('向量覆盖了每种端型、三档大小和不认识的端型', () => {
    const names = vectors.arrow.map((v) => v.name).join(' ')
    for (const k of ['triangle', 'stealth', 'diamond', 'dot', 'open', 'bar', 'foo']) expect(names).toContain(k)
    expect(names).toContain('small')
    expect(names).toContain('large')
  })
})
