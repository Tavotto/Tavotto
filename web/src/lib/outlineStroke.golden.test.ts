/**
 * 轮廓化描边的像素对拍样本（ADR 0185 M7）：前端（真 WASM 内核）把虚线、端型、内外对齐的描边转成面积，原对象与转出来的对象的
 * 导出载荷一起写进 `tests/golden/outline_stroke_vectors.json`；后端 `tests/test_outline_stroke.py` 把两边按导出画出来逐像素比。
 *
 * 每例另带 `doc`（原对象与结果的文档形态），后端 `tests/test_vector_outline.py` 拿它与 Python 版逐数比。
 *
 * 平时这里核「现在算出来的 = 金样」（改了几何 / 换了内核就红）；`UPDATE_GOLDEN=1` 时重写金样。
 */
import { readFileSync, writeFileSync } from 'node:fs'
import path from 'node:path'
import { afterAll, beforeAll, describe, expect, it } from 'vitest'
import type { ArrowObject, CanvasObject, PathObject, ShapeObject } from '@/types/document'
import { toExportObjects } from './exportPayload'
import { outlineStrokes } from './pathfinder'
import { loadVecops, setVecopsSource, type VecopsKernel } from './vecops/kernel'

const WASM = path.resolve(process.cwd(), 'src/lib/vecops/vecops.wasm')
const GOLDEN = path.resolve(process.cwd(), '../tests/golden/outline_stroke_vectors.json')

const open = (over: Partial<PathObject>): PathObject => ({
  id: 'o', type: 'path', x: 8, y: 10, w: 50, h: 18,
  subpaths: [{ closed: false, nodes: [{ p: [0, 1], out: [0.3, -0.6] }, { p: [1, 0.6], in: [0.6, 1.4] }] }],
  strokePt: 2, color: '#1b3a4b', fill: null, ...over,
})
const box = (over: Partial<PathObject>): PathObject => ({
  id: 'b', type: 'path', x: 10, y: 8, w: 40, h: 22,
  subpaths: [{ closed: true, nodes: [{ p: [0, 0] }, { p: [1, 0] }, { p: [1, 1] }, { p: [0.5, 0.6] }, { p: [0, 1] }] }],
  strokePt: 3, color: '#9a3412', fill: null, ...over,
})

const CASES: { name: string; obj: CanvasObject }[] = [
  { name: 'dashed-curve-butt', obj: open({ dash: 'dashed', lineCap: 'butt' }) },
  { name: 'dashed-curve-round', obj: open({ dash: 'longdashdot', lineCap: 'round' }) },
  { name: 'custom-dash-square', obj: open({ dashPattern: [6, 3, 1, 3], lineCap: 'square' }) },
  { name: 'dotted', obj: open({ dash: 'dotted' }) },
  { name: 'heads-triangle-open', obj: open({ headEnd: 'triangle', headStart: 'open' }) },
  { name: 'heads-dot-bar-dashed', obj: open({ headEnd: 'dot', headStart: 'bar', dash: 'dashed' }) },
  { name: 'align-inside', obj: box({ strokeAlign: 'inside' }) },
  { name: 'align-outside-miter', obj: box({ strokeAlign: 'outside', lineJoin: 'miter' }) },
  { name: 'align-inside-dashed', obj: box({ strokeAlign: 'inside', dash: 'dashed', lineCap: 'butt' }) },
  { name: 'rotated-dashed-fill', obj: box({ rotationDeg: 25, dash: 'dashdot', fill: '#ffcc00' }) },
  {
    name: 'arrow-stealth',
    obj: { id: 'a', type: 'arrow', x: 10, y: 12, w: 40, h: 14, start: { rx: 0, ry: 1 }, end: { rx: 1, ry: 0 }, strokePt: 1.5, color: '#2f6fe0', head: 'end', headEnd: 'stealth', headStart: 'dot' } as ArrowObject,
  },
  {
    name: 'shape-star-dashed',
    obj: { id: 's', type: 'shape', shape: 'star', x: 15, y: 5, w: 30, h: 30, sides: 5, strokePt: 2, color: '#444444', fill: null, dash: 'dashed' } as ShapeObject,
  },
]

let kernel: VecopsKernel
beforeAll(async () => {
  setVecopsSource(async () => {
    const b = readFileSync(WASM)
    return b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength) as ArrayBuffer
  })
  kernel = (await loadVecops())!
})
afterAll(() => setVecopsSource(null))

const round = (v: unknown): unknown =>
  typeof v === 'number' ? Math.round(v * 1e5) / 1e5 : Array.isArray(v) ? v.map(round) : v && typeof v === 'object' ? Object.fromEntries(Object.entries(v).filter(([, x]) => x !== undefined).map(([k, x]) => [k, round(x)])) : v

function build() {
  return CASES.map(({ name, obj }) => {
    const made = outlineStrokes(kernel, [obj], [obj.id])
    if (!made) throw new Error(`算不出：${name}`)
    const after = made.flatMap((m) => [m.replace, ...(m.extra ? [m.extra] : [])])
    // 文档形态（camelCase、不取整）给后端 `engine/vectorgeom.outline_stroke` 逐数对拍（`tests/test_vector_outline.py`）；
    // 新长出来的轮廓 id 是随机的，写成固定的 `extra`
    const doc = made.flatMap((m) => [m.replace, ...(m.extra ? [{ ...m.extra, id: 'extra' }] : [])])
    return {
      name,
      original: round(toExportObjects([obj])),
      outlined: round(toExportObjects(after)),
      doc: JSON.parse(JSON.stringify({ original: obj, outlined: doc })),
    }
  })
}

describe('轮廓化描边的像素对拍样本', () => {
  it('现在算出来的就是金样（UPDATE_GOLDEN=1 重写）', () => {
    const cases = build()
    if (process.env.UPDATE_GOLDEN) {
      writeFileSync(GOLDEN, JSON.stringify({ note: '前端 lib/outlineStroke + vecops 内核的输出；后端 tests/test_outline_stroke.py 按导出逐像素比', cases }, null, 1) + '\n')
    }
    const golden = JSON.parse(readFileSync(GOLDEN, 'utf8')) as { cases: unknown[] }
    expect(JSON.parse(JSON.stringify(cases))).toEqual(golden.cases)
  })
})

describe('闭合路径的虚线接缝', () => {
  const sq = (over: Partial<PathObject>): PathObject => ({
    id: 'sq', type: 'path', x: 0, y: 0, w: 10, h: 10,
    subpaths: [{ closed: true, nodes: [{ p: [0, 0] }, { p: [1, 0] }, { p: [1, 1] }, { p: [0, 1] }] }],
    strokePt: 4, color: '#000000', fill: null, lineJoin: 'miter', ...over,
  })
  const area = (o: PathObject) => {
    const made = outlineStrokes(kernel, [o], [o.id])!
    return made.flatMap((m) => [m.replace, ...(m.extra ? [m.extra] : [])])
  }
  it('一段画段盖满整圈（[200, 5]）：与实线轮廓化的结果相同（起点拐角保持斜接，不出笔帽）', () => {
    const solid = JSON.stringify(round(toExportObjects(area(sq({})))))
    const dashed = JSON.stringify(round(toExportObjects(area(sq({ dashPattern: [200, 5] })))))
    expect(dashed.replace(/"id":"[^"]*",?/g, '')).toBe(solid.replace(/"id":"[^"]*",?/g, ''))
  })
})
