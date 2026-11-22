/**
 * 剪切蒙版的**前端那一侧**对拍 `tests/golden/clip_mask_vectors.json`（后端 `plan.clip_outline` 生成，
 * `scripts/dev/gen_clip_mask_vectors.py`）：载荷里的 `clip` 与后端读的是同一个形状，画布上裁的轮廓与导出裁的逐点相同。
 * 后端 `tests/test_clip_mask.py` 用同一份结果核导出。
 */
import { describe, expect, it } from 'vitest'
import vectors from '../../../tests/golden/clip_mask_vectors.json'
import type { CanvasObject, PathObject } from '@/types/document'
import { maskOutline } from './clipMask'
import { toExportObjects } from './exportPayload'

function maskOf(c: (typeof vectors.cases)[number]['canvas']): PathObject {
  return {
    id: 'm',
    type: 'path',
    strokePt: 1,
    color: '#000000',
    fill: '#888888',
    groupId: 'g1',
    clipMask: true,
    hidden: true,
    ...c,
  } as unknown as PathObject
}

const member: CanvasObject = {
  id: 'r',
  type: 'shape',
  shape: 'rect',
  x: 0,
  y: 0,
  w: 100,
  h: 100,
  strokePt: 1,
  color: '#000000',
  fill: '#ff0000',
  groupId: 'g1',
} as CanvasObject

describe('剪切蒙版对拍后端', () => {
  it.each(vectors.cases)('$name：载荷与后端读的同形', (c) => {
    const out = toExportObjects([member, maskOf(c.canvas)])
    expect(out).toHaveLength(1) // 蒙版自己不画
    expect(out[0].clip).toEqual([c.export])
  })

  it.each(vectors.cases)('$name：画布轮廓与导出逐点相同', (c) => {
    const o = maskOutline(maskOf(c.canvas))!
    expect(o.fillRule).toBe(c.fill_rule)
    expect(o.segs).toHaveLength(c.outline_mm.length)
    o.segs.forEach((s, i) => {
      const want = c.outline_mm[i]
      expect(s[0]).toBe(want[0])
      s.slice(1).forEach((v, j) => expect(v as number).toBeCloseTo(want[j + 1] as number, 6))
    })
  })
})
