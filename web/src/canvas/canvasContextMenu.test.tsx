/**
 * 空白画布的右键菜单与素材落点预览（2026-10-07 设计审计 §10.1）。
 *   1. 空白处右键：拦下浏览器 / WebView 自己的菜单，开画布菜单（粘贴 / 全选 / 适应 / 视图开关 / 画布设置）；
 *   2. 对象上的右键不归它（对象有自己的菜单）；快速编辑里不开（菜单全是版面级动作，这一屏只有那一张图）；
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
import { openFastEdit, returnToLayout } from '@/store/workspace'
import { mmToWorld, useViewportStore } from '@/store/viewportStore'
import { emptyProject } from '@/types/document'
import { literal } from '@/i18n'
import { CHROMIUM_UA, SAFARI_UA, WKWEBVIEW_UA, stubClipboardEngine } from '@/test/asyncClipboard'

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

// 默认按 Chromium 摆（有异步 readText → 菜单里有「粘贴」）；WebKit 那一组单独钉
let restoreClipboard: () => void = () => {}
beforeEach(async () => {
  restoreClipboard = stubClipboardEngine(CHROMIUM_UA, true)
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
  restoreClipboard()
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

  // Codex #833：菜单的粘贴只能走异步 readText；WebKit（Safari / 桌面壳）不给非编辑区读、Firefox 默认没有——不提供
  it.each([
    ['Safari', SAFARI_UA, true],
    ['macOS 桌面壳（WKWebView）', WKWEBVIEW_UA, true],
    ['没有 readText（Firefox 默认）', CHROMIUM_UA, false],
  ] as const)('%s：不提供「粘贴」，其余照旧', async (_name, ua, readText) => {
    restoreClipboard()
    restoreClipboard = stubClipboardEngine(ua, readText)
    await contextMenuAt(stage())
    expect(menu()).not.toBeNull()
    expect(document.querySelector('[data-canvas-menu-item="paste"]')).toBeNull()
    expect(item('select-all')).not.toBeNull()
  })

  it('没有对象：「全选」置灰', async () => {
    await contextMenuAt(stage())
    expect(item('select-all').hasAttribute('data-disabled')).toBe(true)
  })

  // Codex #833：判据与 `selectAll` 同一份——只有隐藏 / 锁定的对象时点了也选不上，不该亮着
  it('只有隐藏 / 锁定的对象：「全选」置灰；有一个可选的就亮', async () => {
    const shape = (id: string, extra: object) =>
      ({ id, type: 'shape', shape: 'rect', x: 10, y: 10, w: 20, h: 10, strokePt: 1, color: '#111', fill: null, ...extra }) as never
    await act(async () =>
      useDocumentStore.getState().commit(literal('放'), (d) => {
        d.objects.push(shape('h', { hidden: true }), shape('l', { locked: true }))
      }),
    )
    await contextMenuAt(stage())
    expect(item('select-all').hasAttribute('data-disabled')).toBe(true)
    await act(async () =>
      useDocumentStore.getState().commit(literal('放'), (d) => {
        d.objects.push(shape('ok', {}))
      }),
    )
    expect(item('select-all').hasAttribute('data-disabled')).toBe(false)
  })

  it('视图开关：选了不关菜单，真的改了 uiStore', async () => {
    await contextMenuAt(stage())
    await act(async () => item('rulers').click())
    expect(useUiStore.getState().showRulers).toBe(true)
    expect(menu(), '开关选完菜单还开着').not.toBeNull()
  })

  // Codex #833：快速编辑只画那一张图，粘贴进版面 / 全选版面 / 适应页面 / 标尺网格在这一屏都没有对应物
  it('快速编辑里空白处右键：原生菜单照样拦下，画布菜单不开', async () => {
    try {
      await act(async () => void openFastEdit('fig1'))
      const ev = await contextMenuAt(stage())
      expect(ev.defaultPrevented).toBe(true)
      expect(menu()).toBeNull()
    } finally {
      await act(async () => returnToLayout())
    }
    expect(menu(), '回到排版不会冒出一个快速编辑里点出来的菜单').toBeNull()
  })

  it('开着画布菜单切进快速编辑：菜单收起，回到排版也不在旧落点上重新冒出来', async () => {
    await contextMenuAt(stage())
    expect(menu()).not.toBeNull()
    try {
      await act(async () => void openFastEdit('fig1'))
      expect(menu()).toBeNull()
    } finally {
      await act(async () => returnToLayout())
    }
    expect(menu()).toBeNull()
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
