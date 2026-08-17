/**
 * Shift 修饰键的 Illustrator 语义 + 绘制预览与成品一致性。
 *
 * - 移动：shift 锁定到最近的 0/45/90° 方向（不再只有单轴）
 * - 缩放：shift 强制等比（角柄之外，边柄也等比）
 * - 画矩形：shift 锁成正方形（锚在起点）
 * - 画箭头 / 直线：shift 锁 15° 角（与端点拖拽同一档）
 * - draft 带真实端点：预览里是什么，松手落下来就是什么
 */
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { useDocumentStore } from '@/store/documentStore'
import { useInteractionStore } from '@/store/interactionStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { mmToWorld, useViewportStore } from '@/store/viewportStore'
import { emptyProject, type ArrowObject, type ShapeObject } from '@/types/document'
import { startDraw, startMoveDrag, startResizeDrag } from './interactions'

const down = (clientX = 0, clientY = 0) =>
  ({ clientX, clientY, button: 0, stopPropagation() {} }) as unknown as React.PointerEvent

const fire = (
  type: 'pointermove' | 'pointerup',
  clientX: number,
  clientY: number,
  init: MouseEventInit = {},
) => window.dispatchEvent(new MouseEvent(type, { clientX, clientY, bubbles: true, ...init }))

const px = (mm: number) => mmToWorld(mm)
const objects = () => useDocumentStore.getState().doc.objects

const rect40x20 = (): ShapeObject => ({
  id: 'r1',
  type: 'shape',
  shape: 'rect',
  x: 10,
  y: 10,
  w: 40,
  h: 20,
  strokePt: 1,
  color: '#111111',
  fill: null,
})

beforeEach(async () => {
  localStorage.clear()
  useViewportStore.setState({ zoom: 1, panX: 0, panY: 0, originX: 0, originY: 0 })
  useUiStore.setState({ tool: 'select', snapEnabled: false, elementPanelId: null })
  useSelectionStore.getState().clear()
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_shift')
})

afterEach(() => {
  useInteractionStore.getState().end()
})

describe('shift 移动锁方向', () => {
  it('近似水平的拖动锁成纯水平（垂直分量归零，无浮点残差）', () => {
    useDocumentStore.getState().commit('加', (d) => {
      d.objects.push(rect40x20())
    })
    useSelectionStore.getState().set(['r1'])
    startMoveDrag(down(0, 0), 'r1')
    fire('pointermove', px(20), px(2), { shiftKey: true })
    fire('pointerup', px(20), px(2), { shiftKey: true })

    const o = objects()[0]
    expect(o.y).toBe(10) // 逐位相等：锁水平后 y 不许有 1e-15 级漂移
    expect(o.x).toBeGreaterThan(29)
  })

  it('接近对角线时锁 45°（dx 与 dy 相等），不再吸到单轴', () => {
    useDocumentStore.getState().commit('加', (d) => {
      d.objects.push(rect40x20())
    })
    useSelectionStore.getState().set(['r1'])
    startMoveDrag(down(0, 0), 'r1')
    fire('pointermove', px(20), px(18), { shiftKey: true })
    fire('pointerup', px(20), px(18), { shiftKey: true })

    const o = objects()[0]
    expect(o.x - 10).toBeCloseTo(o.y - 10, 6)
    expect(o.x).toBeGreaterThan(10)
  })
})

describe('shift 缩放等比', () => {
  it('角柄 + shift：即使 alt 反转也强制等比', () => {
    useDocumentStore.getState().commit('加', (d) => {
      d.objects.push(rect40x20())
    })
    startResizeDrag(down(0, 0), 'r1', 'se')
    fire('pointermove', px(10), px(0), { shiftKey: true, altKey: true })
    fire('pointerup', px(10), px(0), { shiftKey: true, altKey: true })

    const o = objects()[0]
    expect(o.w).toBeCloseTo(50, 6)
    expect(o.h).toBeCloseTo(25, 6) // 等比：20 × 50/40
  })

  it('边柄 + shift：单轴拖动也等比缩放另一边', () => {
    useDocumentStore.getState().commit('加', (d) => {
      d.objects.push(rect40x20())
    })
    startResizeDrag(down(0, 0), 'r1', 'e')
    fire('pointermove', px(10), px(0), { shiftKey: true })
    fire('pointerup', px(10), px(0), { shiftKey: true })

    const o = objects()[0]
    expect(o.w).toBeCloseTo(50, 6)
    expect(o.h).toBeCloseTo(25, 6)
  })

  it('边柄不按 shift：保持现状，只改一边', () => {
    useDocumentStore.getState().commit('加', (d) => {
      d.objects.push(rect40x20())
    })
    startResizeDrag(down(0, 0), 'r1', 'e')
    fire('pointermove', px(10), px(0))
    fire('pointerup', px(10), px(0))

    const o = objects()[0]
    expect(o.w).toBeCloseTo(50, 6)
    expect(o.h).toBeCloseTo(20, 6)
  })
})

