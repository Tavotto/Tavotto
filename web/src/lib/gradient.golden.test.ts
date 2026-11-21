/**
 * 渐变（ADR 0186）的**前端那一侧**：字段读法与框空间几何。共享向量 `tests/golden/gradient_vectors.json`，后端
 * `tests/test_gradient.py` 跑同一份（`geometry.read_gradient` / `gradient_paint`）；两侧独立实现，改判据两侧一起改、
 * `scripts/dev/gen_gradient_vectors.py` 重录。
 */
import { describe, expect, it } from 'vitest'
import vectors from '../../../tests/golden/gradient_vectors.json'
import { gradientGeometry, readGradient } from './gradient'

const rgb = (hex: string) => {
  const t = hex.length === 4 ? hex.slice(1).replace(/./g, (c) => c + c) : hex.slice(1)
  return [0, 2, 4].map((i) => parseInt(t.slice(i, i + 2), 16) / 255)
}
const close = (a: number[], b: number[]) => {
  expect(a.length).toBe(b.length)
  a.forEach((v, i) => expect(v).toBeCloseTo(b[i], 12))
}

describe('readGradient', () => {
  it.each(vectors.cases.map((c, i) => [i, c] as const))('#%i', (_i, c) => {
    expect(readGradient(c.in)).toEqual(c.read)
  })
})

describe('gradientGeometry（框空间、色标 alpha × 对象不透明度）', () => {
  for (const [i, c] of vectors.cases.entries()) {
    for (const p of c.paint) {
      it(`#${i} ${p.w}×${p.h} α${p.alpha}`, () => {
        const g = gradientGeometry(c.in, p.w, p.h, p.alpha)
        if (!p.out) return expect(g).toBeNull()
        const out = p.out as { kind: string; stops: number[][]; p0?: number[]; p1?: number[]; center?: number[]; radius?: number }
        expect(g?.kind).toBe(out.kind)
        const stops = g!.stops.map((s) => [s.offset, ...rgb(s.color), s.opacity])
        stops.forEach((s, k) => close(s, out.stops[k]))
        expect(stops.length).toBe(out.stops.length)
        if (g!.kind === 'linear') close([g!.x1, g!.y1, g!.x2, g!.y2], [...out.p0!, ...out.p1!])
        else close([g!.cx, g!.cy, g!.r], [...out.center!, out.radius!])
      })
    }
  }
})
