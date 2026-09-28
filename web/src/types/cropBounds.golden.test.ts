/**
 * 「引擎收得下的裁剪框」是前后端的同源对：前端写进文档之前用 `cropInBounds` 把关，RenderCore 在
 * `rendercore/ir._crop` 按同一条判据拒收——判据漂了，前端放行的 crop 会让整份排版导不出（#688）。
 * 两侧各读 `tests/golden/crop_bounds_vectors.json`，不读对方源码（后端那一侧是
 * `tests/test_rendercore_ir.py::test_crop_bounds_*_is_the_golden_pair`）。
 */
import { describe, expect, it } from 'vitest'

import golden from '../../../tests/golden/crop_bounds_vectors.json'
import { cropInBounds } from './document'

const rect = ([x, y, w, h]: number[]) => ({ x, y, w, h })

describe('裁剪框的合法性（同源对）', () => {
  it('向量里该收的收、该拒的拒', () => {
    for (const c of golden.accept) expect(cropInBounds(rect(c)), JSON.stringify(c)).toBe(true)
    for (const c of golden.reject) expect(cropInBounds(rect(c)), JSON.stringify(c)).toBe(false)
  })

  it('非有限数一律拒（JSON 表达不了，单列）', () => {
    expect(cropInBounds({ x: Number.NaN, y: 0, w: 0.5, h: 0.5 })).toBe(false)
    expect(cropInBounds({ x: 0, y: 0, w: Number.POSITIVE_INFINITY, h: 0.5 })).toBe(false)
  })
})
