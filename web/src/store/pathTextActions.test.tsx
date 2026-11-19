/**
 * 路径文字（ADR 0190）的创建、起点、输入、镜像 / 缩放、导出载荷——走真的 store。字落在哪由后端算（`tests/test_path_text.py`），
 * 这里核的是前端负责的那一半：放在哪条路径上、和它成组、起点比例、文字工具点路径。
 */
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { literal } from '@/i18n'
import { startDraw } from '@/canvas/interactions'
import { visibleContentBounds } from '@/lib/exportCrop'
import { lineWidthOf, pathTextExport, toExportObjects } from '@/lib/exportPayload'
import { groupPathOf } from '@/lib/groupTree'
import { reflectObject, scaleObject } from '@/lib/transform'
import { emptyProject, type CanvasObject, type PathObject, type PathTextObject, type ShapeObject } from '@/types/document'
import { duplicateSelected } from './actions'
import { useDocumentStore } from './documentStore'
import { useInteractionStore } from './interactionStore'
import { commitPathText, createPathText, pathSourceAt, pathTextTarget, startOffsetAt } from './pathTextActions'
import { useSelectionStore } from './selectionStore'
import { useUiStore } from './uiStore'
import { mmToWorld, useViewportStore } from './viewportStore'

const objs = () => useDocumentStore.getState().doc.objects
const byId = <T extends CanvasObject>(id: string) => objs().find((o) => o.id === id) as T
const past = () => useDocumentStore.getState().past.length

const line = (id: string, over: Partial<PathObject> = {}): PathObject => ({
  id,
  type: 'path',
  x: 10,
  y: 20,
  w: 40,
  h: 0.5,
  subpaths: [{ closed: false, nodes: [{ p: [0, 0.5] }, { p: [1, 0.5] }] }],
  strokePt: 1,
  color: '#000000',
  fill: null,
  ...over,
})
const rect = (id: string, over: Partial<ShapeObject> = {}): ShapeObject => ({
  id,
  type: 'shape',
  shape: 'rect',
  x: 60,
  y: 10,
  w: 20,
  h: 10,
  strokePt: 1,
  color: '#000000',
  fill: null,
  ...over,
})
const pt = (over: Partial<PathTextObject> = {}): PathTextObject => ({
  id: 'pt',
  type: 'pathText',
  x: 10,
  y: 20,
  w: 40,
  h: 0.5,
  text: 'Label',
  sizePt: 10,
  bold: false,
  color: '#000000',
  subpaths: [{ closed: false, nodes: [{ p: [0, 0.5] }, { p: [1, 0.5] }] }],
  ...over,
})

async function seed(items: CanvasObject[]) {
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_pt_' + Math.random())
  useDocumentStore.getState().commit(literal('放'), (d) => {
    d.objects.push(...items)
  })
  useDocumentStore.setState({ past: [], future: [] })
}

beforeEach(() => {
  localStorage.clear()
  useViewportStore.setState({ zoom: 1, panX: 0, panY: 0, originX: 0, originY: 0 })
  useUiStore.setState({ tool: 'select', snapEnabled: false, elementPanelId: null, editingTextId: null })
  useSelectionStore.getState().clear()
})
afterEach(() => useInteractionStore.getState().end())

describe('放在哪条路径上', () => {
  it('离轮廓不超过容差的最上面那个；远了不算；锁定 / 隐藏 / 蒙版 / 文字不算', async () => {
    await seed([line('a'), line('b', { y: 20.1 }), rect('r'), line('locked', { y: 40, locked: true }), line('m', { y: 50, clipMask: true })])
    expect(pathSourceAt([30, 20.25], 1)?.src.id).toBe('b')
    expect(pathSourceAt([30, 24], 1)).toBeNull()
    // 矩形按「转为路径」的轮廓算：点在上边上
    const r = pathSourceAt([70, 10.2], 1)!
    expect(r.src.id).toBe('r')
    expect(pathSourceAt([70, 15], 1)).toBeNull() // 矩形里面不是轮廓
    expect(pathSourceAt([30, 40.25], 1)).toBeNull()
    expect(pathSourceAt([30, 50.25], 1)).toBeNull()
  })

  it('点在线上哪里，起点就是哪里（弧长比例）；旋转过的对象按它自己的框算', async () => {
    await seed([line('a'), line('rot', { x: 100, y: 50, w: 40, rotationDeg: 90 })])
    expect(pathSourceAt([20, 20.25], 1)!.offset).toBeCloseTo(0.25, 6)
    // 转 90°：水平线变成过中心的竖线，起点在上面
    const hit = pathSourceAt([120.1, 40.25], 1)!
    expect(hit.src.id).toBe('rot')
    expect(hit.offset).toBeCloseTo(0.25, 6)
  })
})

