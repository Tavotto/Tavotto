/**
 * 「转为路径」的前后端对拍样本（ADR 0212：Agent 的布尔 / 偏移 / 轮廓化收形状，后端 `engine/vectorgeom.to_path_object`
 * 是这里 `lib/convertToPath.toPathObject` 的 Python 版）。原对象与转出来的路径都是**文档形态**（camelCase），写进
 * `tests/golden/convert_to_path_vectors.json`；后端 `tests/test_vector_convert.py` 读同一份逐数比（后端保留 6 位小数）。
 *
 * 平时这里核「现在算出来的 = 金样」（改了几何就红）；`UPDATE_GOLDEN=1` 时重写金样。
 */
import { readFileSync, writeFileSync } from 'node:fs'
import path from 'node:path'
import { describe, expect, it } from 'vitest'
import type { ArrowObject, CanvasObject, ShapeObject } from '@/types/document'
import { toPathObject } from './convertToPath'

const GOLDEN = path.resolve(process.cwd(), '../tests/golden/convert_to_path_vectors.json')

const shape = (over: Partial<ShapeObject>): ShapeObject => ({
  id: 's1', type: 'shape', shape: 'rect', x: 12, y: 9, w: 30, h: 18, strokePt: 1.5, color: '#1b3a4b', fill: null,
  ...over,
})
const arrow = (over: Partial<ArrowObject>): ArrowObject => ({
  id: 'a1', type: 'arrow', x: 10, y: 20, w: 40, h: 12, start: { rx: 0, ry: 1 }, end: { rx: 1, ry: 0 },
  strokePt: 1, color: '#2f6fe0', head: 'end', ...over,
})
const lin = { kind: 'linear' as const, from: [0, 0] as [number, number], to: [1, 1] as [number, number], stops: [{ offset: 0, color: '#ff0000' }, { offset: 1, color: '#0000ff' }] }
const rad = { kind: 'radial' as const, center: [0.3, 0.4] as [number, number], radius: 0.6, stops: [{ offset: 0, color: '#ffffff' }, { offset: 1, color: '#000000' }] }

export const CONVERT_CASES: { name: string; obj: CanvasObject }[] = [
  { name: 'rect-plain', obj: shape({}) },
  { name: 'rect-fill-nostroke', obj: shape({ fill: '#ffcc00', stroke: false, fillOpacity: 0.5 }) },
  { name: 'rect-rounded', obj: shape({ cornerRadius: 3, fill: '#eeeeee' }) },
  { name: 'rect-rounded-clamped', obj: shape({ cornerRadius: 40, h: 8 }) },
  { name: 'rect-align-center', obj: shape({ strokeAlign: 'center', strokePt: 4 }) },
  { name: 'rect-align-outside', obj: shape({ strokeAlign: 'outside', strokePt: 4, lineJoin: 'miter' }) },
  { name: 'rect-rotated-gradient', obj: shape({ rotationDeg: 30, fill: '#ff0000', fillGradient: lin, strokeGradient: rad }) },
  { name: 'ellipse', obj: shape({ shape: 'ellipse', fill: '#88aaff', strokeOpacity: 0.7 }) },
  { name: 'ellipse-rotated', obj: shape({ shape: 'ellipse', rotationDeg: -45, w: 20, h: 10 }) },
  { name: 'triangle', obj: shape({ shape: 'triangle', dash: 'dashed' }) },
  { name: 'diamond', obj: shape({ shape: 'diamond', dashPattern: [3, 1.5], miterLimit: 4 }) },
  { name: 'polygon-8', obj: shape({ shape: 'polygon', sides: 8, w: 20, h: 20 }) },
  { name: 'polygon-default', obj: shape({ shape: 'polygon' }) },
  { name: 'star-6', obj: shape({ shape: 'star', sides: 6, starRatio: 0.5, w: 24, h: 24, fill: '#ffd700' }) },
  { name: 'star-default', obj: shape({ shape: 'star', w: 24, h: 24 }) },
  { name: 'brace', obj: shape({ shape: 'brace', w: 6, h: 30 }) },
  { name: 'arc', obj: shape({ shape: 'arc', w: 20, h: 14, lineCap: 'butt' }) },
  { name: 'line-default', obj: shape({ shape: 'line', h: 2 }) },
  { name: 'line-ends', obj: shape({ shape: 'line', start: { rx: 0.1, ry: 0.9 }, end: { rx: 0.8, ry: 0.2 }, dash: 'dotted' }) },
  { name: 'shape-meta', obj: shape({ shape: 'ellipse', name: '左上的圈', locked: true, hidden: true, groupId: 'g_inner', groupPath: ['g_outer', 'g_inner'], layoutPinned: true, fill: '#ffffff' } as Partial<ShapeObject>) },
  { name: 'arrow-legacy-end', obj: arrow({}) },
  { name: 'arrow-legacy-both', obj: arrow({ head: 'both', dash: 'longdash' }) },
  { name: 'arrow-heads', obj: arrow({ headStart: 'dot', headEnd: 'stealth', headSizeEnd: 'large', headSizeStart: 'small', lineCap: 'square', lineJoin: 'bevel' }) },
  { name: 'arrow-none-rotated', obj: arrow({ head: 'none', rotationDeg: 15, strokeOpacity: 0.4 }) },
  { name: 'arrow-flat', obj: arrow({ h: 0.001, start: { rx: 0, ry: 0.5 }, end: { rx: 1, ry: 0.5 } }) },
]

const plain = (v: unknown): unknown => JSON.parse(JSON.stringify(v))

function build() {
  return CONVERT_CASES.map(({ name, obj }) => {
    const out = toPathObject(obj as ShapeObject | ArrowObject)
    if (!out) throw new Error(`转不出：${name}`)
    return { name, input: plain(obj), output: plain(out) }
  })
}

describe('转为路径的前后端对拍样本', () => {
  it('现在算出来的就是金样（UPDATE_GOLDEN=1 重写）', () => {
    const cases = build()
    if (process.env.UPDATE_GOLDEN) {
      writeFileSync(
        GOLDEN,
        JSON.stringify(
          { note: '前端 lib/convertToPath.toPathObject 的输出（文档形态）；后端 tests/test_vector_convert.py 逐数比', cases },
          null,
          1,
        ) + '\n',
      )
    }
    const golden = JSON.parse(readFileSync(GOLDEN, 'utf8')) as { cases: unknown[] }
    expect(cases).toEqual(golden.cases)
  })
})
