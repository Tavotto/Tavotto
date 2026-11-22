/**
 * 变换面板的**前端那一侧**对拍参考实现 `tests/golden/transform_vectors.json`（`scripts/dev/gen_transform_vectors.py`，
 * 独立写的）：镜像 / 倾斜之后的路径对象（框、旋转角、比例坐标锚点）逐项相同。后端 `tests/test_transform_vectors.py`
 * 用同一份结果核导出几何。
 */
import { describe, expect, it } from 'vitest'
import vectors from '../../../tests/golden/transform_vectors.json'
import type { PathObject } from '@/types/document'
import { reflectObject, skewObject } from './transform'

describe('镜像 / 倾斜对拍参考实现', () => {
  it.each(vectors.cases)('$name', (c) => {
    const o = c.obj as unknown as PathObject
    const ref = c.ref as [number, number]
    const got = (
      c.op.kind === 'skew' ? skewObject(o, c.op.deg as number, ref) : reflectObject(o, c.op.axis as 'h' | 'v', ref)
    ) as PathObject
    const e = c.expect
    for (const f of ['x', 'y', 'w', 'h'] as const) expect(got[f]).toBeCloseTo(e[f], 9)
    expect(got.rotationDeg ?? 0).toBeCloseTo(e.rotationDeg, 9)
    expect(got.subpaths).toHaveLength(e.subpaths.length)
    got.subpaths.forEach((sp, i) => {
      expect(sp.closed).toBe(e.subpaths[i].closed)
      sp.nodes.forEach((n, j) => {
        const w = e.subpaths[i].nodes[j]
        for (const k of ['p', 'in', 'out'] as const) {
          const v = n[k] ?? n.p
          expect(v[0]).toBeCloseTo(w[k][0], 9)
          expect(v[1]).toBeCloseTo(w[k][1], 9)
        }
      })
    })
  })
})
