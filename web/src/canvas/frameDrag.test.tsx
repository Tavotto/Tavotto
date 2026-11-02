/**
 * 单图编辑拖画布（ADR 0161）：边 = 留白（图不动，`doc.framePad`），角 = 等比改图幅（`figure.size_mm`，松手才提交）。
 *
 * 判据打在看得见的差别上：拖边时面板一动不动、留白进文档且整次一条历史；拖角时拖动中文档不动、只有预览框，
 * 松手一条历史；Esc / 没拖动什么都没发生；没有精确 manifest 时入口停摆。数学在 `lib/framePad.test.ts`。
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
import { useSelectionStore } from '@/store/selectionStore'
import { getTransform, mmToWorld, useViewportStore } from '@/store/viewportStore'
import { useWorkspaceStore } from '@/store/workspace'
import { settleMode } from '@/store/workspaceStore'
import { emptyProject, type PanelObject } from '@/types/document'
import { CanvasStage } from './CanvasStage'
import { startFigureCornerResize, startFramePadEdge } from './frameDrag'
import { seedExactRender } from '@/test/renderFixtures'
import { renderKeyOf, useRenderStore } from '@/store/renderStore'
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

const manifestOf = (w: number, h: number, ink?: [number, number, number, number]) =>
  ({
    size_mm: [w, h],
    elements: [
      {
        gid: 'figure',
        role: 'figure',
        editable: [{ prop: 'size_mm', type: 'pair', value: [w, h], min: 20, max: 400 }],
        ...(ink ? { ink_bbox: ink } : {}),
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

const pad = () => useDocumentStore.getState().doc.framePad
const tight = () => useDocumentStore.getState().doc.framePadTight
const status = () => useInteractionStore.getState().dragStatus
const mainKey = () => status()?.main.key

describe('拖边 = 画布留白：图不动，整次一条历史', () => {
  it('拖右边往外：留白进文档、面板与图幅不动；撤销回到没有留白', () => {
    const d0 = undoDepth()
    startFramePadEdge(down(0, 0), 'r', 'a')
    fire('pointermove', dx(6), 0)
    fire('pointermove', dx(10), 0)
    expect(pad()).toEqual({ l: 0, t: 0, r: 10, b: 0 })
    expect(mainKey()).toBe('contextBar.frameGrow')
    expect(status()?.box).toMatchObject({ x: 7, y: 18, w: 90, h: 55 })
    fire('pointerup', dx(10), 0)
    expect(fig()).toMatchObject({ x: 7, y: 18, w: 80, h: 55 })
    expect(sizeOverride()).toBeUndefined()
    expect(undoDepth()).toBe(d0 + 1)
    expect(useInteractionStore.getState().kind).toBe('none')
    expect(status()).toBeNull()
    act(() => void useDocumentStore.getState().undo())
    expect(pad()).toBeUndefined()
  })

  it('拖左边往外：面板 x 不变（画布向左长出去）', () => {
    startFramePadEdge(down(0, 0), 'l', 'a')
    fire('pointermove', dx(-8), 0)
    fire('pointerup', dx(-8), 0)
    expect(pad()).toEqual({ l: 8, t: 0, r: 0, b: 0 })
    expect(fig().x).toBe(7)
  })

  it('往里：停在内容边缘（紧凑，记进 framePadTight）；不按 ⌥ 越不过去，按 ⌥ 才裁', () => {
    act(() => seedExactRender(fig() as PanelObject, manifestOf(80, 55, [0.05, 0.04, 0.97, 0.95])))
    startFramePadEdge(down(0, 0), 'l', 'a')
    fire('pointermove', dx(-0.5), 0)
    fire('pointermove', dx(3.6), 0) // 内容边缘在 4 mm：吸附
    expect(pad()?.l).toBeCloseTo(-4, 6)
    expect(tight()).toEqual(['l'])
    expect(mainKey()).toBe('contextBar.frameTight')
    fire('pointermove', dx(20), 0)
    expect(pad()?.l).toBeCloseTo(-4, 6)
    expect(status()?.tone).toBe('warn')
    fire('pointermove', dx(20), 0, true)
    expect(pad()?.l).toBe(-20)
    expect(tight()).toBeUndefined()
    expect(status()?.tone).toBe('danger')
    fire('pointerup', dx(20), 0, true)
  })

  it('画布宽吸栏宽（图 80 + 右留白 70 = 150，内置默认规范的双栏）', () => {
    startFramePadEdge(down(0, 0), 'r', 'a')
    fire('pointermove', dx(69.2), 0)
    expect(pad()?.r).toBe(70)
    expect(useInteractionStore.getState().pageEdge?.snapLabel).toBeTruthy()
    expect(mainKey()).toBe('contextBar.frameWithCol')
    fire('pointerup', dx(69.2), 0)
  })

  it('Esc / 系统打断、没拖动：什么都没发生', () => {
    const d0 = undoDepth()
    startFramePadEdge(down(0, 0), 'r', 'a')
    fire('pointermove', dx(10), 0)
    fire('pointercancel', dx(10), 0)
    expect(pad()).toBeUndefined()
    startFramePadEdge(down(0, 0), 'r', 'a')
    fire('pointerup', 0, 0)
    expect(pad()).toBeUndefined()
    expect(undoDepth()).toBe(d0)
  })

  it('锁定的图 / 没有精确 manifest：不起手势', () => {
    act(() => useRenderStore.getState().clear?.())
    startFramePadEdge(down(0, 0), 'r', 'a')
    fire('pointermove', dx(20), 0)
    fire('pointerup', dx(20), 0)
    expect(pad()).toBeUndefined()
    expect(useInteractionStore.getState().kind).toBe('none')
  })
})

describe('拖角 = 等比改图幅：松手才提交', () => {
  it('拖右下角：拖动中文档不动、只有预览框；松手一条历史写等比的 size_mm，左上角不动', () => {
    const d0 = undoDepth()
    startFigureCornerResize(down(0, 0), 'br', 'a')
    fire('pointermove', dx(16), dx(3))
    expect(previewBox()).toMatchObject({ x: 7, y: 18, w: 96, h: 66 })
    expect(sizeOverride()).toBeUndefined()
    expect(mainKey()).toBe('contextBar.frameCornerUniform')
    expect(status()?.main.values).toMatchObject({ from: '80', to: '96' })
    fire('pointerup', dx(16), dx(3))
    expect(sizeOverride()).toEqual([96, 66])
    expect(fig()).toMatchObject({ x: 7, y: 18 })
    expect(undoDepth()).toBe(d0 + 1)
  })

  it('拖左上角：右下角不动，同一次提交里 x / y 挪好', () => {
    startFigureCornerResize(down(0, 0), 'tl', 'a')
    fire('pointermove', dx(16), 0)
    fire('pointerup', dx(16), 0)
    expect(sizeOverride()).toEqual([64, 44])
    expect(fig().x + 64).toBeCloseTo(87, 6)
    expect(fig().y + 44).toBeCloseTo(73, 6)
  })

  it('⇧ 自由比例：宽高各跟各的', () => {
    startFigureCornerResize(down(0, 0), 'br', 'a')
    fire('pointermove', dx(10), dx(-5))
    window.dispatchEvent(new MouseEvent('pointermove', { clientX: dx(10), clientY: dx(-5), shiftKey: true, altKey: true }))
    fire('pointerup', dx(10), dx(-5))
    expect(sizeOverride()).toEqual([90, 50])
  })

  it('有留白时：吸附量的是导出宽（图幅 + 左右留白），留白 mm 不变', () => {
    act(() => {
      useDocumentStore.getState().commit(literal('留白'), (d) => {
        d.framePad = { l: 5, t: 0, r: 5, b: 0 }
      })
    })
    startFigureCornerResize(down(0, 0), 'br', 'a')
    fire('pointermove', dx(59.5), 0) // 90 + 59.5 = 149.5 → 吸到 150：图幅 140
    fire('pointerup', dx(59.5), 0)
    expect((sizeOverride() as number[])[0]).toBe(140)
    expect(pad()).toEqual({ l: 5, t: 0, r: 5, b: 0 })
  })
})

describe('松手后新图回来之前：只留虚线占位', () => {
  it('松手后旧图与背纸收起、虚线框占位在目标框；新图回来且几何校正后撤掉', async () => {
    await mount()
    act(() => useViewportStore.setState({ viewW: 4000, viewH: 3000 }))
    expect(container.querySelector('[data-fast-edit-paper]')).not.toBeNull()
    act(() => startFigureCornerResize(down(0, 0), 'br', 'a'))
    fire('pointermove', dx(16), 0)
    act(() => fire('pointerup', dx(16), 0))
    await act(async () => {})
    expect(useInteractionStore.getState().figureFramePending).toMatchObject({ id: 'a', box: { w: 96 } })
    expect(container.querySelector('[data-fast-edit-paper]')).toBeNull()
    expect(container.querySelector('[data-figure-pending]')).not.toBeNull()
    act(() => seedExactRender(fig() as PanelObject, manifestOf(96, 66)))
    act(() => {
      useDocumentStore.getState().silent((d) => {
        const o = d.objects[0] as PanelObject
        o.w = 96
        o.h = 66
        o.nativeW = 96
        o.nativeH = 66
      })
    })
    await act(async () => {})
    expect(useInteractionStore.getState().figureFramePending).toBeNull()
    expect(container.querySelector('[data-fast-edit-paper]')).not.toBeNull()
  })

  it('占位期间撤销：目标作废，占位立即撤', async () => {
    await mount()
    act(() => startFigureCornerResize(down(0, 0), 'br', 'a'))
    fire('pointermove', dx(10), 0)
    act(() => fire('pointerup', dx(10), 0))
    expect(useInteractionStore.getState().figureFramePending).not.toBeNull()
    act(() => void useDocumentStore.getState().undo())
    await act(async () => {})
    expect(useInteractionStore.getState().figureFramePending).toBeNull()
  })

  it('只在当前图幅变体失败时立即撤掉占位', async () => {
    await mount()
    act(() => startFigureCornerResize(down(0, 0), 'br', 'a'))
    fire('pointermove', dx(10), 0)
    act(() => fire('pointerup', dx(10), 0))
    const current = renderKeyOf(fig() as PanelObject)
    act(() => useRenderStore.getState().patch('other-variant', { status: 'error' }))
    expect(useInteractionStore.getState().figureFramePending).not.toBeNull()
    act(() => useRenderStore.getState().patch(current, { status: 'error' }))
    expect(useInteractionStore.getState().figureFramePending).toBeNull()
  })
})

describe('画布框：命中区、角柄、画纸、画外遮罩与胶囊', () => {
  it('四条边 + 四个角，贴在画布框上（有留白时框 = 图 + 留白）', async () => {
    act(() => {
      useDocumentStore.getState().commit(literal('留白'), (d) => {
        d.framePad = { l: 0, t: 0, r: 10, b: 0 }
      })
    })
    await mount()
    const edges = [...container.querySelectorAll('[data-figure-edge]')].map((e) => e.getAttribute('data-figure-edge'))
    expect(edges.sort()).toEqual(['b', 'l', 'r', 't'])
    const corners = [...container.querySelectorAll('[data-figure-corner]')].map((e) => e.getAttribute('data-figure-corner'))
    expect(corners.sort()).toEqual(['bl', 'br', 'tl', 'tr'])
    expect(container.querySelector('[data-page-edge]')).toBeNull()
    const t = getTransform()
    const r = container.querySelector('[data-figure-edge="r"]') as HTMLElement
    const edgeX = t.panX + mmToWorld(97) * t.zoom
    expect(parseFloat(r.style.left)).toBeCloseTo(edgeX - 2, 4)
    const paper = container.querySelector('[data-fast-edit-paper]') as HTMLElement
    expect(parseFloat(paper.style.width)).toBeCloseTo(mmToWorld(90), 4)
    expect(container.querySelector('[data-page-outside-mask]')).not.toBeNull()
  })

  it('没有留白：画纸就是图的框、没有画外遮罩（与此前相同）', async () => {
    await mount()
    const paper = container.querySelector('[data-fast-edit-paper]') as HTMLElement
    expect(parseFloat(paper.style.width)).toBeCloseTo(mmToWorld(80), 4)
    expect(container.querySelector('[data-page-outside-mask]')).toBeNull()
  })

  it('没有精确 manifest（权威缺席）时整组不挂', async () => {
    act(() => useRenderStore.getState().clear?.())
    await mount()
    expect(container.querySelector('[data-figure-edge]')).toBeNull()
    expect(container.querySelector('[data-figure-corner]')).toBeNull()
  })

  it('拖边时胶囊显示读数（不随拖动隐藏），松手后消失', async () => {
    await mount()
    act(() => useViewportStore.setState({ viewW: 4000, viewH: 3000 }))
    act(() => startFramePadEdge(down(0, 0), 'r', 'a'))
    act(() => fire('pointermove', dx(10), 0))
    await act(async () => {})
    const cap = document.querySelector('[data-drag-status]')
    expect(cap).not.toBeNull()
    expect(cap!.getAttribute('data-drag-tone')).toBe('normal')
    act(() => fire('pointerup', dx(10), 0))
    await act(async () => {})
    expect(document.querySelector('[data-drag-status]')).toBeNull()
  })

  it('选中整张图（退出图内编辑）：选区不画缩放手柄，画布框的角柄 / 边带是唯一的角 / 边（拖它们走图幅 / 留白，不是视觉缩放）', async () => {
    act(() => useSelectionStore.getState().set(['a']))
    await mount()
    expect(useWorkspaceStore.getState().activePanelId).toBe('a')
    expect(container.querySelector('[data-handle]')).toBeNull()
    expect(container.querySelector('[data-edge-strip]')).toBeNull()
    expect(container.querySelectorAll('[data-figure-corner]').length).toBe(4)
    expect(container.querySelectorAll('[data-figure-edge]').length).toBe(4)
    // 按角：写的是 size_mm，面板 w / h 不被视觉缩放
    act(() => startFigureCornerResize(down(0, 0), 'br', 'a'))
    fire('pointermove', dx(16), dx(3))
    fire('pointerup', dx(16), dx(3))
    expect(sizeOverride()).toEqual([96, 66])
    expect(fig()).toMatchObject({ w: 80, h: 55 })
  })

  it('非选择工具时命中区与角柄让位', async () => {
    await mount()
    act(() => useUiStore.setState({ tool: 'rect' as never }))
    expect((container.querySelector('[data-figure-edge="r"]') as HTMLElement).className).toContain('pointer-events-none')
    expect((container.querySelector('[data-figure-corner="br"]') as HTMLElement).className).toContain('pointer-events-none')
  })
})
