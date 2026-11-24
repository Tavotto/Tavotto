/**
 * 路径文字的路径几何（ADR 0190）：前端 `lib/pathText` 与后端 `rendercore/pathtext` 对拍 `tests/golden/path_text_vectors.json`
 * （`scripts/dev/gen_path_text_vectors.py` 用后端生成）——起点柄画在哪、点在路径上落哪，与后端落字用的是同一条折线。
 */
import { describe, expect, it } from 'vitest'
import vectors from '../../../tests/golden/path_text_vectors.json'
import type { PathSubpath, PathTextObject } from '@/types/document'
import { arcPathOf, clampOffset, FLATTEN_STEPS, nearestOffset, pointAt, startHandle } from './pathText'

const obj = (c: { subpaths: unknown; flip?: boolean; w: number; h: number }, extra: Partial<PathTextObject> = {}): PathTextObject => ({
  id: 'pt',
  type: 'pathText',
  x: 0,
  y: 0,
  w: c.w,
  h: c.h,
  text: 'x',
  sizePt: 10,
  bold: false,
  color: '#000000',
  subpaths: c.subpaths as PathSubpath[],
  ...(c.flip ? { flip: true as const } : {}),
  ...extra,
})

describe('路径文字的路径与后端对拍', () => {
  it('展平段数同一个数', () => expect(FLATTEN_STEPS).toBe(vectors.flatten_steps))
  it.each(vectors.cases)('$name', (c) => {
    const a = arcPathOf(obj(c), c.w, c.h)
    expect(a.pts).toHaveLength(c.n_pts)
    expect(a.closed).toBe(c.closed)
    expect(a.cum[a.cum.length - 1]).toBeCloseTo(c.total, 9)
    expect(a.pts[0][0]).toBeCloseTo(c.first[0], 9)
    expect(a.pts[0][1]).toBeCloseTo(c.first[1], 9)
    expect(a.pts.at(-1)![0]).toBeCloseTo(c.last[0], 9)
    for (const [f, want] of c.samples as [number, number[] | null][]) {
      const got = pointAt(a, f * c.total)
      if (want == null) {
        expect(got, `s=${f}`).toBeNull()
        continue
      }
      expect(got, `s=${f}`).not.toBeNull()
      expect(got!.x).toBeCloseTo(want[0], 7)
      expect(got!.y).toBeCloseTo(want[1], 7)
      // 角度在 ±π 处可能差一个 2π：比方向向量
      expect(Math.cos(got!.angle)).toBeCloseTo(Math.cos(want[2]), 7)
      expect(Math.sin(got!.angle)).toBeCloseTo(Math.sin(want[2]), 7)
    }
  })
})

describe('点到路径', () => {
  const line = vectors.cases.find((c) => c.name === 'line')!
  it('最近点的弧长比例与距离', () => {
    const a = arcPathOf(obj(line), line.w, line.h)
    const hit = nearestOffset(a, [20, 3])!
    expect(hit.offset).toBeCloseTo(0.4, 9)
    expect(hit.dist).toBeCloseTo(3 - 0.25, 9)
    // 两头之外钳到端点
    expect(nearestOffset(a, [-5, 0.25])!.offset).toBe(0)
    expect(nearestOffset(a, [80, 0.25])!.offset).toBe(1)
  })
  it('翻转后比例沿反方向量（与后端起点同一口径）', () => {
    const a = arcPathOf(obj({ ...line, flip: true }), line.w, line.h)
    expect(nearestOffset(a, [20, 0])!.offset).toBeCloseTo(0.6, 9)
  })
  it('起点柄落在起点比例处', () => {
    const h = startHandle(obj(line, { startOffset: 0.25 }), line.w, line.h)!
    expect(h.x).toBeCloseTo(12.5, 9)
    expect(h.y).toBeCloseTo(0.25, 9)
    expect(clampOffset(undefined)).toBe(0)
    expect(clampOffset(3)).toBe(1)
    expect(clampOffset(Number.NaN)).toBe(0)
  })
})