describe('创建', () => {
  it('复制源的第一条子路径、插在它上面、和它成一个新组（留在原来的组里）；一条历史；选中并进入输入', async () => {
    const two = line('src', {
      groupPath: ['G0', 'G1'],
      groupId: 'G1',
      subpaths: [
        { closed: false, nodes: [{ p: [0, 0.5] }, { p: [1, 0.5], in: [0.6, 0.5] }] },
        { closed: false, nodes: [{ p: [0, 0] }, { p: [1, 1] }] },
      ],
    })
    await seed([line('below'), two, line('above')])
    const id = createPathText(byId('src'), 0.3)!
    expect(past()).toBe(1)
    expect(objs().map((o) => o.id)).toEqual(['below', 'src', id, 'above'])
    const t = byId<PathTextObject>(id)
    expect(t.type).toBe('pathText')
    expect(t.subpaths).toEqual([two.subpaths[0]])
    expect(t.startOffset).toBe(0.3)
    expect(t.lineId).toBe('src')
    expect([t.x, t.y, t.w, t.h]).toEqual([two.x, two.y, two.w, two.h])
    const gp = groupPathOf(t)
    expect(gp.slice(0, 2)).toEqual(['G0', 'G1'])
    expect(gp).toHaveLength(3)
    expect(groupPathOf(byId('src'))).toEqual(gp)
    expect(useSelectionStore.getState().ids).toEqual([id])
    expect(useUiStore.getState().editingTextId).toBe(id)
    useDocumentStore.getState().undo()
    expect(objs().map((o) => o.id)).toEqual(['below', 'src', 'above'])
    expect(groupPathOf(byId('src'))).toEqual(['G0', 'G1'])
  })

  it('新建后接着打字：撤销分两步——先撤回打的字，再撤掉这段路径文字（11-19 用户定）', async () => {
    await seed([line('src')])
    const id = createPathText(byId('src'), 0)!
    const placeholder = byId<PathTextObject>(id).text
    commitPathText(id, '电流密度')
    expect(past()).toBe(2)
    expect(byId<PathTextObject>(id).text).toBe('电流密度')
    useDocumentStore.getState().undo()
    expect(byId<PathTextObject>(id).text).toBe(placeholder)
    useDocumentStore.getState().undo()
    expect(objs().some((o) => o.id === id)).toBe(false)
  })

  it('右键目标：单选一条路径 / 形状 / 箭头才有；文字、多选没有', async () => {
    await seed([line('a'), rect('r'), pt()])
    useSelectionStore.getState().set(['r'])
    expect(pathTextTarget()?.id).toBe('r')
    useSelectionStore.getState().set(['pt'])
    expect(pathTextTarget()).toBeNull()
    useSelectionStore.getState().set(['a', 'r'])
    expect(pathTextTarget()).toBeNull()
  })

  it('文字工具点在一条线上（不拖）= 路径文字；点在空白处还是普通文字', async () => {
    await seed([line('a')])
    const down = (x: number, y: number) =>
      ({ clientX: mmToWorld(x), clientY: mmToWorld(y), button: 0, stopPropagation() {} }) as unknown as React.PointerEvent
    const up = (x: number, y: number) =>
      window.dispatchEvent(new MouseEvent('pointerup', { clientX: mmToWorld(x), clientY: mmToWorld(y), bubbles: true }))
    useUiStore.getState().setTool('text')
    startDraw(down(30, 20.3), 'text')
    up(30, 20.3)
    const made = objs().at(-1) as PathTextObject
    expect(made.type).toBe('pathText')
    expect(made.startOffset).toBeCloseTo(0.5, 3)
    expect(useUiStore.getState().tool).toBe('select')
    useUiStore.getState().setTool('text')
    startDraw(down(30, 60), 'text')
    up(30, 60)
    expect(objs().at(-1)!.type).toBe('text')
  })
})

describe('复制时线的指向跟着走', () => {
  it('和线一起复制：副本指副本的线；只复制字：仍指原来的线', async () => {
    await seed([line('L', { groupId: 'G' }), pt({ lineId: 'L', groupId: 'G' })])
    useSelectionStore.getState().set(['L', 'pt'])
    duplicateSelected()
    const [, , l2, t2] = objs() as [CanvasObject, CanvasObject, CanvasObject, PathTextObject]
    expect(l2.type).toBe('path')
    expect(t2.lineId).toBe(l2.id)
    useSelectionStore.getState().set(['pt'])
    duplicateSelected()
    expect((objs().at(-1) as PathTextObject).lineId).toBe('L')
  })
})

describe('起点与输入', () => {
  it('投到路径上的比例；翻转后沿反方向量', async () => {
    expect(startOffsetAt(pt(), [20, 23])).toBeCloseTo(0.25, 6)
    expect(startOffsetAt(pt({ flip: true }), [20, 23])).toBeCloseTo(0.75, 6)
    expect(startOffsetAt(pt(), [0, 20])).toBe(0)
  })

  it('就地输入：换行并成空格；空白不落；没变不落', async () => {
    await seed([pt()])
    commitPathText('pt', '  a\n b  ')
    expect(byId<PathTextObject>('pt').text).toBe('a b')
    expect(past()).toBe(1)
    commitPathText('pt', '   ')
    commitPathText('pt', 'a b')
    expect(past()).toBe(1)
  })
})

