/**
 * 文字标注的旋转柄（ADR 0163）：柄画在选框顶边中点外并随对象转；拖它改 rotationDeg，
 * 角度 = 起手角 + 指针方位增量（按下那一刻不跳）、⇧ 吸 15°、落盘规范到 (-180, 180]、
 * 回到 0 删字段、x/y/w/h 一个字不动、一次手势一条历史。
 */
import { literal } from '@/i18n'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { normalizeRotationDeg, pointerBearingDeg, snapRotationDeg } from '@/lib/geometry'
import { useDocumentStore } from '@/store/documentStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { mmToWorld, useViewportStore } from '@/store/viewportStore'
import { emptyProject, type CanvasObject, type ShapeObject, type TextObject } from '@/types/document'
import { OverlaySvg } from './OverlaySvg'
import { startRotateDrag } from './rotateDrag'

const text = (over: Partial<TextObject> = {}): TextObject => ({
  id: 't1',
  type: 'text',
  x: 20,
  y: 20,
  w: 40,
  h: 10,
  text: 'Hello',
  sizePt: 12,
  bold: false,
  color: '#000000',
  align: 'left',
  ...over,
})

const CENTER = { x: 40, y: 25 }
const px = (mm: number) => mmToWorld(mm)

/** 方位角（正上方 0、顺时针）上、离中心 r mm 的点 → client px */
const at = (deg: number, r = 30) => {
  const a = (deg * Math.PI) / 180
  return { x: px(CENTER.x + r * Math.sin(a)), y: px(CENTER.y - r * Math.cos(a)) }
}

const down = (p: { x: number; y: number }) =>
  ({ clientX: p.x, clientY: p.y, button: 0, stopPropagation() {} }) as unknown as React.PointerEvent
const fire = (type: 'pointermove' | 'pointerup', p: { x: number; y: number }, shiftKey = false) =>
  window.dispatchEvent(new MouseEvent(type, { clientX: p.x, clientY: p.y, shiftKey, bubbles: true }))

const byId = (id: string) => useDocumentStore.getState().doc.objects.find((o) => o.id === id)!

let container: HTMLDivElement
let root: Root

beforeEach(async () => {
  localStorage.clear()
  useViewportStore.setState({ zoom: 1, panX: 0, panY: 0, originX: 0, originY: 0, viewW: 900, viewH: 700 })
  useUiStore.setState({ tool: 'select', snapEnabled: false, elementPanelId: null, cropTargetId: null })
  useSelectionStore.getState().clear()
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_rotknob')
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
})
afterEach(() => {
  act(() => root.unmount())
  container.remove()
})
declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

function show(obj: CanvasObject) {
  useDocumentStore.getState().commit(literal('加对象'), (d) => {
    d.objects.push(obj)
  })
  useSelectionStore.getState().set([obj.id])
  act(() => root.render(<OverlaySvg />))
}
const knob = () => container.querySelector('[data-rotate-handle]')

describe('角度换算', () => {
  it('normalizeRotationDeg 规范到 (-180, 180]，0 给 0', () => {
    expect(normalizeRotationDeg(270)).toBe(-90)
    expect(normalizeRotationDeg(180)).toBe(180)
    expect(normalizeRotationDeg(-180)).toBe(180)
    expect(normalizeRotationDeg(360)).toBe(0)
    expect(normalizeRotationDeg(-360)).toBe(0)
    expect(normalizeRotationDeg(30.04)).toBe(30)
    expect(normalizeRotationDeg(359.96)).toBe(0)
    expect(normalizeRotationDeg(725)).toBe(5)
    expect(normalizeRotationDeg(Number.NaN)).toBe(0)
  })

  it('方位角：正上 0、正右 90、正下 180、正左 -90（顺时针为正）', () => {
    expect(pointerBearingDeg(0, 0, 0, -5)).toBeCloseTo(0, 9)
    expect(pointerBearingDeg(0, 0, 5, 0)).toBeCloseTo(90, 9)
    expect(Math.abs(pointerBearingDeg(0, 0, 0, 5))).toBeCloseTo(180, 9)
    expect(pointerBearingDeg(0, 0, -5, 0)).toBeCloseTo(-90, 9)
  })

  it('snapRotationDeg 每 15° 一档', () => {
    expect(snapRotationDeg(33)).toBe(30)
    expect(snapRotationDeg(38)).toBe(45)
  })
})

