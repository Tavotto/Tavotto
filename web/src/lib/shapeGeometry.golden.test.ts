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
import { dashArray, polygonPoints } from './shapeGeometry'
import type { DashStyle } from '@/types/document'

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
