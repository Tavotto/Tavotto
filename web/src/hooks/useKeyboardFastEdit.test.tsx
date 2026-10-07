/**
 * 快速编辑里**版面快捷键必须一起关掉**（评审 #208 的 P1）。
 *
 * 顶栏在 `mode === 'fast_edit'` 时把绘制工具那一组按钮藏了起来，
 * `openFastEdit()` 进来时也把工具收回 `select`——两处都说明作者知道这一屏上
 * 没有画布标注的位置。但 `useKeyboard` 不看模式：T/A/R/O/L 照样 `setTool()`，
 * 方向键照样推面板的 x/y。于是在一个「除了这张图什么都不显示」的画面
 * 里，用户能画出一个看不见的矩形、把图在版上挪走，而两者都进文档、进历史、
 * 跟着导出。**只藏按钮不挡快捷键 = 藏的是入口不是能力。**
 *
 * 每条都配一个**反向对照**（同一个键在排版模式下必须照常работать），否则
 * 「什么都没发生」这件事也可能是判据自己没执行到。
 */
import { createElement } from 'react'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useKeyboard } from './useKeyboard'
import { resetNudge } from '@/canvas/nudge'
import { useDocumentStore } from '@/store/documentStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { useViewportStore } from '@/store/viewportStore'
import { useWorkspaceStore } from '@/store/workspace'
import { canvasToDoc } from '@/types/document'
import type { CanvasData, PanelObject, TextObject } from '@/types/document'

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
  resetNudge()
  act(() => root?.unmount())
  root = null
  document.body.innerHTML = ''
  useWorkspaceStore.getState().clear()
  vi.restoreAllMocks()
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

/**
 * ⇧2「缩放到选中」在快速编辑里只认正在编辑的那张图（Codex #833）：这一屏只画这一张，⌘A 之后选区里还有
 * 一整版看不见的对象——按整个选区取景会把唯一看得见的图挪走、缩小。对照组：排版里照常框整个选区。
 */
describe('快速编辑里的 ⇧2', () => {
  const NOTE: TextObject = {
    id: 't1', type: 'text', text: 'a', sizePt: 9, bold: false,
    color: '#000', align: 'left', x: 100, y: 80, w: 30, h: 8,
  }
  const shift2 = () =>
    act(() => {
      window.dispatchEvent(
        new KeyboardEvent('keydown', { key: '@', code: 'Digit2', shiftKey: true, bubbles: true, cancelable: true }),
      )
    })
  const selectAllKey = () =>
    act(() => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'a', ctrlKey: true, bubbles: true, cancelable: true }))
    })
  beforeEach(() => {
    useDocumentStore.getState().silent((d) => {
      d.objects.push({ ...NOTE })
    })
  })

  it('排版里 ⌘A 后 ⇧2：框整个选区（对照组）', () => {
    const spy = vi.spyOn(useViewportStore.getState(), 'fitRectAnimated').mockImplementation(() => {})
    selectAllKey()
    expect(useSelectionStore.getState().ids).toEqual(['p1', 't1'])
    shift2()
    expect(spy).toHaveBeenCalledWith({ x: 10, y: 20, w: 120, h: 68 })
  })

  it('快速编辑里 ⌘A 后 ⇧2：只框正在编辑的那张图', () => {
    const spy = vi.spyOn(useViewportStore.getState(), 'fitRectAnimated').mockImplementation(() => {})
    useWorkspaceStore.getState().enterFastEdit(PANEL.id)
    selectAllKey()
    shift2()
    expect(spy).toHaveBeenCalledTimes(1)
    expect(spy).toHaveBeenCalledWith({ x: 10, y: 20, w: 40, h: 30 })
  })

  it('快速编辑里选区不含那张图：⇧2 不动视口', () => {
    const spy = vi.spyOn(useViewportStore.getState(), 'fitRectAnimated').mockImplementation(() => {})
    useWorkspaceStore.getState().enterFastEdit(PANEL.id)
    useSelectionStore.getState().set([NOTE.id])
    shift2()
    expect(spy).not.toHaveBeenCalled()
  })
})

