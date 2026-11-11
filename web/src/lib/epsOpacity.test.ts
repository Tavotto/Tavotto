/**
 * EPS 半透明提醒的判定（`lib/epsOpacity`）：每个字段各一对正反例。
 * 判据与后端 IR 的 `object_alpha` / `group_opacity` 对齐（`rendercore/geometry.py` / `plan.py` 怎么读这些字段）。
 */
import { describe, expect, it } from 'vitest'
import type { ExportObject } from './api'
import { toExportObjects } from './exportPayload'
import { buildExportRequest, type ExportRequestInput } from './exportRequest'
import { isEpsTranslucent, swapEpsForPdf, translucentAnnotationCount } from './epsOpacity'
import { emptyDocument, type CanvasObject } from '@/types/document'

const box = { x_mm: 1, y_mm: 1, w_mm: 10, h_mm: 5 }
const shape = (over: Record<string, unknown> = {}) =>
  ({ ...box, type: 'shape', shape: 'rect', stroke_pt: 1, color: '#000000', fill: null, ...over }) as ExportObject
const arrow = (over: Record<string, unknown> = {}) =>
  ({
    ...box,
    type: 'arrow',
    start: { rx: 0, ry: 0.5 },
    end: { rx: 1, ry: 0.5 },
    stroke_pt: 1,
    color: '#000000',
    head: 'end',
    ...over,
  }) as ExportObject

describe('isEpsTranslucent：形状', () => {
  it('填充不透明度 < 1 且有填充 → 半透明；没有填充 → 不算（后端那条路径没有填充 alpha）', () => {
    expect(isEpsTranslucent(shape({ fill: '#ff0000', fill_opacity: 0.5 }))).toBe(true)
    expect(isEpsTranslucent(shape({ fill: '#ff0000', fill_opacity: 0 }))).toBe(true)
    expect(isEpsTranslucent(shape({ fill: '#ff0000' }))).toBe(false)
    expect(isEpsTranslucent(shape({ fill: null, fill_opacity: 0.5 }))).toBe(false)
  })
  it('直线 / 大括号只有描边：填充不透明度不算', () => {
    expect(isEpsTranslucent(shape({ shape: 'line', fill: '#ff0000', fill_opacity: 0.5 }))).toBe(false)
    expect(isEpsTranslucent(shape({ shape: 'brace', fill: '#ff0000', fill_opacity: 0.5 }))).toBe(false)
  })
  it('描边不透明度 < 1 → 半透明；无边框时不算；直线 / 大括号恒有描边', () => {
    expect(isEpsTranslucent(shape({ stroke_opacity: 0.4 }))).toBe(true)
    expect(isEpsTranslucent(shape({ stroke_opacity: 0.4, stroke: false }))).toBe(false)
    expect(isEpsTranslucent(shape({ shape: 'line', stroke_opacity: 0.4, stroke: false }))).toBe(true)
    expect(isEpsTranslucent(shape({ shape: 'brace', stroke_opacity: 0.4 }))).toBe(true)
    expect(isEpsTranslucent(shape({ stroke_opacity: 1 }))).toBe(false)
  })
  it('无边框但有半透明填充 → 仍算（填充 alpha）', () => {
    expect(isEpsTranslucent(shape({ fill: '#00ff00', fill_opacity: 0.3, stroke: false }))).toBe(true)
  })
})

describe('isEpsTranslucent：箭头 / 文字', () => {
  it('箭头：描边不透明度 < 1 = 整支箭头是透明组', () => {
    expect(isEpsTranslucent(arrow({ stroke_opacity: 0.5 }))).toBe(true)
    expect(isEpsTranslucent(arrow({ stroke_opacity: 1 }))).toBe(false)
    expect(isEpsTranslucent(arrow())).toBe(false)
  })
  it('文字（颜色 / 底色 / 边框都没有 alpha）永远不算', () => {
    const text = { ...box, type: 'text', text: 'a', size_pt: 9, bold: false, italic: false, color: '#000', align: 'left' } as ExportObject
    expect(isEpsTranslucent(text)).toBe(false)
  })
  it('非有限数 / 非数字的不透明度按不透明（后端 `_unit`）', () => {
    expect(isEpsTranslucent(arrow({ stroke_opacity: Number.NaN }))).toBe(false)
    expect(isEpsTranslucent(arrow({ stroke_opacity: '0.5' }))).toBe(false)
  })
})

describe('translucentAnnotationCount', () => {
  const canvasShape = (id: string, over: Partial<CanvasObject> = {}) =>
    ({ id, type: 'shape', shape: 'rect', x: 0, y: 0, w: 10, h: 5, strokePt: 1, color: '#000000', fill: '#ff0000', ...over }) as CanvasObject

  it('数的是半透明标注的个数；隐藏的不进载荷所以不算', () => {
    const objects = toExportObjects([
      canvasShape('a', { fillOpacity: 0.5 }),
      canvasShape('b'),
      canvasShape('c', { strokeOpacity: 0.2 }),
      canvasShape('hidden', { fillOpacity: 0.5, hidden: true }),
    ])
    expect(translucentAnnotationCount(objects)).toBe(2)
  })
})

describe('swapEpsForPdf', () => {
  it('EPS 换成 PDF；PDF 已在就只是去掉 EPS；其余格式与次序不动', () => {
    expect(swapEpsForPdf(['eps'])).toEqual(['pdf'])
    expect(swapEpsForPdf(['png', 'eps'])).toEqual(['png', 'pdf'])
    expect(swapEpsForPdf(['pdf', 'png', 'eps'])).toEqual(['pdf', 'png'])
    expect(swapEpsForPdf(['pdf', 'png'])).toEqual(['pdf', 'png'])
  })
})

describe('请求里的 eps_opaque', () => {
  const route = { scope: 'original', formats: ['eps'], panels: { 'Fig1.pdf': { source: 'disk', embeds_disk_bitmap: false, formats: {}, offers: { eps: { how: 'redraw' } } } } }
  const input = (over: Partial<ExportRequestInput> = {}): ExportRequestInput => ({
    scope: 'original',
    formats: ['pdf', 'eps'],
    filename: 'Fig',
    ppi: 600,
    documentId: null,
    doc: emptyDocument(),
    figureId: 'Fig1.pdf',
    spec: { widthMm: 80, heightMm: 60, pixelWidth: null, pixelHeight: null, sourceKind: 'vector', ignored: [] } as never,
    annotations: [
      { id: 's', type: 'shape', shape: 'rect', x: 1, y: 1, w: 10, h: 5, strokePt: 1, color: '#000000', fill: '#ff0000', fillOpacity: 0.5 } as CanvasObject,
    ],
    route: route as never,
    ...over,
  })

  it('选了「EPS 按不透明」才带；其余格式的标注仍保留半透明', () => {
    const { request } = buildExportRequest(input({ epsOpaque: true }))
    expect(request.eps_opaque).toBe(true)
    expect((request.original!.objects![0] as { fill_opacity?: number }).fill_opacity).toBe(0.5)
  })
  it('不选 / 没有 EPS / 没有标注：键根本不在，请求逐字节同从前', () => {
    expect('eps_opaque' in buildExportRequest(input()).request).toBe(false)
    expect('eps_opaque' in buildExportRequest(input({ epsOpaque: true, formats: ['pdf'] })).request).toBe(false)
    expect('eps_opaque' in buildExportRequest(input({ epsOpaque: true, annotations: [] })).request).toBe(false)
    expect(JSON.stringify(buildExportRequest(input({ epsOpaque: false })).request)).toBe(
      JSON.stringify(buildExportRequest(input()).request),
    )
  })
})
