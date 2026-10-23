/**
 * 单图编辑里拖这张图的四条边改图幅（`figure.size_mm` override，内容不缩放、图重渲染）。
 *
 * 判据打在看得见的差别上：拖动中文档与历史纹丝不动、只有虚线预览框与标注；松手只提交一次 override
 * （一条历史），拖左 / 上边时 x / y 同次挪好；Esc / 没拖动什么都没发生；没有精确 manifest 时入口停摆。
 * 数学（吸附 / 夹取 / 换算）在 `lib/pageEdgeResize.test.ts`。
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
import { seedExactRender } from '@/test/renderFixtures'
import { useSelectionStore } from '@/store/selectionStore'
import { useRenderStore } from '@/store/renderStore'
import type { Manifest } from '@/lib/api'

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

const manifestOf = (w: number, h: number) =>
  ({
    size_mm: [w, h],
    elements: [
      {
        gid: 'figure',
        role: 'figure',
        editable: [{ prop: 'size_mm', type: 'pair', value: [w, h], min: 20, max: 400 }],
      },
    ],
  }) as unknown as Manifest
/** 给面板 a 种一份精确的渲染（图幅 80 × 55） */
const seed = () => seedExactRender(useDocumentStore.getState().doc.objects[0] as PanelObject, manifestOf(80, 55))

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
  seed()
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
  useInteractionStore.getState().setFigureFramePending(null)
})



const fig = () => useDocumentStore.getState().doc.objects.find((o) => o.id === 'a')!
const sizeOverride = () =>
  (fig() as PanelObject).overrides.find((o) => o.gid === 'figure' && o.prop === 'size_mm')?.value
const undoDepth = () => useDocumentStore.getState().past.length
const previewBox = () => useInteractionStore.getState().figureEdgeBox
const mount = async () => {
  await act(async () => {
    root.render(
      <TooltipProvider>
        <CanvasStage />
      </TooltipProvider>,
    )
  })
}
const dx = (mm: number) => mm * PX_PER_MM()

