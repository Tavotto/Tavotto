/**
 * CanvasHud：画布左下角按需出现的浮层——2026-10-07 起**只有工具提示**（设计审计 §10.1）。拖动中的几何读数
 * 搬到了选区旁边（`canvas/MeasureChip`，用例在 `canvas/measureChip.test.tsx`）。
 *
 * 钉住的事实：
 *   1. 非交互 + 非选择工具：只显示工具提示，且对读屏器隐藏；它是一颗胶囊（单行浮动条）；
 *   2. 拖动进行中不说工具提示，也不再有任何读数盒；
 *   3. 非交互 + 选择工具：什么都不渲染。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { CanvasHud, HUD_NOTICE_GAP, hudLift, NotificationRail } from '@/components/StatusBar'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { literal, setLocale } from '@/i18n'
import { useNoticeRailStore } from '@/store/noticeRailStore'
import { useDocumentStore } from '@/store/documentStore'
import { useInteractionStore } from '@/store/interactionStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { emptyProject } from '@/types/document'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

let container: HTMLDivElement
let root: Root

const mount = () => {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  act(() => root.render(<CanvasHud />))
}

beforeEach(async () => {
  localStorage.clear()
  useUiStore.setState({ tool: 'select' })
  useSelectionStore.getState().clear()
  useInteractionStore.getState().end()
  useInteractionStore.getState().setCursor(null)
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_hud')
})

afterEach(async () => {
  await act(async () => root.unmount())
  container.remove()
  useInteractionStore.getState().end()
  useInteractionStore.getState().setCursor(null)
  await setLocale('zh-CN')
})

describe('CanvasHud：只有工具提示', () => {
  it('非交互 + 绘制工具：只显示工具提示，对读屏器隐藏，是一颗胶囊', () => {
    useUiStore.setState({ tool: 'rect' })
    mount()
    expect(container.textContent).toBe('拖动绘制矩形；Esc 取消')
    const hud = container.querySelector<HTMLElement>('[data-canvas-hud]')!
    expect(hud.getAttribute('aria-hidden')).toBe('true')
    expect(hud.querySelector('p')!.className).toContain('rounded-full')
  })

  it('拖动中：不说工具提示，也没有读数盒（读数在选区旁边）', () => {
    useUiStore.setState({ tool: 'rect' })
    mount()
    act(() => {
      useInteractionStore.getState().begin('draw')
      useInteractionStore.getState().setCursor({ x: 1, y: 2 })
    })
    expect(container.innerHTML).toBe('')
  })

  it('非交互 + 选择工具：什么都不渲染', () => {
    mount()
    expect(container.innerHTML).toBe('')
  })
})

describe('hudLift：底部通知轨有通知时，工具提示抬到通知上方', () => {
  it('没有通知 / 量不到容器：不抬（按工具条位置的缺省落）', () => {
    expect(hudLift(null, 800)).toBeNull()
    expect(hudLift(700, null)).toBeNull()
  })

  it('有通知：bottom = 容器底边 − 通知顶边 + 缝（提示的底边正好在通知顶边之上）', () => {
    expect(hudLift(790, 892)).toBe(892 - 790 + HUD_NOTICE_GAP)
  })

  it('通知顶边在容器底边之下（量的时机错开）：不给负数', () => {
    expect(hudLift(900, 892)).toBe(0)
  })
})

describe('通知轨的顶边：窗口尺寸变了要重量（读它的浮动胶囊才不会按旧位置避让）', () => {
  it('窗口变矮、通知没重渲染：resize 后共用的顶边跟着换', () => {
    let top = 600
    const orig = Element.prototype.getBoundingClientRect
    Element.prototype.getBoundingClientRect = function (this: Element) {
      const r = orig.call(this)
      return this.hasAttribute('data-toast-layout') ? ({ ...r.toJSON?.(), top, bottom: top + 40 } as DOMRect) : r
    }
    try {
      container = document.createElement('div')
      document.body.appendChild(container)
      root = createRoot(container)
      act(() => useUiStore.getState().setStatus(literal('渲染完成')))
      act(() => root.render(<TooltipProvider><NotificationRail /></TooltipProvider>))
      expect(useNoticeRailStore.getState().top).toBe(600)
      top = 420 // 窗口变矮：通知跟着底边锚上移，但没有新的渲染
      act(() => window.dispatchEvent(new Event('resize')))
      expect(useNoticeRailStore.getState().top).toBe(420)
    } finally {
      Element.prototype.getBoundingClientRect = orig
      act(() => useUiStore.setState({ status: null }))
    }
  })
})
