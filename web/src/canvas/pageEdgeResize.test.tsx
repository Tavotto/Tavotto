/**
 * 多图排版里拖页面四条边改画布大小（渐进式画布改版 · 裁决 5）。
 *
 * 判据打在看得见的差别上：页面尺寸、对象在屏幕上的位置、撤销条数、标注是否出现。
 * 数学（吸附 / 夹取 / 平移量）在 `lib/pageEdgeResize.test.ts`。
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
import { mmToViewX, mmToViewY, getTransform, mmToWorld, useViewportStore } from '@/store/viewportStore'
import { openFastEdit, useWorkspaceStore } from '@/store/workspace'
import { emptyProject, type PanelObject } from '@/types/document'
import { CanvasStage } from './CanvasStage'
import { startPageSizeFit } from '@/store/pageFit'
import { startPageEdgeResize } from './pageEdgeDrag'

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

const page = () => useDocumentStore.getState().doc.page
const obj = (id: string) => useDocumentStore.getState().doc.objects.find((o) => o.id === id)!
/** 对象左上角此刻在屏幕（视口）上的位置 */
const screenPos = (id: string) => {
  const t = getTransform()
  const o = obj(id)
  return { x: mmToViewX(o.x, t), y: mmToViewY(o.y, t) }
}

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
    d.objects.push(panel('a', 7, 18), panel('b', 92, 18))
    d.guides.push({ axis: 'x', pos: 90 })
  })
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

const mount = () =>
  act(() => {
    root.render(
      <TooltipProvider>
        <CanvasStage />
      </TooltipProvider>,
    )
  })

describe('拖页面边（手势）', () => {
  it('拖右边改宽：对象不动；松手只记一步撤销，撤销回到原宽', () => {
    startPageEdgeResize(down(0, 0), 'r')
    fire('pointermove', 20 * PX_PER_MM(), 0) // +20 mm → 200
    expect(page().w).toBe(200)
    expect(obj('a').x).toBe(7)
    fire('pointermove', 30 * PX_PER_MM(), 0)
    fire('pointerup', 30 * PX_PER_MM(), 0)
    expect(page().w).toBe(210)
    expect(useInteractionStore.getState().kind).toBe('none')
    act(() => void useDocumentStore.getState().undo())
    expect(page().w).toBe(180)
    expect(page().h).toBe(120)
  })

  it('拖左边：宽度变，页面坐标反向补偿，对象在屏幕上的位置不变', () => {
    const before = { a: screenPos('a'), b: screenPos('b') }
    startPageEdgeResize(down(0, 0), 'l')
    fire('pointermove', -30 * PX_PER_MM(), 0) // 往左拖 30 mm → 宽 210
    expect(page().w).toBe(210)
    expect(obj('a').x).toBe(37)
    expect(obj('b').x).toBe(122)
    expect(useDocumentStore.getState().doc.guides[0].pos).toBe(120)
    // 视口平移跟着补偿：屏幕上没动
    expect(screenPos('a').x).toBeCloseTo(before.a.x, 6)
    expect(screenPos('b').x).toBeCloseTo(before.b.x, 6)
    expect(screenPos('a').y).toBeCloseTo(before.a.y, 6)
    fire('pointerup', -30 * PX_PER_MM(), 0)
    expect(screenPos('a').x).toBeCloseTo(before.a.x, 6)
  })

  it('拖上边同理（y 轴）', () => {
    const before = screenPos('a')
    startPageEdgeResize(down(0, 0), 't')
    fire('pointermove', 0, -10 * PX_PER_MM())
    expect(page().h).toBe(130)
    expect(obj('a').y).toBe(28)
    expect(screenPos('a').y).toBeCloseTo(before.y, 6)
    fire('pointerup', 0, -10 * PX_PER_MM())
  })

  it('宽度吸栏宽（±2 mm），标注上写栏宽名；⌥ 不吸', () => {
    startPageEdgeResize(down(0, 0), 'r')
    const dx = (mm: number) => mm * PX_PER_MM()
    fire('pointermove', dx(-28.5), 0) // 151.5 → 150（内置默认规范的双栏宽）
    expect(page().w).toBe(150)
    expect(useInteractionStore.getState().pageEdge?.snapLabel).toBeTruthy()
    fire('pointermove', dx(-28.5), 0, true)
    expect(page().w).toBe(151.5)
    expect(useInteractionStore.getState().pageEdge?.snapLabel).toBeNull()
    fire('pointerup', dx(-28.5), 0)
  })

  it('最小 30 mm，允许比图小（图超出页面不拦）', () => {
    startPageEdgeResize(down(0, 0), 'r')
    fire('pointermove', -500 * PX_PER_MM(), 0)
    expect(page().w).toBe(30)
    fire('pointerup', -500 * PX_PER_MM(), 0)
    expect(obj('b').x).toBe(92)
  })

  it('Esc（取消手势）回到原值，视口也回到原处，不留历史', () => {
    const pan = { x: useViewportStore.getState().panX, y: useViewportStore.getState().panY }
    startPageEdgeResize(down(0, 0), 'l')
    fire('pointermove', -30 * PX_PER_MM(), 0)
    expect(page().w).toBe(210)
    expect(cancelActivePointerGesture()).toBe(true)
    expect(page()).toMatchObject({ w: 180, h: 120 })
    expect(obj('a').x).toBe(7)
    expect(useViewportStore.getState().panX).toBe(pan.x)
    expect(useDocumentStore.getState().txn).toBeNull()
    expect(useInteractionStore.getState().kind).toBe('none')
  })

  it('没拖动（点一下）不改文档、不进历史', () => {
    startPageEdgeResize(down(0, 0), 'r')
    fire('pointerup', 0, 0)
    expect(page().w).toBe(180)
    expect(useDocumentStore.getState().txn).toBeNull()
  })

  it('拖动期间不触发「页面尺寸变了就补间取景」', () => {
    const fit = vi.spyOn(useViewportStore.getState(), 'fitAnimated')
    const stop = startPageSizeFit()
    startPageEdgeResize(down(0, 0), 'r')
    fire('pointermove', 20 * PX_PER_MM(), 0)
    expect(fit).not.toHaveBeenCalled()
    fire('pointerup', 20 * PX_PER_MM(), 0)
    stop()
    fit.mockRestore()
  })
})

