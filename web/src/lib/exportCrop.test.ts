/** 单图退路导出按可见对象并集包围盒裁（只改请求，不改文档、不改协议） */
import { describe, expect, it } from 'vitest'
import { cropToVisibleContent, visibleContentBounds } from './exportCrop'
import { buildExportRequest, type ExportRequestInput } from './exportRequest'
import type { CanvasObject, FigureDocument, PanelObject, TextObject } from '@/types/document'
import { emptyDocument } from '@/types/document'

const panel = (over: Partial<PanelObject> = {}): PanelObject => ({
  id: 'p1', type: 'panel', fileId: 'runtime:a', fileKind: 'pdf', nativeW: 80, nativeH: 60,
  overrides: [], x: 20, y: 30, w: 80, h: 60, ...over,
})
const text = (over: Partial<TextObject> = {}): TextObject =>
  ({ id: 't1', type: 'text', text: 'a', x: 10, y: 5, w: 20, h: 8, sizePt: 8, bold: false, color: '#000', align: 'left', ...over }) as TextObject

function docOf(objects: CanvasObject[]): FigureDocument {
  const d = emptyDocument()
  return { ...d, page: { ...d.page, w: 180, h: 120 }, objects }
}
function req(doc: FigureDocument, over: Partial<ExportRequestInput> = {}) {
  return buildExportRequest({
    scope: 'canvas', formats: ['pdf'], filename: 'f', ppi: 600, documentId: 'd', doc, ...over,
  }).request
}

describe('visibleContentBounds', () => {
  it('隐藏对象不计入', () => {
    const b = visibleContentBounds([panel(), text({ hidden: true, x: 0, y: 0 })])
    expect(b).toEqual({ x: 20, y: 30, w: 80, h: 60 })
  })
  it('带标注时包围盒包含标注', () => {
    const b = visibleContentBounds([panel(), text()])
    expect(b).toEqual({ x: 10, y: 5, w: 90, h: 85 })
  })
  it('旋转对象取外接框', () => {
    const b = visibleContentBounds([text({ x: 0, y: 0, w: 10, h: 2, rotationDeg: 90 })])!
    expect(b.w).toBeCloseTo(2)
    expect(b.h).toBeCloseTo(10)
  })
  it('没有可见对象 -> null', () => {
    expect(visibleContentBounds([panel({ hidden: true })])).toBeNull()
  })
})

describe('buildExportRequest cropToContent', () => {
  it('页面尺寸 = 包围盒，对象平移 -x/-y', () => {
    const doc = docOf([panel(), text()])
    const r = req(doc, { cropToContent: true })
    expect(r.canvas!.page_w_mm).toBe(90)
    expect(r.canvas!.page_h_mm).toBe(85)
    const [p, t] = r.canvas!.objects
    expect([p.x_mm, p.y_mm]).toEqual([10, 25])
    expect([t.x_mm, t.y_mm]).toEqual([0, 0])
  })
  it('不改文档', () => {
    const doc = docOf([panel()])
    req(doc, { cropToContent: true })
    expect(doc.page.w).toBe(180)
    expect(doc.objects[0].x).toBe(20)
  })
  it('隐藏对象不进请求也不撑大区域', () => {
    const r = req(docOf([panel(), text({ hidden: true, x: 0, y: 0 })]), { cropToContent: true })
    expect(r.canvas!.page_w_mm).toBe(80)
    expect(r.canvas!.objects).toHaveLength(1)
  })
  it('多图整版（不传 cropToContent）保持整页与原坐标', () => {
    const r = req(docOf([panel(), panel({ id: 'p2', x: 100 })]))
    expect(r.canvas!.page_w_mm).toBe(180)
    expect(r.canvas!.page_h_mm).toBe(120)
    expect(r.canvas!.objects[0].x_mm).toBe(20)
  })
  it('没有可见对象时不裁', () => {
    const c = cropToVisibleContent({ w: 180, h: 120 }, [panel({ hidden: true })])
    expect(c.page).toEqual({ w: 180, h: 120 })
  })
})