describe('变换', () => {
  it('上下翻只翻路径；左右翻再倒过来走：起点 1 − 原起点，起止对齐对调', () => {
    const o = pt({
      startOffset: 0.2,
      subpaths: [{ closed: false, nodes: [{ p: [0, 1], out: [0.3, 0] }, { p: [1, 1], in: [0.7, 0] }] }],
      h: 10,
    })
    const v = reflectObject(o, 'v', [0, 0]) as PathTextObject
    expect(v.subpaths[0].nodes[0].p).toEqual([0, 0])
    expect(v.startOffset).toBe(0.2)
    const h = reflectObject(o, 'h', [0, 0]) as PathTextObject
    // 原终点 (1,1) 翻过来是 (0,1)，倒过来走就成了起点；手柄跟着对调
    const [n0, n1] = h.subpaths[0].nodes
    expect(n0.p).toEqual([0, 1])
    expect(n0.in).toBeUndefined()
    expect(n0.out![0]).toBeCloseTo(0.3, 12)
    expect(n1.p).toEqual([1, 1])
    expect(n1.out).toBeUndefined()
    expect(n1.in![0]).toBeCloseTo(0.7, 12)
    expect(h.startOffset).toBe(0.8)
    expect(h.textAlign).toBe('end')
    expect((reflectObject(h, 'h', [0, 0]) as PathTextObject).textAlign).toBeUndefined()
  })

  it('闭合路径左右翻保住起点那个锚点', () => {
    const nodes = [{ p: [0.5, 0] }, { p: [1, 0.5] }, { p: [0.5, 1] }, { p: [0, 0.5] }] as PathTextObject['subpaths'][0]['nodes']
    const h = reflectObject(pt({ subpaths: [{ closed: true, nodes }], h: 10 }), 'h', [0, 0]) as PathTextObject
    expect(h.subpaths[0].nodes.map((n) => n.p)).toEqual([[0.5, 0], [1, 0.5], [0.5, 1], [0, 0.5]])
  })

  it('整组缩放时字号跟着变，单个改宽不变', () => {
    expect(scaleObject(pt(), 2, 2, [0, 0], { strokes: false, text: 'size' }).sizePt).toBeCloseTo(20, 9)
    const w = scaleObject(pt(), 2, 1, [0, 0], { strokes: false, text: 'width' })
    expect(w.sizePt).toBe(10)
    expect(w.w).toBe(80)
  })
})

describe('导出范围', () => {
  it('「裁到内容」按字立在路径上的高度往外留：字高一个字号多一点，再加自动离线的保守上限（线宽 / 2 + 1.2 个字号）', () => {
    const b = visibleContentBounds([pt({ sizePt: 10 })])!
    const mm = (10 * 2.4 * 25.4) / 72
    expect(b.y).toBeCloseTo(20 - mm, 6)
    expect(b.h).toBeCloseTo(0.5 + 2 * mm, 6)
  })
})

describe('导出载荷', () => {
  it('缺省不发；路径原样；旋转、起点、对齐、翻转按需发', () => {
    expect(pathTextExport(pt())).toEqual({
      x_mm: 10,
      y_mm: 20,
      w_mm: 40,
      h_mm: 0.5,
      type: 'pathText',
      text: 'Label',
      size_pt: 10,
      bold: false,
      italic: false,
      color: '#000000',
      font_family: undefined,
      subpaths: [{ closed: false, nodes: [{ p: [0, 0.5] }, { p: [1, 0.5] }] }],
      start_offset: undefined,
      text_align: undefined,
      flip: undefined,
      baseline_offset_pt: undefined,
      line_width_pt: undefined,
      rotation_deg: undefined,
    })
    const e = pathTextExport(pt({ startOffset: 0.4, textAlign: 'center', flip: true, rotationDeg: 15, fontFamily: 'sans-serif' })) as unknown as Record<string, unknown>
    expect([e.start_offset, e.text_align, e.flip, e.rotation_deg, e.font_family]).toEqual([0.4, 'center', true, 15, 'sans-serif'])
    // 离线 0 也要发（0 = 贴线，不是「没写」）
    expect((pathTextExport(pt({ baselineOffsetPt: 0 })) as unknown as Record<string, unknown>).baseline_offset_pt).toBe(0)
  })

  it('线宽取一起成组的那条线：看得见、有描边才算；线没了 / 隐藏 / 无描边 = 0（不发）', () => {
    const t = pt({ lineId: 'L' })
    const w = (objs: CanvasObject[]) => (toExportObjects([t], objs)[0] as unknown as Record<string, unknown>).line_width_pt
    expect(w([t, line('L', { strokePt: 1.5 })])).toBe(1.5)
    expect(w([t, rect('L', { strokePt: 2 })])).toBe(2)
    expect(w([t, line('L', { strokePt: 1.5, hidden: true })])).toBeUndefined()
    expect(w([t, line('L', { strokePt: 1.5, stroke: false })])).toBeUndefined()
    expect(w([t, rect('L', { stroke: false, fill: '#ff0000' })])).toBeUndefined()
    expect(w([t])).toBeUndefined()
    expect(lineWidthOf(pt(), [line('L')])).toBe(0)
  })
})