describe('旋转柄的显示', () => {
  it('选中未锁定的文字有柄，位置在顶边中点外；转 90° 后整组跟着转', () => {
    show(text())
    const k = knob()!
    expect(k).toBeTruthy()
    // 未旋转：柄在选框顶边（y=20mm）上方 22px，x 在中线
    expect(Number(k.getAttribute('cx'))).toBeCloseTo(px(40), 6)
    expect(Number(k.getAttribute('cy'))).toBeCloseTo(px(20) - 22, 6)
    expect(k.closest('g[transform]')).toBeNull()
  })

  it('旋转后柄所在的 <g> 绕包围盒中心转同一个角', () => {
    show(text({ rotationDeg: 90 }))
    const tr = knob()!.closest('g[transform]')!.getAttribute('transform')!
    expect(tr).toBe(`rotate(90 ${px(40)} ${px(25)})`)
  })

  it('锁定的文字、形状、没选中都没有柄', () => {
    show(text({ locked: true }))
    expect(knob()).toBeNull()
    useDocumentStore.getState().commit(literal('x'), (d) => {
      d.objects = []
    })
    const rect: ShapeObject = {
      id: 's1', type: 'shape', shape: 'rect', x: 0, y: 0, w: 10, h: 10, strokePt: 1, color: '#111', fill: null,
    }
    show(rect)
    expect(knob()).toBeNull()
  })
})

describe('拖旋转柄', () => {
  it('从正上方拖到正右方 = 90°，x/y/w/h 不变，一次手势一条历史', () => {
    show(text())
    const before = useDocumentStore.getState().doc.objects.length
    const t0 = { ...byId('t1') }
    act(() => startRotateDrag(down(at(0)), 't1'))
    act(() => fire('pointermove', at(90)))
    expect(byId('t1').rotationDeg).toBeCloseTo(90, 6)
    act(() => fire('pointerup', at(90)))
    const o = byId('t1') as TextObject
    expect(o.rotationDeg).toBe(90)
    expect([o.x, o.y, o.w, o.h]).toEqual([t0.x, t0.y, t0.w, t0.h])
    expect(useDocumentStore.getState().doc.objects.length).toBe(before)
    act(() => {
      useDocumentStore.getState().undo()
    })
    expect(byId('t1').rotationDeg).toBeUndefined()
  })

  it('按下的位置不在正上方时不跳：只加指针转过的增量', () => {
    show(text({ rotationDeg: 20 }))
    act(() => startRotateDrag(down(at(50)), 't1')) // 柄此刻在方位 20°，用户抓在 50° 的位置
    act(() => fire('pointermove', at(80)))
    act(() => fire('pointerup', at(80)))
    expect(byId('t1').rotationDeg).toBeCloseTo(50, 6) // 20 + (80 − 50)
  })

  it('⇧ 吸 15°，不按 ⇧ 吸附关闭', () => {
    show(text())
    act(() => startRotateDrag(down(at(0)), 't1'))
    act(() => fire('pointermove', at(33), true))
    expect(byId('t1').rotationDeg).toBe(30)
    act(() => fire('pointermove', at(33), false))
    expect(byId('t1').rotationDeg).toBeCloseTo(33, 6)
    act(() => fire('pointerup', at(33)))
  })

  it('转过 180° 落盘规范到 (-180, 180]：拖到正左方存 -90 而不是 270', () => {
    show(text())
    act(() => startRotateDrag(down(at(0)), 't1'))
    act(() => fire('pointermove', at(-90)))
    act(() => fire('pointerup', at(-90)))
    expect(byId('t1').rotationDeg).toBe(-90)
  })

  it('回到 0 删字段（不留 rotationDeg: 0）', () => {
    show(text({ rotationDeg: 30 }))
    act(() => startRotateDrag(down(at(30)), 't1'))
    act(() => fire('pointermove', at(0)))
    act(() => fire('pointerup', at(0)))
    expect('rotationDeg' in byId('t1')).toBe(false)
  })

  it('锁定的文字不响应', () => {
    show(text({ locked: true }))
    act(() => startRotateDrag(down(at(0)), 't1'))
    act(() => fire('pointermove', at(90)))
    act(() => fire('pointerup', at(90)))
    expect(byId('t1').rotationDeg).toBeUndefined()
  })
})

