/** 单图退路导出按可见对象并集包围盒裁（只改请求，不改文档、不改协议） */
import { describe, expect, it } from 'vitest'
import { cropToVisibleContent, croppedDocument, visibleContentBounds } from './exportCrop'
import { buildExportRequest, type ExportRequestInput } from './exportRequest'
import type { ArrowObject, CanvasObject, FigureDocument, PanelObject, TextObject } from '@/types/document'
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
function req(doc: FigureDocument, over: { crop?: boolean } = {}) {
  return buildExportRequest({
    scope: 'canvas', formats: ['pdf'], filename: 'f', ppi: 600, documentId: 'd',
    doc: over.crop ? croppedDocument(doc) : doc,
  } as ExportRequestInput).request
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

describe('buildExportRequest 吃裁后的文档', () => {
  it('页面尺寸 = 包围盒，对象平移 -x/-y', () => {
    const doc = docOf([panel(), text()])
    const r = req(doc, { crop: true })
    expect(r.canvas!.page_w_mm).toBe(90)
    expect(r.canvas!.page_h_mm).toBe(85)
    const [p, t] = r.canvas!.objects
    expect([p.x_mm, p.y_mm]).toEqual([10, 25])
    expect([t.x_mm, t.y_mm]).toEqual([0, 0])
  })
  it('不改文档', () => {
    const doc = docOf([panel()])
    req(doc, { crop: true })
    expect(doc.page.w).toBe(180)
    expect(doc.objects[0].x).toBe(20)
  })
  it('隐藏对象不进请求也不撑大区域', () => {
    const r = req(docOf([panel(), text({ hidden: true, x: 0, y: 0 })]), { crop: true })
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

const arrow = (over: Partial<ArrowObject> = {}): ArrowObject =>
  ({ id: 'a1', type: 'arrow', x: 10, y: 10, w: 40, h: 0, start: { rx: 0, ry: 0.5 }, end: { rx: 1, ry: 0.5 },
    strokePt: 2, color: '#000', head: 'none', ...over }) as ArrowObject
const MM = 25.4 / 72

describe('描边 / 箭头外扩（与 RenderCore 绘制同源）', () => {
  it('水平细线：上下各外扩半个线宽', () => {
    const b = visibleContentBounds([
      { id: 'l', type: 'shape', shape: 'line', x: 10, y: 10, w: 40, h: 0, strokePt: 1, color: '#000', fill: null } as CanvasObject,
    ])!
    expect(b.h).toBeCloseTo(1 * MM)
    expect(b.y).toBeCloseTo(10 - 0.5 * MM)
    expect(b.w).toBeCloseTo(40 + 1 * MM)
  })
  it('带边框文字：外扩半个 border_pt（缺省 0.75）', () => {
    const b = visibleContentBounds([text({ borderColor: '#000' })])!
    expect(b.x).toBeCloseTo(10 - 0.375 * MM)
    expect(b.w).toBeCloseTo(20 + 0.75 * MM)
    expect(visibleContentBounds([text()])!.w).toBeCloseTo(20)
  })
  it('箭头：有端型外扩 2.2×线宽，无端型 0.5×', () => {
    const withHead = visibleContentBounds([arrow({ head: 'end' })])!
    expect(withHead.h).toBeCloseTo(2 * 2.2 * 2 * MM)
    const plain = visibleContentBounds([arrow()])!
    expect(plain.h).toBeCloseTo(2 * MM)
    const newField = visibleContentBounds([arrow({ headEnd: 'open' })])!
    expect(newField.h).toBeCloseTo(2 * 2.2 * 2 * MM)
  })
  it('箭头在裁切边上：整体平移后描边仍在 [0, 页面] 内', () => {
    const doc = docOf([arrow({ head: 'end' })])
    const c = croppedDocument(doc)
    const m = 2.2 * 2 * MM
    expect(c.objects[0].x).toBeCloseTo(m)
    expect(c.page.w).toBeCloseTo(40 + 2 * m)
  })
})

describe('croppedDocument', () => {
  it('页面与对象都是裁后值，原文档不变', () => {
    const doc = docOf([panel(), text()])
    const c = croppedDocument(doc)
    expect([c.page.w, c.page.h]).toEqual([90, 85])
    expect(c.objects[0].x).toBe(10)
    expect(doc.page.w).toBe(180)
  })
})
