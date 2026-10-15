/**
 * 等比缩放吸附（ADR 0128 §三）的集成：锁比例的角柄 / ⇧ 等比，让自己的子图边靠上别的图的子图边。
 *
 * 版面：A 在 (0,0) 100×75，子图占 x∈[0.1,0.6]；B 在 (0,100) 100×75，子图占 x∈[0.2,0.7]。
 * 拖 A 的东南角柄，A 的子图右边（页面 x = 60·s）与 B 的子图右边（x = 70）对齐需要 s = 7/6。
 * 外框右边（100·s）、页面线、B 外框线都离得远，所以只有「内容锚点 ↔ 别的图子图边」能解释吸附。
 */
import { literal } from '@/i18n'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import type { Manifest, ManifestElement } from '@/lib/api'
import { useDocumentStore } from '@/store/documentStore'
import { useInteractionStore } from '@/store/interactionStore'
import { renderKeyOf, useRenderStore } from '@/store/renderStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { mmToWorld, useViewportStore } from '@/store/viewportStore'
import { emptyProject, type PanelObject, type ShapeObject } from '@/types/document'
import { startMoveDrag, startResizeDrag } from './interactions'

const axesOf = (pos: [number, number, number, number]): ManifestElement => ({
  gid: 'axes_0',
  role: 'axes',
  label: '子图 1',
  // position 是 bottom-origin；bbox 取同一个框的 top-origin
  bbox: [pos[0], 1 - pos[1] - pos[3], pos[2], pos[3]],
  editable: [{ prop: 'position', type: 'rect', value: pos }],
  draggable: false,
  resizable: true,
})

const manifestOf = (pos: [number, number, number, number]): Manifest => ({
  stem: 'Fig',
  size_mm: [100, 75],
  elements: [
    { gid: 'figure', role: 'figure', label: '整图', bbox: [0, 0, 1, 1], editable: [], draggable: false },
    axesOf(pos),
  ],
})

const panel = (id: string, y: number, extra: Partial<PanelObject> = {}): PanelObject =>
  ({
    id,
    type: 'panel',
    x: 0,
    y,
    w: 100,
    h: 75,
    fileId: `${id}.pdf`,
    fileKind: 'pdf',
    nativeW: 100,
    nativeH: 75,
    script: 'fig.py',
    overrides: [],
    ...extra,
  }) as unknown as PanelObject

const A_POS: [number, number, number, number] = [0.1, 0.2, 0.5, 0.6]
const B_POS: [number, number, number, number] = [0.2, 0.2, 0.5, 0.6]

const objs = () => useDocumentStore.getState().doc.objects
const get = (id: string) => objs().find((o) => o.id === id)!

function mount(p: PanelObject, pos: [number, number, number, number]) {
  const manifest = manifestOf(pos)
  useRenderStore.getState().patch(renderKeyOf(p), {
    fileId: p.fileId,
    manifest,
    svg: '<svg xmlns="http://www.w3.org/2000/svg"/>',
    rev: 1,
    status: 'ready',
    lastPatches: JSON.stringify(p.overrides),
  })
  useRenderStore.setState((s) => ({ latest: { ...s.latest, [p.fileId]: renderKeyOf(p) } }))
}

async function setup(aExtra: Partial<PanelObject> = {}, bExtra: Partial<PanelObject> = {}) {
  localStorage.clear()
  useViewportStore.setState({ zoom: 1, panX: 0, panY: 0, originX: 0, originY: 0, viewW: 900, viewH: 700 })
  useUiStore.setState({ tool: 'select', snapEnabled: true, snapToObjects: true, snapToGuides: true, snapToGrid: false })
  useSelectionStore.getState().clear()
  useRenderStore.getState().clear()
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_scale_snap')
  const a = panel('pa', 0, aExtra)
  const b = panel('pb', 100, bExtra)
  useDocumentStore.getState().commit(literal('加面板'), (d) => {
    d.objects.push(a, b)
  })
  mount(get('pa') as PanelObject, A_POS)
  mount(get('pb') as PanelObject, B_POS)
  useDocumentStore.setState({ past: [], future: [] })
}