describe('单图编辑拖图的边（手势）：改图幅，松手才提交', () => {
  it('拖右边：拖动中文档与历史不动、只有预览框；松手一条历史写 size_mm；撤销回原样', () => {
    const d0 = undoDepth()
    startFigureEdgeResize(down(0, 0), 'r', 'a')
    fire('pointermove', dx(10), 0)
    fire('pointermove', dx(20), 0)
    expect(previewBox()).toMatchObject({ x: 7, y: 18, w: 100, h: 55 })
    expect(sizeOverride()).toBeUndefined()
    expect(fig()).toMatchObject({ x: 7, w: 80, h: 55 })
    expect(undoDepth()).toBe(d0)
    fire('pointerup', dx(20), 0)
    expect(sizeOverride()).toEqual([100, 55])
    expect(fig()).toMatchObject({ x: 7, y: 18 })
    expect(undoDepth()).toBe(d0 + 1)
    expect(previewBox()).toBeNull()
    expect(useInteractionStore.getState().kind).toBe('none')
    act(() => void useDocumentStore.getState().undo())
    expect(sizeOverride()).toBeUndefined()
    expect(undoDepth()).toBe(d0)
  })

  it('拖左边：同一次提交里 x 挪好，对侧（右）边不动', () => {
    const d0 = undoDepth()
    startFigureEdgeResize(down(0, 0), 'l', 'a')
    fire('pointermove', dx(-15), 0)
    expect(previewBox()).toMatchObject({ x: -8, w: 95 })
    fire('pointerup', dx(-15), 0)
    expect(sizeOverride()).toEqual([95, 55])
    expect(fig().x).toBeCloseTo(-8, 6)
    expect(fig().x + 95).toBeCloseTo(87, 6)
    expect(undoDepth()).toBe(d0 + 1)
    act(() => void useDocumentStore.getState().undo())
    expect(fig().x).toBe(7)
  })

  it('拖下 / 上边改高；上边 y 挪好', () => {
    startFigureEdgeResize(down(0, 0), 'b', 'a')
    fire('pointermove', 0, dx(5))
    fire('pointerup', 0, dx(5))
    expect(sizeOverride()).toEqual([80, 60])
    expect(fig().y).toBe(18)
    // 渲染回来后面板的页面尺寸跟着图幅走（缩放比不变）：模拟同步器的校正
    act(() => {
      useDocumentStore.getState().silent((d) => {
        d.objects[0].h = 60
        ;(d.objects[0] as PanelObject).nativeH = 60
      })
    })
    seedExactRender(fig() as PanelObject, manifestOf(80, 60))
    startFigureEdgeResize(down(0, 0), 't', 'a')
    fire('pointermove', 0, dx(-4))
    fire('pointerup', 0, dx(-4))
    expect(sizeOverride()).toEqual([80, 64])
    expect(fig().y).toBeCloseTo(14, 6) // 下边 78 不动
  })

  it('Esc / 系统打断、没拖动：什么都没发生', () => {
    const d0 = undoDepth()
    startFigureEdgeResize(down(0, 0), 'l', 'a')
    fire('pointermove', dx(-30), 0)
    fire('pointercancel', dx(-30), 0)
    expect(sizeOverride()).toBeUndefined()
    expect(fig().x).toBe(7)
    expect(previewBox()).toBeNull()
    startFigureEdgeResize(down(0, 0), 'r', 'a')
    fire('pointerup', 0, 0)
    expect(sizeOverride()).toBeUndefined()
    expect(undoDepth()).toBe(d0)
  })

  it('宽度吸栏宽（页面 mm），标注上写栏宽名；⌥ 不吸；高度不吸', () => {
    startFigureEdgeResize(down(0, 0), 'r', 'a')
    fire('pointermove', dx(69.5), 0) // 149.5 → 150（内置默认规范的双栏宽）
    expect(previewBox()?.w).toBe(150)
    expect(useInteractionStore.getState().pageEdge?.snapLabel).toBeTruthy()
    fire('pointermove', dx(69.5), 0, true)
    expect(previewBox()?.w).toBe(149.5)
    expect(useInteractionStore.getState().pageEdge?.snapLabel).toBeNull()
    fire('pointerup', dx(69.5), 0, true)
    expect(sizeOverride()).toEqual([149.5, 55])
  })

  it('面板被缩放过（页面 160 × 110 = 图幅 80 × 55 的 2 倍）：位移按页面 mm 算，写进去的是图幅 mm', () => {
    act(() => {
      useDocumentStore.getState().commit(literal('缩放'), (d) => {
        const o = d.objects[0]
        o.w = 160
        o.h = 110
      })
    })
    seed()
    startFigureEdgeResize(down(0, 0), 'r', 'a')
    fire('pointermove', dx(20), 0) // 页面 +20 mm → 180，图幅 90
    expect(previewBox()).toMatchObject({ w: 180, h: 110 })
    fire('pointerup', dx(20), 0)
    expect(sizeOverride()).toEqual([90, 55])
  })

  it('图幅夹在引擎给的界里', () => {
    startFigureEdgeResize(down(0, 0), 'r', 'a')
    fire('pointermove', dx(9999), 0)
    fire('pointerup', dx(9999), 0)
    expect((sizeOverride() as number[])[0]).toBe(400)
  })

  it('锁定的图 / 没有精确 manifest：不起手势', () => {
    const d0 = undoDepth()
    act(() => useRenderStore.getState().clear?.())
    startFigureEdgeResize(down(0, 0), 'r', 'a')
    fire('pointermove', dx(20), 0)
    fire('pointerup', dx(20), 0)
    expect(sizeOverride()).toBeUndefined()
    expect(useInteractionStore.getState().kind).toBe('none')
    seed()
    act(() => {
      useDocumentStore.getState().commit(literal('lock'), (d) => {
        ;(d.objects[0] as PanelObject).locked = true
      })
    })
    const d1 = undoDepth()
    startFigureEdgeResize(down(0, 0), 'r', 'a')
    fire('pointermove', dx(20), 0)
    fire('pointerup', dx(20), 0)
    expect(sizeOverride()).toBeUndefined()
    expect(undoDepth()).toBe(d1)
    expect(d1).toBe(d0 + 1)
  })
})

