/**
 * 单图编辑里拖这张图的四条边改图幅（面板 w / h）。
 *
 * 判据打在看得见的差别上：图的框、对侧边在屏幕上的位置、撤销条数、标注是否出现、一次拖动只提交一个事务。
 * 数学（吸附 / 夹取 / 对侧锚定）在 `lib/pageEdgeResize.test.ts`。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { TooltipProvider } from '@/components/ui/Tooltip'
import { literal } from '@/i18n'
import { cancelActivePointerGesture } from '@/store/gestureCoordinator'
import { useDocumentStore } from '@/store/documentStore'
import { useInteractionStore } from '@/store/interactionStore'
import { useUiStore } from '@/store/uiStore'
import { getTransform, mmToWorld, useViewportStore } from '@/store/viewportStore'
import { useWorkspaceStore } from '@/store/workspace'
import { settleMode } from '@/store/workspaceStore'
import { emptyProject, type PanelObject } from '@/types/document'
import { CanvasStage } from './CanvasStage'
import { startFigureEdgeResize } from './figureEdgeDrag'

/** 会滚的数字换成探针：只关心传下去的 duration（0 = 即时换，undefined = 默认时长会滚） */
vi.mock('@sfinterface/numbers', () => ({
  Numbers: ({ value, duration }: { value: number; duration?: number }) => (
    <span data-numbers data-value={value} data-duration={duration === undefined ? 'roll' : String(duration)} />
  ),
}))

globalThis.fetch = (async () => new Response('{}', { status: 200 })) as typeof fetch
class NoopResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}
globalThis.ResizeObserver = NoopResizeObserver as unknown as typeof ResizeObserver
declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const panel = (id: string, x: number, y: number): PanelObject =>
  ({
    id,
    type: 'panel',
    x,
    y,
    w: 80,
    h: 55,
    fileId: `${id}.pdf`,
    fileKind: 'pdf',
    nativeW: 80,
    nativeH: 55,
    overrides: [],
  }) as unknown as PanelObject

const down = (clientX = 0, clientY = 0) =>
  ({ clientX, clientY, button: 0, stopPropagation() {}, preventDefault() {} }) as unknown as React.PointerEvent
const fire = (type: 'pointermove' | 'pointerup' | 'pointercancel', clientX: number, clientY: number, altKey = false) =>
  window.dispatchEvent(new MouseEvent(type, { clientX, clientY, altKey, bubbles: true }))

/** zoom 2：1 mm = 2 × BASE_PX_PER_MM 屏幕像素 */
const PX_PER_MM = () => mmToWorld(1) * useViewportStore.getState().zoom

let container: HTMLDivElement
let root: Root

beforeEach(async () => {
  localStorage.clear()
  useWorkspaceStore.getState().clear()
  useUiStore.setState({ tool: 'select', snapEnabled: false })
  useViewportStore.setState({ zoom: 2, panX: 100, panY: 80, originX: 0, originY: 0, viewW: 1200, viewH: 800 })
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_page_edge')
  useDocumentStore.getState().commit(literal('摆图'), (d) => {
    d.page.w = 180
    d.page.h = 120
    d.objects.push(panel('a', 7, 18))
  })
  settleMode()
  expect(useWorkspaceStore.getState().mode).toBe('fast_edit')
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
})

afterEach(() => {
  cancelActivePointerGesture()
  act(() => root.unmount())
  container.remove()
  useInteractionStore.getState().end()
})



const fig = () => useDocumentStore.getState().doc.objects.find((o) => o.id === 'a')!
const undoDepth = () => useDocumentStore.getState().past.length
const mount = async () => {
  await act(async () => {
    root.render(
      <TooltipProvider>
        <CanvasStage />
      </TooltipProvider>,
    )
  })
}

