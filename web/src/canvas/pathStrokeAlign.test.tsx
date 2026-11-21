/**
 * 路径的描边对齐（ADR 0185 M2）：画布 / 缩略图（`PathBody`）、导出载荷、导出裁切外扩与后端 `geometry.path_paths`
 * 同一条判据（`pathGeom.pathStrokeAlign`：设了内侧 / 外侧、有描边、全部子路径闭合）。
 */
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { visibleContentBounds } from '@/lib/exportCrop'
import { toExportObjects } from '@/lib/exportPayload'
import { outsideClipPad, pathStrokeAlign } from '@/lib/pathGeom'
import type { PathObject } from '@/types/document'
import { PathBody } from './PathView'

const box: PathObject = {
  id: 'p',
  type: 'path',
  x: 10,
  y: 10,
  w: 40,
  h: 30,
  strokePt: 2,
  color: '#000000',
  fill: '#ff0000',
  subpaths: [{ closed: true, nodes: [{ p: [0, 0] }, { p: [1, 0] }, { p: [1, 1] }, { p: [0, 1] }] }],
}

describe('生效判据', () => {
  it('内侧 / 外侧只对有描边、全闭合的路径生效', () => {
    expect(pathStrokeAlign(box)).toBeNull()
    expect(pathStrokeAlign({ ...box, strokeAlign: 'inside' })).toBe('inside')
    expect(pathStrokeAlign({ ...box, strokeAlign: 'outside' })).toBe('outside')
    expect(pathStrokeAlign({ ...box, strokeAlign: 'inside', stroke: false })).toBeNull()
    expect(
      pathStrokeAlign({ ...box, strokeAlign: 'inside', subpaths: [{ closed: false, nodes: [{ p: [0, 0] }, { p: [1, 1] }] }] }),
    ).toBeNull()
  })
  it('外侧裁剪区的外扩量与后端 outside_clip_pad 同一个数', () => {
    expect(outsideClipPad(40, 30, 2)).toBe(48)
  })
})

describe('PathBody', () => {
  const svg = (o: PathObject) => renderToStaticMarkup(<svg><PathBody obj={o} sw={2} w={40} h={30} /></svg>)
  it('居中：一条路径又填又描，没有裁剪', () => {
    const s = svg(box)
    expect(s).not.toContain('clipPath')
    expect(s).toContain('stroke-width="2"')
  })
  it('内侧：填充一条 + 两倍线宽的描边裁到路径里面', () => {
    const s = svg({ ...box, strokeAlign: 'inside' })
    expect(s).toContain('<clipPath')
    expect(s).toContain('data-stroke-align="inside"')
    expect(s).toContain('stroke-width="4"')
    expect(s).not.toContain('clip-rule="evenodd"')
  })
  it('外侧：裁剪区是大矩形 + 路径（奇偶）', () => {
    const s = svg({ ...box, strokeAlign: 'outside' })
    expect(s).toContain('clip-rule="evenodd"')
    expect(s).toContain('M-48 -48H88V78H-48Z')
  })
})

describe('导出', () => {
  it('载荷：路径只发 inside / outside；缺省不发', () => {
    const [a, b] = toExportObjects([box, { ...box, id: 'q', strokeAlign: 'outside' }])
    expect('stroke_align' in a).toBe(false)
    expect((b as { stroke_align?: string }).stroke_align).toBe('outside')
  })
  it('裁切外扩：内侧不越出曲线、居中越出半个线宽、外侧越出一整个线宽', () => {
    const sw = (2 * 25.4) / 72
    const w = (o: PathObject) => visibleContentBounds([o])!.w
    expect(w({ ...box, strokeAlign: 'inside' })).toBeCloseTo(40, 6)
    expect(w(box)).toBeCloseTo(40 + sw, 6)
    expect(w({ ...box, strokeAlign: 'outside' })).toBeCloseTo(40 + 2 * sw, 6)
  })
})
