/**
 * 快速编辑里**版面快捷键必须一起关掉**（评审 #208 的 P1）。
 *
 * 顶栏在 `mode === 'fast_edit'` 时把绘制工具那一组按钮藏了起来，
 * `openFastEdit()` 进来时也把工具收回 `select`——两处都说明作者知道这一屏上
 * 没有画布标注的位置。但 `useKeyboard` 不看模式：T/A/R/O/L 照样 `setTool()`，
 * 方向键照样 `nudgeSelected()`。于是在一个「除了这张图什么都不显示」的画面
 * 里，用户能画出一个看不见的矩形、把图在版上挪走，而两者都进文档、进历史、
 * 跟着导出。**只藏按钮不挡快捷键 = 藏的是入口不是能力。**
 *
 * 每条都配一个**反向对照**（同一个键在排版模式下必须照常работать），否则
 * 「什么都没发生」这件事也可能是判据自己没执行到。
 */
import { createElement } from 'react'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { useKeyboard } from './useKeyboard'
import { useDocumentStore } from '@/store/documentStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { useWorkspaceStore } from '@/store/workspace'
import { canvasToDoc } from '@/types/document'
import type { CanvasData, PanelObject } from '@/types/document'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const PANEL: PanelObject = {
  id: 'p1',
  type: 'panel',
  fileId: 'Fig1.pdf',
  fileKind: 'pdf',
  nativeW: 80,
  nativeH: 60,
  overrides: [],
  x: 10,
  y: 20,
  w: 40,
  h: 30,
}

let root: Root | null = null

function Harness() {
  useKeyboard()
  return null
}

function mount() {
  const el = document.createElement('div')
  document.body.appendChild(el)
  root = createRoot(el)
  act(() => {
    root!.render(createElement(Harness))
  })
}

const panel = () => useDocumentStore.getState().doc.objects[0] as PanelObject

const press = (key: string) => {
  act(() => {
    window.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true }))
  })
}

beforeEach(() => {
  const canvas: CanvasData = {
    id: 'c1',
    name: 'Fig 1',
    page: { w: 150, h: 100 },
    objects: [{ ...PANEL }],
    guides: [],
  }
  useDocumentStore.setState({
    doc: canvasToDoc(canvas),
    canvases: [canvas],
    activeCanvasId: 'c1',
    openTabs: ['c1'],
    past: [],
    future: [],
    txn: null,
  })
  useSelectionStore.getState().set([PANEL.id])
  useUiStore.setState({ tool: 'select' })
  useWorkspaceStore.getState().clear()
  mount()
})

afterEach(() => {
  act(() => root?.unmount())
  root = null
  document.body.innerHTML = ''
  useWorkspaceStore.getState().clear()
})

describe('快速编辑里的版面快捷键', () => {
  it('方向键在排版模式下照常推动面板（对照组）', () => {
    press('ArrowRight')
    expect(panel().x).toBeCloseTo(10.5)
  })

  it('方向键在快速编辑里一个字都不改', () => {
    useWorkspaceStore.getState().enterFastEdit(PANEL.id)
    press('ArrowRight')
    press('ArrowDown')
    expect(panel().x).toBe(10)
    expect(panel().y).toBe(20)
    // 也没有偷偷进历史
    expect(useDocumentStore.getState().past).toHaveLength(0)
  })

  it('绘制工具快捷键在排版模式下照常切换（对照组）', () => {
    press('r')
    expect(useUiStore.getState().tool).toBe('rect')
  })

  it('绘制工具快捷键在快速编辑里全部无效', () => {
    useWorkspaceStore.getState().enterFastEdit(PANEL.id)
    for (const key of ['t', 'a', 'r', 'o', 'l']) {
      press(key)
      expect(useUiStore.getState().tool).toBe('select')
    }
  })

  it('`V`（回到选择工具）在快速编辑里仍然有效', () => {
    // 挡的是「在这一屏上做版面动作」，不是「回到那个唯一合法的工具」。
    useUiStore.setState({ tool: 'rect' })
    useWorkspaceStore.getState().enterFastEdit(PANEL.id)
    press('v')
    expect(useUiStore.getState().tool).toBe('select')
  })
})