describe('单图编辑拖图的边（手势）', () => {
  it('拖右边改宽：x / y / h 不动；整次拖动只提交一个事务，撤销回原宽', () => {
    const d0 = undoDepth()
    startFigureEdgeResize(down(0, 0), 'r', 'a')
    expect(undoDepth()).toBe(d0) // 事务没提交
    fire('pointermove', 10 * PX_PER_MM(), 0)
    fire('pointermove', 20 * PX_PER_MM(), 0)
    expect(fig()).toMatchObject({ x: 7, y: 18, w: 100, h: 55 })
    expect(undoDepth()).toBe(d0) // 逐帧只改事务草稿，不入栈
    fire('pointerup', 20 * PX_PER_MM(), 0)
    expect(undoDepth()).toBe(d0 + 1)
    expect(useInteractionStore.getState().kind).toBe('none')
    act(() => void useDocumentStore.getState().undo())
    expect(fig()).toMatchObject({ x: 7, y: 18, w: 80, h: 55 })
  })

  it('拖左边：右边不动，图向左长出去', () => {
    startFigureEdgeResize(down(0, 0), 'l', 'a')
    fire('pointermove', -15 * PX_PER_MM(), 0)
    fire('pointerup', -15 * PX_PER_MM(), 0)
    expect(fig().w).toBe(95)
    expect(fig().x + fig().w).toBeCloseTo(87, 6)
  })

  it('拖上 / 下边改高', () => {
    startFigureEdgeResize(down(0, 0), 'b', 'a')
    fire('pointermove', 0, -5 * PX_PER_MM())
    fire('pointerup', 0, -5 * PX_PER_MM())
    expect(fig()).toMatchObject({ y: 18, h: 50, w: 80 })
    startFigureEdgeResize(down(0, 0), 't', 'a')
    fire('pointermove', 0, 4 * PX_PER_MM())
    fire('pointerup', 0, 4 * PX_PER_MM())
    expect(fig().h).toBe(46)
    expect(fig().y + fig().h).toBeCloseTo(68, 6)
  })

  it('Esc / 系统打断回到原框，不留历史；没拖动不入栈', () => {
    const d0 = undoDepth()
    startFigureEdgeResize(down(0, 0), 'l', 'a')
    fire('pointermove', -30 * PX_PER_MM(), 0)
    fire('pointercancel', -30 * PX_PER_MM(), 0)
    expect(fig()).toMatchObject({ x: 7, w: 80 })
    startFigureEdgeResize(down(0, 0), 'r', 'a')
    fire('pointerup', 0, 0)
    expect(undoDepth()).toBe(d0)
  })

  it('宽度吸栏宽（±2 mm），标注上写栏宽名；⌥ 不吸；高度不吸', () => {
    const dx = (mm: number) => mm * PX_PER_MM()
    startFigureEdgeResize(down(0, 0), 'r', 'a')
    fire('pointermove', dx(69.5), 0) // 149.5 → 150（内置默认规范的双栏宽，与拖页面边同一份）
    expect(fig().w).toBe(150)
    expect(useInteractionStore.getState().pageEdge?.snapLabel).toBeTruthy()
    fire('pointermove', dx(69.5), 0, true)
    expect(fig().w).toBe(149.5)
    expect(useInteractionStore.getState().pageEdge?.snapLabel).toBeNull()
    fire('pointerup', dx(69.5), 0)
    startFigureEdgeResize(down(0, 0), 'b', 'a')
    fire('pointermove', 0, dx(0.7))
    expect(fig().h).toBe(55.7)
    fire('pointerup', 0, dx(0.7))
  })

  it('锁定的图不响应', () => {
    act(() => {
      useDocumentStore.getState().commit(literal('lock'), (d) => {
        ;(d.objects[0] as PanelObject).locked = true
      })
    })
    const d0 = undoDepth()
    startFigureEdgeResize(down(0, 0), 'r', 'a')
    fire('pointermove', 20 * PX_PER_MM(), 0)
    fire('pointerup', 20 * PX_PER_MM(), 0)
    expect(fig().w).toBe(80)
    expect(undoDepth()).toBe(d0)
  })
})

describe('单图编辑拖边的命中区与标注', () => {
  it('单图编辑里四条边各有一个命中区（data-figure-edge），没有页面的拖边', async () => {
    await mount()
    const edges = [...container.querySelectorAll('[data-figure-edge]')]
    expect(edges.map((e) => e.getAttribute('data-figure-edge')).sort()).toEqual(['b', 'l', 'r', 't'])
    expect(container.querySelector('[data-page-edge]')).toBeNull()
    // 右边命中区压在图右边上：边线在命中区里偏外（只压进图里 3 px）
    const t = getTransform()
    const r = container.querySelector('[data-figure-edge="r"]') as HTMLElement
    const edgeX = t.panX + mmToWorld(87) * t.zoom
    expect(parseFloat(r.style.left)).toBeCloseTo(edgeX - 7, 4)
    expect(parseFloat(r.style.left) + parseFloat(r.style.width)).toBeCloseTo(edgeX + 3, 4)
  })

  it('拖动中出现尺寸标注（data-figure-dims），数字是图的宽高；松手后消失', async () => {
    await mount()
    act(() => useViewportStore.setState({ viewW: 4000, viewH: 3000 }))
    expect(container.querySelector('[data-figure-dims]')).toBeNull()
    act(() => startFigureEdgeResize(down(0, 0), 'r', 'a'))
    fire('pointermove', 10 * PX_PER_MM(), 0)
    await act(async () => {})
    expect(container.querySelectorAll('[data-figure-dims]')).toHaveLength(1)
    expect(container.querySelector('[data-page-dims]')).toBeNull()
    const w = container.querySelector('[data-dim-readout="width"] [data-numbers]')!
    expect(w.getAttribute('data-value')).toBe('90')
    const h = container.querySelector('[data-dim-readout="height"] [data-numbers]')!
    expect(h.getAttribute('data-value')).toBe('55')
    fire('pointerup', 10 * PX_PER_MM(), 0)
    await act(async () => {})
    expect(container.querySelector('[data-figure-dims]')).toBeNull()
  })

  it('图内编辑里照常可用；非选择工具时让位', async () => {
    await mount()
    act(() => useUiStore.getState().setElementPanel('a'))
    expect(container.querySelectorAll('[data-figure-edge]')).toHaveLength(4)
    act(() => useUiStore.getState().setElementPanel(null))
    expect(container.querySelectorAll('[data-figure-edge]')).toHaveLength(4)
    act(() => useUiStore.setState({ tool: 'rect' as never }))
    expect((container.querySelector('[data-figure-edge="r"]') as HTMLElement).className).toContain('pointer-events-none')
  })
})