describe('shift 绘制约束', () => {
  it('画矩形 + shift：锁成正方形，锚在起点、跟随拖动方向', () => {
    useUiStore.setState({ tool: 'rect' })
    startDraw(down(px(10), px(10)), 'rect')
    fire('pointermove', px(40), px(25), { shiftKey: true })
    fire('pointerup', px(40), px(25), { shiftKey: true })

    const o = objects().at(-1) as ShapeObject
    expect(o.shape).toBe('rect')
    expect(o.x).toBeCloseTo(10, 6)
    expect(o.y).toBeCloseTo(10, 6)
    expect(o.w).toBeCloseTo(30, 6)
    expect(o.h).toBeCloseTo(30, 6)
  })

  it('画箭头 + shift：15° 角锁定，近水平的拖动落成纯水平箭头', () => {
    useUiStore.setState({ tool: 'arrow' })
    startDraw(down(px(10), px(10)), 'arrow')
    fire('pointermove', px(40), px(11.5), { shiftKey: true })
    fire('pointerup', px(40), px(11.5), { shiftKey: true })

    const o = objects().at(-1) as ArrowObject
    expect(o.type).toBe('arrow')
    expect(o.h).toBeCloseTo(0.01, 6) // 纯水平：包围盒钳到最小厚度
    expect(o.start.ry).toBe(o.end.ry)
  })

  it('画箭头 + shift：接近对角线锁 45°（w ≈ h）', () => {
    useUiStore.setState({ tool: 'arrow' })
    startDraw(down(px(10), px(10)), 'arrow')
    fire('pointermove', px(30), px(28), { shiftKey: true })
    fire('pointerup', px(30), px(28), { shiftKey: true })

    const o = objects().at(-1) as ArrowObject
    expect(o.w).toBeCloseTo(o.h, 6)
  })
})

describe('绘制预览与成品一致', () => {
  it('拖动中 draft 带真实端点；松手落的对象端点与 draft 逐位一致', () => {
    useUiStore.setState({ tool: 'arrow' })
    startDraw(down(px(10), px(20)), 'arrow')
    fire('pointermove', px(50), px(45))

    const draft = useInteractionStore.getState().draft
    expect(draft?.start).toEqual({ x: 10, y: 20 })
    expect(draft?.end?.x).toBeCloseTo(50, 6)
    expect(draft?.end?.y).toBeCloseTo(45, 6)

    fire('pointerup', px(50), px(45))
    const o = objects().at(-1) as ArrowObject
    const sx = o.x + o.start.rx * o.w
    const sy = o.y + o.start.ry * o.h
    const ex = o.x + o.end.rx * o.w
    const ey = o.y + o.end.ry * o.h
    expect(sx).toBeCloseTo(10, 6)
    expect(sy).toBeCloseTo(20, 6)
    expect(ex).toBeCloseTo(50, 6)
    expect(ey).toBeCloseTo(45, 6)
  })

  it('矩形 / 椭圆的 draft 不带端点（预览仍是虚线框）', () => {
    useUiStore.setState({ tool: 'rect' })
    startDraw(down(px(10), px(10)), 'rect')
    fire('pointermove', px(30), px(30))
    const draft = useInteractionStore.getState().draft
    expect(draft?.start).toBeUndefined()
    expect(draft?.end).toBeUndefined()
    fire('pointerup', px(30), px(30))
  })
})
