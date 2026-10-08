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

import { CanvasHud } from '@/components/StatusBar'
import { setLocale } from '@/i18n'
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
