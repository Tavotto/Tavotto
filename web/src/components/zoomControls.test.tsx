/**
 * 缩放菜单（2026-10-07 设计审计 §10.1）：顶上一格可以直接敲倍率；「缩放到选中」（⇧2）没有选中时置灰、
 * 有选中时把选区的包围盒放进视野（`fitRectAnimated`，与「适应画布」同一条补间）。
 * 主语：认 `data-zoom-menu` / `data-zoom-value-row` / `data-zoom-selection-item`；动作认 viewport store 的调用。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ZoomControls } from './ZoomControls'
import { useDocumentStore } from '@/store/documentStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useViewportStore } from '@/store/viewportStore'
import { useWorkspaceStore } from '@/store/workspace'
import { zoomToSelection } from '@/store/zoomToSelection'
import { emptyProject, type PanelObject, type TextObject } from '@/types/document'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true
globalThis.fetch = (async () => new Response('{}', { status: 200 })) as typeof fetch

const text: TextObject = {
  id: 't1', type: 'text', text: 'a', sizePt: 9, bold: false,
  color: '#000', align: 'left', x: 10, y: 20, w: 30, h: 8,
}

let root: Root
let host: HTMLDivElement

const open = async () => {
  const trigger = host.querySelector<HTMLElement>('[data-zoom-menu]')!
  await act(async () => {
    trigger.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, button: 0 }))
    await new Promise((r) => setTimeout(r, 0))
  })
}
const item = () => document.querySelector<HTMLElement>('[data-zoom-selection-item]')!

beforeEach(async () => {
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_zoom')
  useDocumentStore.getState().silent((d) => {
    d.objects.push(text)
  })
  useSelectionStore.setState({ ids: [] })
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => root.render(<ZoomControls />))
})
afterEach(async () => {
  await act(async () => root.unmount())
  document.body.innerHTML = ''
  useWorkspaceStore.getState().clear()
  vi.restoreAllMocks()
})

describe('缩放菜单', () => {
  it('顶上有一格倍率输入', async () => {
    await open()
    expect(document.querySelector('[data-zoom-value-row] input')).not.toBeNull()
  })

  it('没有选中：「缩放到选中」置灰', async () => {
    await open()
    expect(item().hasAttribute('data-disabled')).toBe(true)
  })

  it('有选中：把选区包围盒交给 fitRectAnimated', async () => {
    const spy = vi.spyOn(useViewportStore.getState(), 'fitRectAnimated').mockImplementation(() => {})
    act(() => useSelectionStore.setState({ ids: ['t1'] }))
    await open()
    expect(item().hasAttribute('data-disabled')).toBe(false)
    await act(async () => item().click())
    expect(spy).toHaveBeenCalledWith({ x: 10, y: 20, w: 30, h: 8 })
  })

  // Codex #833：隐藏对象的 id 留在选区里；只看 ids 长度的话入口还亮着、点了静默空转
  it('选中的全隐藏了：「缩放到选中」置灰，⇧2 / 动作也不动视口', async () => {
    const spy = vi.spyOn(useViewportStore.getState(), 'fitRectAnimated').mockImplementation(() => {})
    act(() => useSelectionStore.setState({ ids: ['t1'] }))
    act(() =>
      useDocumentStore.getState().silent((d) => {
        d.objects[0].hidden = true
      }),
    )
    await open()
    expect(item().hasAttribute('data-disabled')).toBe(true)
    expect(zoomToSelection()).toBe(false)
    expect(spy).not.toHaveBeenCalled()
  })

  // Codex #833：旋转了的标注 x/y/w/h 是未旋转的盒；取景要按看得见的外接矩形，转出去的角不能裁在视野外
  it('旋转 45° 的正方形：取景框是外接矩形（≈1.41× 边长），盖住转出去的四个角', async () => {
    const spy = vi.spyOn(useViewportStore.getState(), 'fitRectAnimated').mockImplementation(() => {})
    act(() =>
      useDocumentStore.getState().silent((d) => {
        Object.assign(d.objects[0], { x: 0, y: 0, w: 20, h: 20, rotationDeg: 45 })
      }),
    )
    act(() => useSelectionStore.setState({ ids: ['t1'] }))
    expect(zoomToSelection()).toBe(true)
    const box = spy.mock.calls[0][0] as { x: number; y: number; w: number; h: number }
    const side = 20 * Math.SQRT2
    expect(box.w).toBeCloseTo(side, 6)
    expect(box.h).toBeCloseTo(side, 6)
    expect(box.x).toBeCloseTo(10 - side / 2, 6)
    expect(box.y).toBeCloseTo(10 - side / 2, 6)
  })
})

/**
 * 快速编辑这一屏只画正在编辑的那张图（`CanvasLayers only=`），缩放菜单还挂着。⌘A / 面板「全选」之后选区里有
 * 一整版看不见的对象——「缩放到选中」只认那张图；选区里没有它就置灰（Codex #833）。
 */
describe('缩放菜单 · 快速编辑', () => {
  const panel: PanelObject = {
    id: 'p1', type: 'panel', fileId: 'Fig1.pdf', fileKind: 'pdf', nativeW: 80, nativeH: 60,
    overrides: [], x: 100, y: 80, w: 40, h: 30,
  }
  beforeEach(() => {
    act(() =>
      useDocumentStore.getState().silent((d) => {
        d.objects.push({ ...panel })
      }),
    )
  })

  it('排版里选中两者：框两者的并（对照组）', async () => {
    const spy = vi.spyOn(useViewportStore.getState(), 'fitRectAnimated').mockImplementation(() => {})
    act(() => useSelectionStore.setState({ ids: ['t1', 'p1'] }))
    await open()
    await act(async () => item().click())
    expect(spy).toHaveBeenCalledWith({ x: 10, y: 20, w: 130, h: 90 })
  })

  it('快速编辑里选中两者：只框正在编辑的那张图', async () => {
    const spy = vi.spyOn(useViewportStore.getState(), 'fitRectAnimated').mockImplementation(() => {})
    act(() => useWorkspaceStore.getState().enterFastEdit('p1'))
    act(() => useSelectionStore.setState({ ids: ['t1', 'p1'] }))
    await open()
    expect(item().hasAttribute('data-disabled')).toBe(false)
    await act(async () => item().click())
    expect(spy).toHaveBeenCalledWith({ x: 100, y: 80, w: 40, h: 30 })
  })

  it('快速编辑里只选中了看不见的版面对象：置灰，动作不动视口', async () => {
    const spy = vi.spyOn(useViewportStore.getState(), 'fitRectAnimated').mockImplementation(() => {})
    act(() => useSelectionStore.setState({ ids: ['t1'] }))
    act(() => useWorkspaceStore.getState().enterFastEdit('p1'))
    await open()
    // 进快速编辑本身要让已开着的菜单重算（订阅了工作区模式），不是只在选区变时
    expect(item().hasAttribute('data-disabled')).toBe(true)
    expect(zoomToSelection()).toBe(false)
    expect(spy).not.toHaveBeenCalled()
  })
})