describe('旋转后的对齐 / 吸附 / 选区框用外接框', () => {
  it('转 90° 的长条文字左对齐：可见左边贴到参照线，x/y/w/h 只平移', async () => {
    const { alignOneVisual, visualBounds } = await import('@/lib/geometry')
    // 40×10 的框，中心 (40,25)，转 90° 后可见框 10×40：x 35..45、y 5..45
    const o = text({ rotationDeg: 90 })
    const v0 = visualBounds(o)
    expect([v0.x, v0.y, v0.w, v0.h].map((n) => Math.round(n * 1e6) / 1e6)).toEqual([35, 5, 10, 40])
    alignOneVisual(o, 'left', { x: 0, y: 0, w: 100, h: 100 })
    expect(visualBounds(o).x).toBeCloseTo(0, 9)
    expect([o.w, o.h]).toEqual([40, 10])
    alignOneVisual(o, 'right', { x: 0, y: 0, w: 100, h: 100 })
    expect(visualBounds(o).x + visualBounds(o).w).toBeCloseTo(100, 9)
    alignOneVisual(o, 'vcenter', { x: 0, y: 0, w: 100, h: 100 })
    expect(visualBounds(o).y + visualBounds(o).h / 2).toBeCloseTo(50, 9)
  })

  it('吸附候选取别的对象的外接框；applyAlign 分布用外接框', async () => {
    const { snapCandidates, applyAlign, visualBounds } = await import('@/lib/geometry')
    const rot = text({ id: 'r', rotationDeg: 90 })
    const c = snapCandidates([rot], new Set(), { w: 200, h: 200 })
    expect(c.xs.some((x) => Math.abs(x - 35) < 1e-9)).toBe(true)
    expect(c.xs.some((x) => Math.abs(x - 45) < 1e-9)).toBe(true)
    expect(c.xs).not.toContain(20) // 未旋转盒的左边不是屏幕上看得见的边
    const a = text({ id: 'a', x: 0, y: 0, w: 10, h: 10 })
    const b = text({ id: 'b', x: 100, y: 0, w: 10, h: 10 })
    const m = text({ id: 'm', x: 30, y: 0, w: 40, h: 10, rotationDeg: 90 })
    applyAlign([a, m, b], 'hdist', { w: 200, h: 200 })
    const [va, vm, vb] = [a, m, b].map(visualBounds)
    expect(vm.x - (va.x + va.w)).toBeCloseTo(vb.x - (vm.x + vm.w), 9)
  })
})

describe('复制 / 副本 / 撤销重做保留旋转角', () => {
  it('⌘D 副本带着同一个 rotationDeg；撤销重做回到同一个值', async () => {
    const { duplicateSelected } = await import('@/store/actions')
    show(text({ rotationDeg: -30 }))
    act(() => duplicateSelected())
    const objs = useDocumentStore.getState().doc.objects
    expect(objs).toHaveLength(2)
    expect(objs.map((o) => (o as TextObject).rotationDeg)).toEqual([-30, -30])
    act(() => {
      useDocumentStore.getState().undo()
    })
    expect(useDocumentStore.getState().doc.objects).toHaveLength(1)
    act(() => {
      useDocumentStore.getState().redo()
    })
    expect((useDocumentStore.getState().doc.objects[1] as TextObject).rotationDeg).toBe(-30)
  })
})