describe('画布上的拖边命中区与标注', () => {
  it('多图排版里四条边各有一个命中区，命中区屏幕宽度恒定、不随倍率变', async () => {
    await mount()
    const edges = [...container.querySelectorAll('[data-page-edge]')]
    expect(edges.map((e) => e.getAttribute('data-page-edge')).sort()).toEqual(['b', 'l', 'r', 't'])
    const widthAt = (z: number) => {
      act(() => useViewportStore.setState({ zoom: z }))
      return (container.querySelector('[data-page-edge="l"]') as HTMLElement).style.width
    }
    expect(widthAt(1)).toBe(widthAt(4))
  })

  it('拖动中出现尺寸标注，松手后消失；标注不进文档', async () => {
    await mount()
    expect(container.querySelector('[data-page-dims]')).toBeNull()
    const docBefore = JSON.stringify(useDocumentStore.getState().doc.objects)
    act(() => startPageEdgeResize(down(0, 0), 'r'))
    act(() => fire('pointermove', 20 * PX_PER_MM(), 0))
    const dims = container.querySelector('[data-page-dims]')
    expect(dims).not.toBeNull()
    expect(dims!.querySelector('[data-dim-chip="width"] [data-numbers]')?.getAttribute('data-value')).toBe('200')
    act(() => fire('pointerup', 20 * PX_PER_MM(), 0))
    expect(container.querySelector('[data-page-dims]')).toBeNull()
    expect(JSON.stringify(useDocumentStore.getState().doc.objects)).toBe(docBefore)
  })

  it('标注读数：逐 mm 拖动即时换（duration 0），吸上栏宽 / ⌥ 跳变时才滚', async () => {
    await mount()
    const dur = () =>
      [...container.querySelectorAll('[data-dim-chip] [data-numbers]')].map((n) => n.getAttribute('data-duration'))
    const mm = (v: number) => v * PX_PER_MM()
    act(() => startPageEdgeResize(down(0, 0), 'r'))
    act(() => fire('pointermove', mm(-10), 0)) // 170：连续
    expect(dur()).toEqual(['0', '0'])
    act(() => fire('pointermove', mm(-11), 0)) // 169：连续
    expect(dur()).toEqual(['0', '0'])
    act(() => fire('pointermove', mm(-29), 0)) // 151 → 吸到 150：跳变
    expect(page().w).toBe(150)
    expect(dur()).toEqual(['roll', 'roll'])
    act(() => fire('pointermove', mm(-29.2), 0)) // 仍吸着：回到连续
    expect(dur()).toEqual(['0', '0'])
    act(() => fire('pointermove', mm(-29.2), 0, true)) // ⌥ 按下：跳变
    expect(dur()).toEqual(['roll', 'roll'])
    act(() => fire('pointerup', mm(-29.2), 0))
  })

  it('单图编辑（fast_edit）没有拖边', async () => {
    useDocumentStore.getState().commit(literal('x'), () => {})
    act(() => openFastEdit('a.pdf'))
    await mount()
    expect(container.querySelector('[data-page-edge]')).toBeNull()
  })
})
