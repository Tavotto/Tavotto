/**
 * 空白画布的右键菜单与素材落点预览（2026-10-07 设计审计 §10.1）。
 *   1. 空白处右键：拦下浏览器 / WebView 自己的菜单，开画布菜单（粘贴 / 全选 / 适应 / 视图开关 / 画布设置）；
 *   2. 对象上的右键不归它（对象有自己的菜单）；
 *   3. 视图开关选了不关菜单、真的改了 uiStore；没有对象时「全选」置灰；
 *   4. 从素材库拖一张图进来：落点预览框出现，大小与松手后 `placePanelInPage` 落的是同一个框；离开就收。
 * 主语：`data-canvas-stage` 上派发的原生事件；菜单认 `data-canvas-menu` / `data-canvas-menu-item`，预览认 `data-drop-ghost`。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { CanvasStage } from '@/canvas/CanvasStage'
import { TooltipProvider } from '@/components/ui/Tooltip'
import type { PanelInfo } from '@/lib/api'
import { placePanelInPage } from '@/lib/panelPlacement'
import { useAssetStore } from '@/store/assetStore'
import { useDocumentStore } from '@/store/documentStore'
import { useUiStore } from '@/store/uiStore'
import { mmToWorld, useViewportStore } from '@/store/viewportStore'
import { emptyProject } from '@/types/document'

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

const asset: PanelInfo = { id: 'fig1', name: 'fig1', folder: '.', kind: 'pdf', native_w_mm: 80, native_h_mm: 60, mtime: 1 }

let root: Root
let host: HTMLDivElement
const stage = () => document.querySelector<HTMLElement>('[data-canvas-stage]')!
const menu = () => document.querySelector<HTMLElement>('[data-canvas-menu]')
const item = (k: string) => document.querySelector<HTMLElement>(`[data-canvas-menu-item="${k}"]`)!

beforeEach(async () => {
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_canvas_menu')
  useUiStore.setState({ showRulers: false, showGrid: false })
  useAssetStore.setState({ byId: { fig1: asset }, panels: [asset], loaded: true } as never)
  useViewportStore.setState({ zoom: 1, panX: 0, panY: 0, originX: 0, originY: 0 })
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () =>
    root.render(
      <TooltipProvider>
        <CanvasStage />
      </TooltipProvider>,
    ),
  )
})

afterEach(async () => {
  await act(async () => root.unmount())
  document.body.innerHTML = ''
})

const contextMenuAt = async (target: Element) => {
  const ev = new MouseEvent('contextmenu', { bubbles: true, cancelable: true, clientX: 40, clientY: 50 })
  await act(async () => {
    target.dispatchEvent(ev)
    await new Promise((r) => setTimeout(r, 0))
  })
  return ev
}

describe('空白画布的右键菜单', () => {
  it('空白处右键：拦下原生菜单，开画布菜单', async () => {
    const ev = await contextMenuAt(stage())
    expect(ev.defaultPrevented).toBe(true)
    expect(menu()).not.toBeNull()
    for (const k of ['paste', 'select-all', 'fit', 'rulers', 'grid', 'safe-area', 'canvas-settings'])
      expect(item(k), k).not.toBeNull()
  })

  it('没有对象：「全选」置灰', async () => {
    await contextMenuAt(stage())
    expect(item('select-all').hasAttribute('data-disabled')).toBe(true)
  })

  it('视图开关：选了不关菜单，真的改了 uiStore', async () => {
    await contextMenuAt(stage())
    await act(async () => item('rulers').click())
    expect(useUiStore.getState().showRulers).toBe(true)
    expect(menu(), '开关选完菜单还开着').not.toBeNull()
  })

  it('对象上的右键不归它', async () => {
    const obj = document.createElement('div')
    obj.setAttribute('data-object-id', 'x')
    stage().appendChild(obj)
    const ev = await contextMenuAt(obj)
    expect(ev.defaultPrevented).toBe(false)
    expect(menu()).toBeNull()
    obj.remove()
  })
})

describe('素材落点预览', () => {
  it('拖着素材经过画布：预览框 = 松手后落的那个框；离开就收', async () => {
    // 素材库那边开始拖：dragstart 冒到 document 时记下被拖的是哪一张
    const card = document.createElement('div')
    document.body.appendChild(card)
    // jsdom 没有 DataTransfer：给一个只认这一种类型的假对象
    const dt = {
      types: ['application/x-panel-id'],
      getData: (k: string) => (k === 'application/x-panel-id' ? 'fig1' : ''),
      setData: () => {},
      dropEffect: 'none',
    }
    await act(async () => {
      const start = new Event('dragstart', { bubbles: true }) as DragEvent
      Object.defineProperty(start, 'dataTransfer', { value: dt })
      card.dispatchEvent(start)
    })
    await act(async () => {
      const over = new Event('dragover', { bubbles: true, cancelable: true }) as DragEvent
      Object.defineProperty(over, 'dataTransfer', { value: dt })
      Object.defineProperty(over, 'clientX', { value: mmToWorld(60) })
      Object.defineProperty(over, 'clientY', { value: mmToWorld(50) })
      stage().dispatchEvent(over)
    })
    const ghost = document.querySelector<HTMLElement>('[data-drop-ghost]')
    expect(ghost).not.toBeNull()
    const page = useDocumentStore.getState().doc.page
    const want = placePanelInPage(80, 60, page, { x: 60, y: 50 })
    expect(parseFloat(ghost!.style.width)).toBeCloseTo(mmToWorld(want.w), 1)
    expect(parseFloat(ghost!.style.left)).toBeCloseTo(mmToWorld(want.x), 1)
    await act(async () => {
      document.dispatchEvent(new Event('dragend'))
    })
    expect(document.querySelector('[data-drop-ghost]')).toBeNull()
    card.remove()
  })
})
