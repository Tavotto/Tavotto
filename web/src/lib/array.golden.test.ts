/**
 * 阵列的**前端那一侧**对拍参考实现 `tests/golden/array_vectors.json`（`scripts/dev/gen_array_vectors.py`，独立写的）：每一份的框与旋转角
 * 逐项相同；副本的 id / 组 id 全换新。后端 `tests/test_array_vectors.py` 在同一份结果上核几何不变量。
 */
import { describe, expect, it } from 'vitest'
import vectors from '../../../tests/golden/array_vectors.json'
import type { CanvasObject } from '@/types/document'
import { arrayCount, makeArray, type ArraySpec } from './array'

describe('阵列对拍参考实现', () => {
  it.each(vectors.cases)('$name', (c) => {
    const objs = c.objs as unknown as CanvasObject[]
    const spec = c.spec as ArraySpec
    const made = makeArray(objs, spec)
    expect(made).toHaveLength((arrayCount(spec) - 1) * objs.length)
    c.expect.forEach((copy, i) => {
      copy.forEach((want, k) => {
        const got = made[i * objs.length + k]
        expect(got.id).not.toBe(objs[k].id)
        expect(got.x).toBeCloseTo(want.x, 9)
        expect(got.y).toBeCloseTo(want.y, 9)
        expect(got.rotationDeg ?? 0).toBeCloseTo(want.rotationDeg, 9)
        expect(got.w).toBe(objs[k].w)
      })
    })
  })

  it('组：每一份各一套新的组 id，不和原件、也不和别的份粘在一起', () => {
    const objs = [
      { id: 'a', type: 'shape', shape: 'rect', x: 0, y: 0, w: 2, h: 2, strokePt: 1, color: '#000', fill: null, groupId: 'G' },
      { id: 'b', type: 'shape', shape: 'rect', x: 3, y: 0, w: 2, h: 2, strokePt: 1, color: '#000', fill: null, groupId: 'G' },
    ] as unknown as CanvasObject[]
    const made = makeArray(objs, { kind: 'grid', rows: 1, cols: 3, gapX: 1, gapY: 0 })
    const gids = made.map((o) => o.groupId)
    expect(gids[0]).toBe(gids[1])
    expect(gids[2]).toBe(gids[3])
    expect(new Set([...gids, 'G']).size).toBe(3)
  })
})