const down = (clientX = 0, clientY = 0) =>
  ({ clientX, clientY, button: 0, stopPropagation() {} }) as unknown as React.PointerEvent
const fire = (
  type: 'pointermove' | 'pointerup',
  mm: [number, number],
  mods: { metaKey?: boolean; ctrlKey?: boolean; shiftKey?: boolean; altKey?: boolean } = {},
) =>
  window.dispatchEvent(
    new MouseEvent(type, { clientX: mmToWorld(mm[0]), clientY: mmToWorld(mm[1]), bubbles: true, ...mods }),
  )

/** 子图右边在页面里的 x */
const axesRight = (p: PanelObject, pos = A_POS) => p.x + (pos[0] + pos[2]) * p.w

function drag(dir: 'se' | 'e', to: [number, number], mods = {}) {
  startResizeDrag(down(0, 0), 'pa', dir)
  fire('pointermove', [to[0] / 2, to[1] / 2], mods)
  fire('pointermove', to, mods)
  const xs = [...useInteractionStore.getState().snapXs]
  fire('pointerup', to, mods)
  return xs
}

beforeEach(async () => {
  await setup()
})
afterEach(() => {
  useInteractionStore.getState().end()
})

describe('等比缩放吸附', () => {
  it('锁比例角柄：子图右边靠近 B 子图右边 → 吸上，宽高比精确不变', () => {
    // s0 = 1.16：子图右边在 69.6，离 B 子图右边(70) 0.4mm
    const xs = drag('se', [16, 0])
    const a = get('pa') as PanelObject
    expect(axesRight(a)).toBeCloseTo(70, 6)
    expect(a.w / a.h).toBeCloseTo(100 / 75, 12)
    expect(a.x).toBe(0)
    expect(a.y).toBe(0)
    expect(xs).toEqual([70]) // 只显示胜出那一对的线
  })

  it('⇧ 等比边柄：同样按子图边吸', () => {
    drag('e', [16, 0], { shiftKey: true })
    const a = get('pa') as PanelObject
    expect(axesRight(a)).toBeCloseTo(70, 6)
    expect(a.w / a.h).toBeCloseTo(100 / 75, 12)
  })

  it('⌘ / Ctrl：不吸，落在手指停的地方', () => {
    drag('se', [16, 0], { metaKey: true })
    expect((get('pa') as PanelObject).w).toBeCloseTo(116, 6)
    drag('se', [16, 0], { ctrlKey: true })
  })

  it('Alt 反转成非等比的角柄：仍走原来的只吸动边，不套等比', () => {
    drag('se', [16, 0], { altKey: true })
    const a = get('pa') as PanelObject
    expect(a.w).toBeCloseTo(116, 6)
    expect(a.h).toBeCloseTo(75, 6)
  })

  it('⌘ 那帧清掉保持：松开 ⌘ 后离旧吸点 2mm（> 容差、< 2×容差）不再被拽回去', () => {
    startResizeDrag(down(0, 0), 'pa', 'se')
    fire('pointermove', [16, 0])
    expect((get('pa') as PanelObject).w).toBeCloseTo(100 * (70 / 60), 6) // 吸上
    fire('pointermove', [18, 0], { metaKey: true })
    fire('pointermove', [20, 0]) // 子图右边 72：旧吸点 70 在 2mm 外
    expect((get('pa') as PanelObject).w).toBeCloseTo(120, 6)
    fire('pointerup', [20, 0])
  })

  it('容差外不吸', () => {
    drag('se', [10, 0]) // s0 = 1.10：子图右边 66，离 70 有 4mm
    expect((get('pa') as PanelObject).w).toBeCloseTo(110, 6)
  })

  it('面板旋转 90°：不取子图锚点（分数坐标与页面轴不再平行）——只剩外框锚点，不吸到 B 的子图边', async () => {
    await setup({ rotation: 90 })
    drag('se', [16, 0])
    // 旋转面板 w=100,h=75 仍是包围盒：外框右边 116 离一切候选都远
    expect((get('pa') as PanelObject).w).toBeCloseTo(116, 6)
  })

  it('旋转过的形状：不吸（候选线是页面轴的）', async () => {
    useDocumentStore.getState().commit(literal('加形状'), (d) => {
      const s: ShapeObject = {
        id: 'sh', type: 'shape', shape: 'rect', x: 10, y: 10, w: 40, h: 20,
        strokePt: 1, color: '#111111', fill: null, rotationDeg: 30,
      }
      d.objects.push(s)
      d.guides.push({ id: 'g', axis: 'x', pos: 70 } as never)
    })
    // 局部系里 dx′ = 20.4：未旋转的话右边正好 70.4，会被参考线吸到 70
    const c = Math.cos(Math.PI / 6)
    const sn = Math.sin(Math.PI / 6)
    const to: [number, number] = [20.4 * c, 20.4 * sn]
    startResizeDrag(down(0, 0), 'sh', 'se')
    fire('pointermove', to, { shiftKey: true })
    expect(useInteractionStore.getState().snapXs).toEqual([])
    fire('pointerup', to, { shiftKey: true })
    expect(get('sh').w).toBeCloseTo(60.4, 6)
  })

  it('外框右边吸（形状等比）：外框锚点 ↔ 参考线', () => {
    useDocumentStore.getState().commit(literal('加形状'), (d) => {
      d.objects.push({
        id: 'sh', type: 'shape', shape: 'rect', x: 10, y: 10, w: 40, h: 20,
        strokePt: 1, color: '#111111', fill: null,
      } as ShapeObject)
      d.guides.push({ id: 'g', axis: 'x', pos: 70 } as never)
    })
    startResizeDrag(down(0, 0), 'sh', 'se')
    fire('pointermove', [20.4, 0], { shiftKey: true }) // 右边 70.4
    fire('pointerup', [20.4, 0], { shiftKey: true })
    const sh = get('sh')
    expect(sh.x + sh.w).toBeCloseTo(70, 6)
    expect(sh.h / sh.w).toBeCloseTo(0.5, 12)
  })

  it('吸住后保持：同一手势里先吸上，再多拖 1.5×容差内仍停在吸点；拖远则放开', () => {
    startResizeDrag(down(0, 0), 'pa', 'se')
    fire('pointermove', [16, 0])
    expect((get('pa') as PanelObject).w).toBeCloseTo(100 * (70 / 60), 6)
    fire('pointermove', [17.5, 0]) // 子图右边 70.5（0.5mm 外，超过「新挑」的 0.4 但仍 < 2×容差）
    expect((get('pa') as PanelObject).w).toBeCloseTo(100 * (70 / 60), 6)
    fire('pointermove', [40, 0])
    expect((get('pa') as PanelObject).w).toBeCloseTo(140, 6)
    fire('pointerup', [40, 0])
  })
})

