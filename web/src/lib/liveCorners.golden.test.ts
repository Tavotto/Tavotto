/**
 * 实时圆角的**前端那一侧**对拍共享向量 `tests/golden/live_corner_vectors.json`（参考实现在
 * `scripts/dev/gen_live_corner_vectors.py`，独立写的）；后端 `tests/test_live_corners.py` 用同一份结果核导出。
 */
import { describe, expect, it } from 'vitest'
import vectors from '../../../tests/golden/live_corner_vectors.json'
import { roundCorners } from './liveCorners'
import type { MmSubpath, XY } from './pathEdit'

describe('roundCorners 对拍参考实现', () => {
  it.each(vectors.cases)('$name', (c) => {
    const sp: MmSubpath = {
      closed: c.closed,
      nodes: c.points.map((p) => ({ p: p as XY, in: p as XY, out: p as XY, smooth: false })),
    }
    const [out] = roundCorners([sp], c.corners.map((i) => ({ sp: 0, i })), c.r)
    expect(out.nodes).toHaveLength(c.out.length)
    out.nodes.forEach((n, k) => {
      for (const f of ['p', 'in', 'out'] as const) {
        n[f].forEach((v, j) => expect(v).toBeCloseTo(c.out[k][f][j], 9))
      }
    })
  })
})
