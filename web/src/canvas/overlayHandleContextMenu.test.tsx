/**
 * 选中框手柄上的右键（Codex #833）：对象缩放手柄的 16px 命中层、沿边命中带、图内元素框的手柄都画在
 * `OverlaySvg` 里，不在 `[data-object-id]` 底下。此前右键它们冒到 `CanvasStage`，被当成空白处开了版面菜单。
 * 钉：
 *   1. 对象手柄 / 命中带上右键 = 这个对象的菜单（quickEdit 目标是它），版面菜单不开、原生菜单拦下；
 *   2. 正在改字时与 ObjectView 同一条：留给浏览器自己的菜单，也不开版面菜单；
 *   3. 图内编辑态的元素框手柄 / 命中带上右键 = 选中元素的菜单。
 * 主语：`CanvasStage` 整棵树里 `data-handle` / `data-edge-strip` / `data-element-handle` 上派发的原生 contextmenu。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { CanvasStage } from '@/canvas/CanvasStage'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { literal } from '@/i18n'
import type { Manifest } from '@/lib/api'
import { useDocumentStore } from '@/store/documentStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { useViewportStore } from '@/store/viewportStore'
import { seedExactRender } from '@/test/renderFixtures'
import { emptyProject, type PanelObject } from '@/types/document'
import { useQuickEdit } from './quickEditStore'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true
globalThis.fetch = (async () => new Response('{}', { status: 200 })) as typeof fetch
class NoopResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}
globalThis.ResizeObserver = NoopResizeObserver as unknown as typeof ResizeObserver

const panel: PanelObject = {
  id: 'p1', type: 'panel', x: 0, y: 0, w: 100, h: 80, fileId: 'Fig1.pdf', fileKind: 'pdf',
  nativeW: 100, nativeH: 80, script: 'fig.py', overrides: [],
} as PanelObject

const manifest: Manifest = {
  stem: 'Fig1',
  size_mm: [100, 80],
  elements: [
    { gid: 'figure', role: 'figure', label: '整图', bbox: [0, 0, 1, 1], editable: [], draggable: false },
    {
      gid: 'axes_0', role: 'axes', label: '子图 1', bbox: [0.1, 0.2, 0.8, 0.7],
      editable: [{ prop: 'position', type: 'rect', value: [0.1, 0.1, 0.8, 0.7] }],
      draggable: false, resizable: true,
    },
  ],
}

let root: Root
let host: HTMLDivElement
const canvasMenu = () => document.querySelector('[data-canvas-menu]')

beforeEach(async () => {
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_overlay_ctx')
  useUiStore.setState({ showRulers: false, showGrid: false, elementPanelId: null, cropTargetId: null, editingTextId: null, selectedGids: [] })
  useViewportStore.setState({ zoom: 1, panX: 0, panY: 0, originX: 0, originY: 0, viewW: 900, viewH: 700 })
  useQuickEdit.getState().close()
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})

afterEach(async () => {
  await act(async () => root.unmount())
  document.body.innerHTML = ''
  useQuickEdit.getState().close()
  useSelectionStore.getState().set([])
})

const mount = () =>
  act(async () =>
    root.render(
      <TooltipProvider>
        <CanvasStage />
      </TooltipProvider>,
    ),
  )

const contextMenuOn = async (target: Element) => {
  const ev = new MouseEvent('contextmenu', { bubbles: true, cancelable: true, clientX: 60, clientY: 70 })
  await act(async () => {
    target.dispatchEvent(ev)
    await new Promise((r) => setTimeout(r, 0))
  })
  return ev
}

describe('选中框手柄上的右键', () => {
  it.each(['[data-handle="se"]', '[data-edge-strip="e"]'])('对象 %s：开这个对象的菜单，不开版面菜单', async (sel) => {
    await act(async () =>
      useDocumentStore.getState().commit(literal('放'), (d) => {
        d.objects.push({ id: 'r1', type: 'shape', shape: 'rect', x: 20, y: 20, w: 60, h: 30, strokePt: 1, color: '#111', fill: null } as never)
      }),
    )
    useSelectionStore.getState().set(['r1'])
    await mount()
    const ev = await contextMenuOn(document.querySelector(sel)!)
    expect(ev.defaultPrevented).toBe(true)
    expect(canvasMenu()).toBeNull()
    expect(useQuickEdit.getState().target).toEqual({ kind: 'object', id: 'r1' })
    expect(useSelectionStore.getState().ids).toEqual(['r1'])
  })

  it('正在改字：手柄上的右键留给浏览器，也不开版面菜单', async () => {
    await act(async () =>
      useDocumentStore.getState().commit(literal('放'), (d) => {
        d.objects.push({ id: 't1', type: 'text', x: 20, y: 20, w: 60, h: 10, text: 'a', fontPt: 9, color: '#111' } as never)
      }),
    )
    useSelectionStore.getState().set(['t1'])
    useUiStore.setState({ editingTextId: 't1' })
    await mount()
    const ev = await contextMenuOn(document.querySelector('[data-handle="e"]')!)
    expect(ev.defaultPrevented).toBe(false)
    expect(canvasMenu()).toBeNull()
    expect(useQuickEdit.getState().target).toBeNull()
  })

  it.each(['[data-element-handle="se"]', '[data-edge-strip="e"]'])('图内元素框 %s：开选中元素的菜单', async (sel) => {
    await act(async () =>
      useDocumentStore.getState().commit(literal('放'), (d) => {
        d.objects.push(structuredClone(panel))
      }),
    )
    seedExactRender(panel, manifest)
    useSelectionStore.getState().set(['p1'])
    useUiStore.setState({ elementPanelId: 'p1', selectedGids: ['axes_0'] })
    await mount()
    const target = document.querySelector(sel)
    expect(target, '元素框手柄画出来了').not.toBeNull()
    const ev = await contextMenuOn(target!)
    expect(ev.defaultPrevented).toBe(true)
    expect(canvasMenu()).toBeNull()
    expect(useQuickEdit.getState().target).toEqual({ kind: 'element', panelId: 'p1', gid: 'axes_0' })
  })
})