describe('「吸附到对象」关掉（Codex #1033 r4268157398）', () => {
  const addGuide = (pos: number) =>
    useDocumentStore.getState().commit(literal('加参考线'), (d) => {
      d.guides.push({ id: 'g', axis: 'x', pos } as never)
    })

  it('自身子图锚点不吸：参考线在 70，子图右边 69.6 也不被拽过去（开着时会吸）', () => {
    useUiStore.setState({ snapToObjects: false })
    addGuide(70)
    drag('se', [16, 0])
    expect((get('pa') as PanelObject).w).toBeCloseTo(116, 6)
  })

  it('外框↔参考线仍吸（页面线 / 参考线 / 网格不归「吸附到对象」）', () => {
    useUiStore.setState({ snapToObjects: false })
    addGuide(110)
    drag('se', [10.3, 0])
    expect((get('pa') as PanelObject).w).toBeCloseTo(110, 6)
  })

  it('别的对象外框不吸：B 外框右边 100，A 拖到 100.6 停在手指处；开着时吸到 100', () => {
    useUiStore.setState({ snapToObjects: false })
    drag('se', [0.6, 0])
    expect((get('pa') as PanelObject).w).toBeCloseTo(100.6, 6)
    useUiStore.setState({ snapToObjects: true })
    drag('se', [0.6, 0])
    expect((get('pa') as PanelObject).w).toBeCloseTo(100, 6)
  })
})

