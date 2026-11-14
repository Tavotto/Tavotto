/**
 * 自由路径几何的**前端那一侧**（`pathGeom.pathGeometry` ↔ `rendercore/geometry.path_segments`，ADR 0185 §四）。
 *
 * 数字由共享向量 `tests/golden/path_vectors.json` 接住：后端 `tests/test_path_object.py` 与这里各跑一遍同一份，
 * 两侧是各自独立的实现。这里还断言导出载荷（`toExportObjects`）产出向量里的 `payload`——后端拿到的正是它。
 * 改公式时两侧一起改、用 `scripts/dev/gen_path_vectors.py` 重录；别在这里手抄期望值。
 */
import { describe, expect, it } from 'vitest'
import vectors from '../../../tests/golden/path_vectors.json'
import type { CanvasObject, PathObject } from '@/types/document'
import { toExportObjects } from './exportPayload'
import { pathGeometry, rotateAbout, segsExtent, type PathSeg } from './pathGeom'
import type { ArrowHeadShape } from './shapeGeometry'

type Vec = (typeof vectors.cases)[number]

const close = (got: readonly number[], want: readonly number[], what: string) => {
  expect(got.length, what).toBe(want.length)
  got.forEach((v, i) => expect(v, `${what}[${i}]`).toBeCloseTo(want[i], 9))
}

function segsClose(got: PathSeg[], want: readonly (readonly (string | number)[])[], what: string) {
  expect(got.length, `${what} 段数`).toBe(want.length)
  got.forEach((s, i) => {
    expect(s[0], `${what}[${i}] 指令`).toBe(want[i][0])
    close(s.slice(1) as number[], want[i].slice(1) as number[], `${what}[${i}]`)
  })
}

/** 前端端型（SVG 串）→ 向量里的形状（后端 `head_geometry` 的元组形） */
function headJson(h: ArrowHeadShape): { kind: string; points?: number[][]; cx?: number; cy?: number; r?: number } {
  const pairs = (s: string) =>
    s
      .replace(/[ML]/g, ' ')
      .trim()
      .split(/\s+/)
      .map((p) => p.split(',').map(Number))
  if (h.kind === 'dot') return { kind: 'dot', cx: h.cx, cy: h.cy, r: h.r }
  if ('d' in h) return { kind: h.kind, points: pairs(h.d) }
  return { kind: 'poly', points: pairs(h.points) }
}

describe('pathGeometry：与共享向量逐点相同', () => {
  it.each(vectors.cases)('$name', (vec: Vec) => {
    const doc = vec.doc as unknown as PathObject
    const g = pathGeometry(doc, vec.w, vec.h, vec.sw)
    segsClose(g.fill, vec.fill, 'fill')
    segsClose(g.stroke, vec.stroke, 'stroke')
    expect(g.headHalf).toBeCloseTo(vec.head_half, 9)
    expect(g.heads.length, '端型个数').toBe(vec.heads.length)
    g.heads.forEach((h, i) => {
      const got = headJson(h)
      const want = vec.heads[i] as { kind: string; points?: number[][]; cx?: number; cy?: number; r?: number }
      expect(got.kind, `heads[${i}] 种类`).toBe(want.kind)
      if (want.points) close(got.points!.flat(), want.points.flat(), `heads[${i}]`)
      else close([got.cx!, got.cy!, got.r!], [want.cx!, want.cy!, want.r!], `heads[${i}]`)
    })
  })

  it.each(vectors.cases)('$name：曲线实际包围盒（不按控制点）', (vec: Vec) => {
    const g = pathGeometry(vec.doc as unknown as PathObject, vec.w, vec.h, vec.sw)
    const e = segsExtent(g.fill)
    if (vec.extent == null) expect(e).toBeNull()
    else close([e!.x0, e!.y0, e!.x1, e!.y1], vec.extent, 'extent')
  })

  it.each(vectors.cases)('$name：导出载荷与向量的 payload 逐字段相同', (vec: Vec) => {
    const [payload] = toExportObjects([vec.doc as unknown as CanvasObject])
    expect(JSON.parse(JSON.stringify(payload))).toEqual(vec.payload)
  })

  it.each(vectors.cases)('$name：锚点在页面上的位置（含旋转）与后端的对象变换一致', (vec: Vec) => {
    const doc = vec.doc as unknown as PathObject
    const c: [number, number] = [doc.x + doc.w / 2, doc.y + doc.h / 2]
    const got = doc.subpaths.flatMap((sp) =>
      sp.nodes.map((n) => rotateAbout([doc.x + n.p[0] * doc.w, doc.y + n.p[1] * doc.h], c, doc.rotationDeg ?? 0)),
    )
    close(got.flat(), vec.page_points.flat(), 'page_points')
  })

  it('向量集覆盖了 ADR 0185 §四 点名的几种', () => {
    const names = vectors.cases.map((c) => c.name)
    for (const n of ['closed_corner', 'closed_smooth', 'open_heads', 'multi_evenodd', 'handles_outside_box', 'rotated']) {
      expect(names).toContain(n)
    }
    // 手柄落在框外：曲线实际范围 ≠ 控制点范围（控制点会超出 [0, h]，曲线不会）
    const out = vectors.cases.find((c) => c.name === 'handles_outside_box')!
    expect(out.extent![1]).toBeGreaterThan(-0.6 * out.h)
    expect(out.extent![1]).toBeLessThan(out.h)
  })
})