/**
 * 快速编辑里的 ⌘A / Delete（Codex #833，数据丢失）：这一屏只画正在编辑的那张图，`selectAll` 曾经不看模式，
 * 把整版看不见的对象全选上，紧接着 Backspace（不在图内元素编辑里时）就经 `deleteSelected` 把它们从版上删掉。
 * 现在全选只收那张图，删除也只删得到那张图（选区里挂着的旧版面对象不动）。对照组：排版里 ⌘A 照常全选、
 * Backspace 照常整选区删除——否则「没删」也可能是判据自己没执行到。
 */
describe('快速编辑里的 ⌘A / Delete', () => {
  const NOTE: TextObject = {
    id: 't1', type: 'text', text: 'a', sizePt: 9, bold: false,
    color: '#000', align: 'left', x: 100, y: 80, w: 30, h: 8,
  }
  const modKey = (key: string) =>
    act(() => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key, ctrlKey: true, bubbles: true, cancelable: true }))
    })
  const ids = () => useDocumentStore.getState().doc.objects.map((o) => o.id)
  beforeEach(() => {
    useDocumentStore.getState().silent((d) => {
      d.objects.push({ ...NOTE })
    })
    useSelectionStore.getState().clear()
  })

  it('排版里 ⌘A 全选、Backspace 整选区删除（对照组）', () => {
    modKey('a')
    expect(useSelectionStore.getState().ids).toEqual(['p1', 't1'])
    press('Backspace')
    expect(ids()).toEqual([])
  })

  it('快速编辑里 ⌘A 只选正在编辑的那张图', () => {
    useWorkspaceStore.getState().enterFastEdit(PANEL.id)
    modKey('a')
    expect(useSelectionStore.getState().ids).toEqual(['p1'])
  })

  it('快速编辑里那张图锁着：⌘A 什么都不选（不退到整版）', () => {
    useDocumentStore.getState().silent((d) => {
      d.objects[0].locked = true
    })
    useWorkspaceStore.getState().enterFastEdit(PANEL.id)
    modKey('a')
    expect(useSelectionStore.getState().ids).toEqual([])
  })

  it('快速编辑里 ⌘A 后 Backspace：看不见的版面对象一个不少，只删看得见的那张图', () => {
    useWorkspaceStore.getState().enterFastEdit(PANEL.id)
    modKey('a')
    press('Backspace')
    // 删那张图本身是既有语义（看得见、可撤销，对象没了快速编辑随之退出）；版上别的对象原样在
    expect(ids()).toEqual(['t1'])
    expect(useDocumentStore.getState().past).toHaveLength(1)
  })

  it('快速编辑里选区挂着进来之前的版面对象：Backspace 只删那张图', () => {
    useWorkspaceStore.getState().enterFastEdit(PANEL.id)
    useSelectionStore.getState().set(['p1', 't1'])
    press('Backspace')
    expect(ids()).toEqual(['t1'])
  })

  it('快速编辑里选区只有看不见的对象：Backspace 什么都不删、不进历史', () => {
    useWorkspaceStore.getState().enterFastEdit(PANEL.id)
    useSelectionStore.getState().set(['t1'])
    press('Backspace')
    expect(ids()).toEqual(['p1', 't1'])
    expect(useDocumentStore.getState().past).toHaveLength(0)
  })
})

/**
 * ⌘1「适应」与舞台双击同一个取景框（`stageFitFrame`，Codex #833）：快速编辑里适应那张图（右下角当框），
 * 不是适应这一屏根本没画的页面。对照组：排版里适应页面。
 */
describe('快速编辑里的 ⌘1', () => {
  const cmd1 = () =>
    act(() => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: '1', ctrlKey: true, bubbles: true, cancelable: true }))
    })

  it('排版里 ⌘1 适应页面（对照组）', () => {
    const spy = vi.spyOn(useViewportStore.getState(), 'fitAnimated').mockImplementation(() => {})
    cmd1()
    expect(spy).toHaveBeenCalledWith(150, 100)
  })

  it('快速编辑里 ⌘1 适应正在编辑的那张图', () => {
    const spy = vi.spyOn(useViewportStore.getState(), 'fitAnimated').mockImplementation(() => {})
    useWorkspaceStore.getState().enterFastEdit(PANEL.id)
    cmd1()
    expect(spy).toHaveBeenCalledTimes(1)
    // PANEL x10 y20 w40 h30 → 右下角 (50, 50)
    expect(spy).toHaveBeenCalledWith(50, 50)
  })
})