describe('平移候选不变', () => {
  it('拖整张 A：别的图的子图边不会出现在平移候选里', () => {
    // A 外框右边 100 → 拖 −19.5 后在 80.5；B 的子图右边在 70、外框线在 0 / 50 / 100，都不在 1.6mm 内
    // 注意 A 中线 50−19.5 = 30.5 也远离一切。若子图边混进平移候选，也不会在此处触发，
    // 所以另取一组：把 A 的右边拖到 B 子图右边(70)附近，必须仍是手指停的地方
    useSelectionStore.getState().set(['pa'])
    startMoveDrag(down(0, 0), 'pa')
    fire('pointermove', [-29.6, 0]) // A 外框右边 70.4 = 离 B 子图右边 0.4mm
    const xs = [...useInteractionStore.getState().snapXs]
    fire('pointerup', [-29.6, 0])
    expect(get('pa').x).toBeCloseTo(-29.6, 9)
    expect(xs).toEqual([])
  })
})

describe('吸附反馈（ADR 0128 §四）', () => {
  const fbOf = () => useInteractionStore.getState().snapFeedback
  const rectClose = (r: { x: number; y: number; w: number; h: number }, e: [number, number, number, number]) =>
    [r.x, r.y, r.w, r.h].forEach((v, i) => expect(v).toBeCloseTo(e[i], 6))

  it('子图锚点靠上别的图的子图边：self 是缩放后的子图框（不是外框），target 是对方子图框', () => {
    startResizeDrag(down(0, 0), 'pa', 'se')
    fire('pointermove', [16, 0])
    const fb = fbOf()
    fire('pointerup', [16, 0])
    expect(fb).toHaveLength(1)
    expect(fb[0].axis).toBe('x')
    expect(fb[0].v).toBe(70)
    const s = 70 / 60
    // A 子图：x 10..60、y 15..60（页面 mm），绕左上角 (0,0) 放大 s
    rectClose(fb[0].self, [10 * s, 15 * s, 50 * s, 45 * s])
    // B 子图：x 20..70、y 100+15..100+60
    rectClose(fb[0].target!, [20, 115, 50, 45])
  })

  it('外框锚点靠上参考线：self 是吸附后的外框，没有 target', () => {
    useDocumentStore.getState().commit(literal('加形状'), (d) => {
      d.objects.push({
        id: 'sh', type: 'shape', shape: 'rect', x: 10, y: 10, w: 40, h: 20,
        strokePt: 1, color: '#111111', fill: null,
      } as ShapeObject)
      d.guides.push({ id: 'g', axis: 'x', pos: 70 } as never)
    })
    startResizeDrag(down(0, 0), 'sh', 'se')
    fire('pointermove', [20.4, 0], { shiftKey: true })
    const fb = fbOf()
    fire('pointerup', [20.4, 0], { shiftKey: true })
    expect(fb).toHaveLength(1)
    rectClose(fb[0].self, [10, 10, 60, 30])
    expect(fb[0].target).toBeUndefined()
  })

  it('松手后反馈消失；⌘ 时没有反馈', () => {
    startResizeDrag(down(0, 0), 'pa', 'se')
    fire('pointermove', [16, 0])
    expect(fbOf()).toHaveLength(1)
    fire('pointermove', [16, 0], { metaKey: true })
    expect(fbOf()).toHaveLength(0)
    fire('pointermove', [16, 0])
    fire('pointerup', [16, 0])
    expect(fbOf()).toHaveLength(0)
  })

  it('画布对象平移吸附：有吸附线，没有反馈', () => {
    useDocumentStore.getState().commit(literal('加形状'), (d) => {
      d.objects.push({
        id: 'sh', type: 'shape', shape: 'rect', x: 10, y: 10, w: 40, h: 20,
        strokePt: 1, color: '#111111', fill: null,
      } as ShapeObject)
      d.guides.push({ id: 'g', axis: 'x', pos: 80 } as never)
    })
    useSelectionStore.getState().set(['sh'])
    startMoveDrag(down(0, 0), 'sh')
    fire('pointermove', [15, 0])
    fire('pointermove', [30.2, 0]) // 右边 80.2 → 吸参考线 80
    expect(useInteractionStore.getState().snapXs).toEqual([80])
    expect(fbOf()).toHaveLength(0)
    fire('pointerup', [30.2, 0])
  })
})
