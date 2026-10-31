import { describe, expect, it } from 'vitest'
import { type CanvasObject, type FigureDocument, type PanelObject } from '@/types/document'
import { singleAnnotatedDocument } from './singleExportDoc'
import type { OriginalOutputSpec } from './originalSpec'

const panel: PanelObject = {
  id: 'p1',
  type: 'panel',
  fileId: 'Fig1.pdf',
  fileKind: 'pdf',
  nativeW: 80,
  nativeH: 60,
  overrides: [],
  x: 30,
  y: 20,
  w: 80,
  h: 60,
}
const text = (over: Partial<CanvasObject> = {}): CanvasObject =>
  ({ id: 't1', type: 'text', text: 'Label QA', sizePt: 9, x: 40, y: 30, w: 20, h: 6, ...over }) as never
const docOf = (objects: CanvasObject[]): FigureDocument => ({
  schema: 2,
  name: 't',
  page: { w: 180, h: 120 },
  objects,
  guides: [],
})
const spec = (w = 80, h = 60, fallback = false) => ({ widthMm: w, heightMm: h, fallback }) as OriginalOutputSpec

describe('singleAnnotatedDocument（QA-WIN-20）', () => {
  it('没有可见标注 / 隐藏标注 / 没有面板：null（请求保持纯原图）', () => {
    expect(singleAnnotatedDocument(docOf([panel]), panel, spec())).toBeNull()
    expect(singleAnnotatedDocument(docOf([panel, text({ hidden: true })]), panel, spec())).toBeNull()
    expect(singleAnnotatedDocument(docOf([text()]), null, spec())).toBeNull()
    expect(singleAnnotatedDocument(docOf([panel, { ...panel, id: 'p2' }, text()]), panel, spec())).toBeNull()
  })

  it('页面 = 原图图幅，面板落在 (0,0)，标注平移到图左上角为原点；不改入参', () => {
    const doc = docOf([panel, text()])
    const frozen = JSON.stringify(doc)
    const out = singleAnnotatedDocument(doc, panel, spec())!
    expect([out.page.w, out.page.h]).toEqual([80, 60])
    expect(out.objects.map((o) => [o.id, o.x, o.y, o.w, o.h])).toEqual([
      ['p1', 0, 0, 80, 60],
      ['t1', 10, 10, 20, 6],
    ])
    expect(JSON.stringify(doc)).toBe(frozen)
  })

  it('面板在画布上被缩放：位置按 图幅/画布显示尺寸 映射，字号不缩', () => {
    const scaled = { ...panel, w: 40, h: 30 } // 画布上缩到一半，原图 80 × 60
    const out = singleAnnotatedDocument(docOf([scaled, text({ x: 40, y: 30, w: 10, h: 3 })]), scaled, spec())!
    const t = out.objects[1] as CanvasObject & { sizePt: number }
    expect([t.x, t.y, t.w, t.h]).toEqual([20, 20, 20, 6])
    expect(t.sizePt).toBe(9)
  })

  it('画布上的裁剪 / 旋转 / 翻转 / 透明度不套用（与不带标注的原图导出同一张图）', () => {
    const odd = { ...panel, crop: { x: 0, y: 0, w: 1, h: 1 }, rotation: 0, opacity: 0.5, flipH: true } as PanelObject
    const out = singleAnnotatedDocument(docOf([odd, text()]), odd, spec())!
    const p = out.objects[0] as PanelObject
    expect([p.crop, p.rotation, p.opacity, p.flipH, p.flipV]).toEqual([undefined, undefined, undefined, undefined, undefined])
  })

  it('图幅外的标注保留坐标（页面就是图幅，由合成裁掉），不扩大页面', () => {
    const out = singleAnnotatedDocument(docOf([panel, text({ x: 200, y: 150 })]), panel, spec())!
    expect([out.page.w, out.page.h]).toEqual([80, 60])
    expect([out.objects[1].x, out.objects[1].y]).toEqual([170, 130])
  })

  it('规格是占位值（fallback）时按画布上图的完整显示尺寸', () => {
    const out = singleAnnotatedDocument(docOf([panel, text()]), panel, spec(10, 10, true))!
    expect([out.page.w, out.page.h]).toEqual([80, 60])
  })
})