describe('松手后新图回来之前：只留虚线占位', () => {
  it('松手后旧图与背纸收起、虚线框占位在目标框；新图（精确渲染）回来后撤掉', async () => {
    await mount()
    act(() => useViewportStore.setState({ viewW: 4000, viewH: 3000 }))
    expect(container.querySelector('[data-fast-edit-paper]')).not.toBeNull()
    act(() => useSelectionStore.getState().set(['a']))
    expect(container.querySelector('[data-handle]')).not.toBeNull() // 前提：选中时手柄在
    act(() => startFigureEdgeResize(down(0, 0), 'l', 'a'))
    fire('pointermove', dx(-15), 0)
    act(() => fire('pointerup', dx(-15), 0))
    await act(async () => {})
    expect(useInteractionStore.getState().figureFramePending).toMatchObject({ id: 'a', box: { w: 95 } })
    expect(container.querySelector('[data-fast-edit-paper]')).toBeNull()
    expect(container.querySelector('[data-figure-pending]')).not.toBeNull()
    expect(container.querySelector('[data-figure-edge]')).toBeNull()
    // 选区轮廓 / 缩放手柄一并压住（面板保持选中是常态）
    expect(container.querySelector('[data-handle]')).toBeNull()
    // 新图回来，但同步器还没把面板几何校正到位：占位不撤（否则新图配旧尺寸露一帧）
    act(() => seedExactRender(fig() as PanelObject, manifestOf(95, 55)))
    await act(async () => {})
    expect(useInteractionStore.getState().figureFramePending).not.toBeNull()
    expect(container.querySelector('[data-fast-edit-paper]')).toBeNull()
    // 同步器校正（nativeW/H → w/h）落地：占位才撤
    act(() => {
      useDocumentStore.getState().silent((d) => {
        const o = d.objects[0] as PanelObject
        o.w = 95
        o.nativeW = 95
      })
    })
    await act(async () => {})
    expect(useInteractionStore.getState().figureFramePending).toBeNull()
    expect(container.querySelector('[data-figure-pending]')).toBeNull()
    expect(container.querySelector('[data-fast-edit-paper]')).not.toBeNull()
  })

  it('占位期间撤销：目标作废，占位立即撤、旧图回来', async () => {
    await mount()
    act(() => startFigureEdgeResize(down(0, 0), 'r', 'a'))
    fire('pointermove', dx(10), 0)
    act(() => fire('pointerup', dx(10), 0))
    expect(useInteractionStore.getState().figureFramePending).not.toBeNull()
    act(() => void useDocumentStore.getState().undo())
    await act(async () => {})
    expect(useInteractionStore.getState().figureFramePending).toBeNull()
  })
})

describe('单图编辑拖边的命中区与标注', () => {
  it('四条边各有一个命中区（data-figure-edge），没有页面的拖边；压进图里只有 2 px', async () => {
    await mount()
    const edges = [...container.querySelectorAll('[data-figure-edge]')]
    expect(edges.map((e) => e.getAttribute('data-figure-edge')).sort()).toEqual(['b', 'l', 'r', 't'])
    expect(container.querySelector('[data-page-edge]')).toBeNull()
    const t = getTransform()
    const r = container.querySelector('[data-figure-edge="r"]') as HTMLElement
    const edgeX = t.panX + mmToWorld(87) * t.zoom
    expect(parseFloat(r.style.left)).toBeCloseTo(edgeX - 2, 4)
    expect(parseFloat(r.style.left) + parseFloat(r.style.width)).toBeCloseTo(edgeX + 8, 4)
  })

  it('没有精确 manifest（权威缺席）时整组不挂', async () => {
    act(() => useRenderStore.getState().clear?.())
    await mount()
    expect(container.querySelector('[data-figure-edge]')).toBeNull()
  })

  it('拖动中出现虚线预览框与尺寸标注（数字 = 预览框的页面 mm）；松手后消失；标注不进文档', async () => {
    await mount()
    act(() => useViewportStore.setState({ viewW: 4000, viewH: 3000 }))
    expect(container.querySelector('[data-figure-dims]')).toBeNull()
    act(() => startFigureEdgeResize(down(0, 0), 'r', 'a'))
    fire('pointermove', dx(10), 0)
    await act(async () => {})
    expect(container.querySelectorAll('[data-figure-dims]')).toHaveLength(1)
    expect(container.querySelector('[data-page-dims]')).toBeNull()
    const pv = container.querySelector('[data-figure-preview]') as HTMLElement
    const t = getTransform()
    expect(parseFloat(pv.style.width)).toBeCloseTo(mmToWorld(90) * t.zoom, 4)
    expect(container.querySelector('[data-dim-readout="width"] [data-numbers]')!.getAttribute('data-value')).toBe('90')
    expect(container.querySelector('[data-dim-readout="height"] [data-numbers]')!.getAttribute('data-value')).toBe('55')
    fire('pointerup', dx(10), 0)
    await act(async () => {})
    expect(container.querySelector('[data-figure-dims]')).toBeNull()
    expect(container.querySelector('[data-figure-preview]')).toBeNull()
  })

  it('非选择工具时命中区让位', async () => {
    await mount()
    act(() => useUiStore.setState({ tool: 'rect' as never }))
    expect((container.querySelector('[data-figure-edge="r"]') as HTMLElement).className).toContain('pointer-events-none')
  })
})
