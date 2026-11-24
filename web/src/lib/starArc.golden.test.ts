/**
 * 星形 / 弧线几何的**前端那一侧**（`shapeGeometry.starPoints` / `arcPoints` ↔ `rendercore/geometry.star_points` /
 * `arc_segments`，ADR 0185 M1）。共享向量 `tests/golden/star_arc_vectors.json`，后端 `tests/test_star_arc.py` 跑同一份。
 */
import { describe, expect, it } from 'vitest'
import vectors from '../../../tests/golden/star_arc_vectors.json'
import { arcPoints, starPoints } from './shapeGeometry'

describe('starPoints：与共享向量逐点相同', () => {
  it.each(vectors.star)('$points 角 @ $w×$h inset $inset 内径比 $ratio', (v) => {
    const got = starPoints(v.points, v.w, v.h, v.inset, v.ratio)
    expect(got.length).toBe(v.out.length)
    got.forEach((p, i) => {
      expect(p[0]).toBeCloseTo(v.out[i][0], 9)
      expect(p[1]).toBeCloseTo(v.out[i][1], 9)
    })
  })
})

describe('arcPoints：与共享向量逐点相同', () => {
  it.each(vectors.arc)('$w×$h inset $inset', (v) => {
    const [p0, c1, c2, p3] = arcPoints(v.w, v.h, v.inset)
    const [m, c] = v.segments as [string, ...number[]][]
    expect(m[0]).toBe('M')
    expect(c[0]).toBe('C')
    const want = [...m.slice(1), ...c.slice(1)] as number[]
    ;[...p0, ...c1, ...c2, ...p3].forEach((x, i) => expect(x).toBeCloseTo(want[i], 9))
  })
})
