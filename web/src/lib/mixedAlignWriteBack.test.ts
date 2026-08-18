/**
 * 混排对齐（图内元素 + 画布标注）与「写回原图带标注」的几何换算。
 *
 * - annotationAlignEntries：标注框 → 面板内容分数框（与元素同一空间）；
 *   面板带旋转/翻转时明确不给条目（换算对不上，宁缺毋滥）
 * - applyMixedAlign：override 与对象位移进同一次 commit——一条撤销同时回滚两边
 * - collectPanelAnnotations：重叠面积最大者得，隐藏对象不进，坐标/字号按
 *   面板显示比例换算成图自身 mm
 */
import { literal } from '@/i18n'
import { beforeEach, describe, expect, it } from 'vitest'

import { annotationAlignEntries } from '@/lib/elementGeom'
import { collectPanelAnnotations, annotationsBlocked } from '@/lib/writeBackAnnotations'
import { applyMixedAlign } from '@/store/actions'
import { useDocumentStore } from '@/store/documentStore'
import { useRenderStore } from '@/store/renderStore'
import { emptyProject, type PanelObject, type TextObject } from '@/types/document'

const panel = (over: Partial<PanelObject> = {}): PanelObject => ({
  id: 'p1',
  type: 'panel',
  x: 10,
  y: 20,
  w: 100,
  h: 80,
  fileId: 'f1.pdf',
  fileKind: 'pdf',
  nativeW: 200,
  nativeH: 160,
  script: 'fig.py',
  overrides: [],
  ...over,
})

const note = (over: Partial<TextObject> = {}): TextObject => ({
  id: 't1',
  type: 'text',
  x: 35,
  y: 40,
  w: 20,
  h: 8,
  text: 'hello',
  sizePt: 9,
  bold: false,
  color: '#000000',
  align: 'left',
  ...over,
})

beforeEach(async () => {
  localStorage.clear()
  useRenderStore.setState({ render: async () => {} })
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_mixed')
})

describe('annotationAlignEntries', () => {
  it('标注框换算成面板内容分数（与元素对齐同一空间）', () => {
    const [e] = annotationAlignEntries(panel(), [note()])
    expect(e.objectId).toBe('t1')
    expect(e.box[0]).toBeCloseTo((35 - 10) / 100, 6)
    expect(e.box[1]).toBeCloseTo((40 - 20) / 80, 6)
    expect(e.box[2]).toBeCloseTo(0.2, 6)
    expect(e.resizable).toBe(false)
  })

  it('面板带旋转 / 翻转时不给条目', () => {
    expect(annotationAlignEntries(panel({ rotation: 90 }), [note()])).toEqual([])
    expect(annotationAlignEntries(panel({ flipH: true }), [note()])).toEqual([])
  })

  it('隐藏标注与面板对象不进条目', () => {
    expect(
      annotationAlignEntries(panel(), [note({ hidden: true }), panel({ id: 'p2' })]),
    ).toEqual([])
  })
})

describe('applyMixedAlign', () => {
  it('override 与对象位移同一次 commit，一条撤销同时回滚', () => {
    const store = useDocumentStore.getState()
    store.commit(literal('加'), (d) => {
      d.objects.push(panel(), note())
    })
    applyMixedAlign(
      'p1',
      literal('顶对齐'),
      [{ gid: 'axes_0', prop: 'position', value: [0.1, 0.1, 0.5, 0.5] }],
      [{ id: 't1', x: 42, y: 21 }],
    )

    const doc = () => useDocumentStore.getState().doc
    const p = doc().objects.find((o) => o.id === 'p1') as PanelObject
    const t = doc().objects.find((o) => o.id === 't1') as TextObject
    expect(p.overrides).toHaveLength(1)
    expect(t.x).toBe(42)

    expect(useDocumentStore.getState().undo()).toBe('顶对齐')
    const p2 = doc().objects.find((o) => o.id === 'p1') as PanelObject
    const t2 = doc().objects.find((o) => o.id === 't1') as TextObject
    expect(p2.overrides).toHaveLength(0)
    expect(t2.x).toBe(35)
  })
})

describe('collectPanelAnnotations', () => {
  it('坐标与字号按面板显示比例换算成图自身 mm', () => {
    const p = panel() // 显示 100×80mm，图自身 200×160mm → kx=ky=2
    const map = collectPanelAnnotations([p], [p, note()])
    const ann = map.get('p1')!
    expect(ann.objectIds).toEqual(['t1'])
    const o = ann.objects[0]
    expect(o.x_mm).toBeCloseTo((35 - 10) * 2, 6)
    expect(o.y_mm).toBeCloseTo((40 - 20) * 2, 6)
    expect(o.w_mm).toBeCloseTo(40, 6)
    expect(o.type === 'text' && o.size_pt).toBeCloseTo(18, 6)
  })

  it('压着两个面板时归重叠面积大的那个；隐藏的不进', () => {
    const a = panel({ id: 'pa', x: 0, w: 50 })
    const b = panel({ id: 'pb', x: 50, w: 100 })
    // 标注 x 45..65：与 a 重叠 5、与 b 重叠 15 → 归 b
    const n = note({ x: 45, w: 20 })
    const map = collectPanelAnnotations([a, b], [a, b, n, note({ id: 't2', hidden: true })])
    expect(map.get('pa')).toBeUndefined()
    expect(map.get('pb')!.objectIds).toEqual(['t1'])
  })

  it('旋转 / 翻转 / 位图面板整个不参与，blocked 给出原因', () => {
    const rot = panel({ rotation: 90 })
    expect(annotationsBlocked(rot)).toBeTruthy()
    expect(collectPanelAnnotations([rot], [rot, note()]).size).toBe(0)
    expect(annotationsBlocked(panel({ fileKind: 'raster' }))).toBeTruthy()
  })
})
