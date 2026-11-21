/**
 * 三角形 / 菱形 / 多边形 / 大括号是拖画工具（2026-11-14 起，与矩形走同一条 `startDraw`）：拖出来的框就是形状的框；
 * 单击不拖 = 落一个默认尺寸（与原来「插入形状」相同：20×16，大括号 6×24）；⇧ 锁成正方形；多边形默认六边。
 */
import { act } from 'react'
import { beforeEach, describe, expect, it } from 'vitest'
import { useDocumentStore } from '@/store/documentStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { mmToWorld, useViewportStore } from '@/store/viewportStore'
import { emptyProject, type ShapeObject } from '@/types/document'
import { startDraw, type DrawTool } from './interactions'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const px = (mm: number) => mmToWorld(mm)
const down = (x: number, y: number) =>
  ({ clientX: px(x), clientY: px(y), button: 0, stopPropagation() {} }) as unknown as React.PointerEvent
const fire = (type: 'pointermove' | 'pointerup', x: number, y: number, init: MouseEventInit = {}) =>
  window.dispatchEvent(new MouseEvent(type, { clientX: px(x), clientY: px(y), bubbles: true, ...init }))
const last = () => useDocumentStore.getState().doc.objects.at(-1) as ShapeObject

beforeEach(async () => {
  useViewportStore.setState({ zoom: 1, panX: 0, panY: 0, originX: 0, originY: 0 })
  useUiStore.setState({ tool: 'select', snapEnabled: false, elementPanelId: null })
  useSelectionStore.getState().clear()
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_shape_tools_' + Math.random())
  useDocumentStore.setState({ past: [], future: [] })
})

const draw = (tool: DrawTool, to: [number, number] | null, init: MouseEventInit = {}) =>
  act(() => {
    startDraw(down(10, 20), tool)
    if (to) fire('pointermove', to[0], to[1], init)
    fire('pointerup', to?.[0] ?? 10, to?.[1] ?? 20, init)
  })

describe('三角形 / 菱形 / 多边形 / 大括号：拖画', () => {
  it.each(['triangle', 'diamond', 'polygon', 'brace', 'star', 'arc'] as const)('%s：拖出来的框就是形状的框，一条历史，选中它', (tool) => {
    draw(tool, [40, 35])
    const o = last()
    expect(o).toMatchObject({ type: 'shape', shape: tool, x: 10, y: 20, w: 30, h: 15 })
    expect(useDocumentStore.getState().past).toHaveLength(1)
    expect(useSelectionStore.getState().ids).toEqual([o.id])
    expect(useUiStore.getState().tool).toBe('select')
  })

  it.each([
    ['triangle', 20, 16],
    ['diamond', 20, 16],
    ['polygon', 20, 16],
    ['brace', 6, 24],
    ['star', 20, 20],
    ['arc', 20, 16],
  ] as const)('%s：单击不拖 = 默认尺寸', (tool, w, h) => {
    draw(tool, null)
    expect(last()).toMatchObject({ shape: tool, x: 10, y: 20, w, h })
  })

  it('多边形默认六边（与原来「插入形状」相同）；星形默认五角、内径比不写（用缺省）', () => {
    draw('polygon', [40, 40])
    expect(last().sides).toBe(6)
    draw('star', [40, 40])
    expect(last().sides).toBe(5)
    expect('starRatio' in last()).toBe(false)
  })

  it('⇧：锁成正方形（与矩形 / 椭圆同一条）', () => {
    draw('diamond', [40, 30], { shiftKey: true })
    expect(last().w).toBeCloseTo(last().h, 9)
  })
})
