import { describe, expect, it } from 'vitest'
import { type CanvasObject, type FigureDocument, type PanelObject } from '@/types/document'
import { annotationsBelow, annotationsOf, singleAnnotatedDocument } from './singleExportDoc'
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

  it('面板在画布上被缩放：位置与字号 / 线宽 / 内边距 / 描边 / 圆角同比映射（与图的相对大小同画布）', () => {
    const scaled = { ...panel, w: 40, h: 30 } // 画布上缩到一半，原图 80 × 60
    const objs = [
      scaled,
      text({ x: 40, y: 30, w: 10, h: 3, padding: 1, borderColor: '#000' }),
      { id: 'a1', type: 'arrow', x: 40, y: 30, w: 10, h: 5, strokePt: 0.5 } as never,
      { id: 's1', type: 'shape', shape: 'rect', x: 40, y: 30, w: 10, h: 5, strokePt: 1, cornerRadius: 2 } as never,
    ]
    const out = singleAnnotatedDocument(docOf(objs), scaled, spec())!
    const t = out.objects[1] as CanvasObject & { sizePt: number; padding: number; borderPt: number }
    expect([t.x, t.y, t.w, t.h]).toEqual([20, 20, 20, 6])
    // 画布上 9 pt 的字对着缩到一半的图摆；原图是两倍大，字也得是两倍（缺省 0.75 pt 的描边同样跟着缩）
    expect([t.sizePt, t.padding, t.borderPt]).toEqual([18, 2, 1.5])
    expect((out.objects[2] as CanvasObject & { strokePt: number }).strokePt).toBe(1)
    const s = out.objects[3] as CanvasObject & { strokePt: number; cornerRadius: number }
    expect([s.strokePt, s.cornerRadius]).toEqual([2, 4])
  })

  it('比例为 1：字号 / 线宽原样（只平移），不凭空补 borderPt', () => {
    const out = singleAnnotatedDocument(docOf([panel, text()]), panel, spec())!
    const t = out.objects[1] as CanvasObject & { sizePt: number }
    expect(t.sizePt).toBe(9)
    expect('borderPt' in t).toBe(false)
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

  it('没有规格：不合成（没有图幅就没有页面）；占位规格照样按请求里的图幅', () => {
    expect(singleAnnotatedDocument(docOf([panel, text()]), panel, null)).toBeNull()
    const out = singleAnnotatedDocument(docOf([panel, text()]), panel, spec(10, 10, true))!
    expect([out.page.w, out.page.h]).toEqual([10, 10])
  })

  it('annotationsBelow：z 序里排在面板前面的标注（图层树里放在图之下）', () => {
    const doc = docOf([text({ id: 'u1' }), panel, text({ id: 't2' })])
    const out = singleAnnotatedDocument(doc, panel, spec())!
    expect(annotationsOf(out).map((o) => o.id)).toEqual(['u1', 't2'])
    expect(annotationsBelow(out)).toBe(1)
    expect(annotationsBelow(singleAnnotatedDocument(docOf([panel, text()]), panel, spec())!)).toBe(0)
  })

  it('annotationsOf：只取可见的非面板对象', () => {
    const out = singleAnnotatedDocument(docOf([panel, text(), text({ id: 't2', hidden: true })]), panel, spec())!
    expect(annotationsOf(out).map((o) => o.id)).toEqual(['t1'])
  })
})

describe('singleAnnotatedDocument：画布留白（ADR 0161）', () => {
  it('只有留白、没有标注也合成：页面 = 图幅 + 留白，图落在 (左, 上)', () => {
    const out = singleAnnotatedDocument(docOf([panel]), panel, spec(), { l: 5, t: 2, r: 3, b: 0 })!
    expect([out.page.w, out.page.h]).toEqual([88, 62])
    expect(out.objects.map((o) => [o.id, o.x, o.y])).toEqual([['p1', 5, 2]])
    expect(annotationsOf(out)).toEqual([])
  })
  it('标注跟着平移；负留白（紧凑 / 裁剪）页面比图幅小', () => {
    const out = singleAnnotatedDocument(docOf([panel, text()]), panel, spec(), { l: -4, t: -2, r: -1, b: 0 })!
    expect([out.page.w, out.page.h]).toEqual([75, 58])
    expect(out.objects.map((o) => [o.id, o.x, o.y])).toEqual([
      ['p1', -4, -2],
      ['t1', 6, 8],
    ])
  })
  it('全 0 留白与不给一样：没有标注时仍是 null（载荷不变）', () => {
    expect(singleAnnotatedDocument(docOf([panel]), panel, spec(), { l: 0, t: 0, r: 0, b: 0 })).toBeNull()
  })
})
