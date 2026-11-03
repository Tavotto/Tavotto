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

describe('单图编辑里的版面快捷键', () => {
  it('方向键在排版模式下照常推动面板（对照组）', () => {
    press('ArrowRight')
    expect(panel().x).toBeCloseTo(10.5)
  })

  it('方向键在单图编辑里与排版同口径（2026-10-21：单图编辑的舞台就是这张画布，推选中的对象）', () => {
    useWorkspaceStore.getState().enterFastEdit(PANEL.id)
    useSelectionStore.getState().set([PANEL.id])
    press('ArrowRight')
    press('ArrowDown')
    expect(panel().x).toBeCloseTo(10.5)
    expect(panel().y).toBeCloseTo(20.5)
  })

  it('绘制工具快捷键在排版模式下照常切换（对照组）', () => {
    press('r')
    expect(useUiStore.getState().tool).toBe('rect')
  })

  it('绘制工具快捷键在单图编辑里照常切换（标注在单图编辑里看得见、可画）', () => {
    useWorkspaceStore.getState().enterFastEdit(PANEL.id)
    press('r')
    expect(useUiStore.getState().tool).toBe('rect')
  })

  it('`V`（回到选择工具）在单图编辑里有效', () => {
    useUiStore.setState({ tool: 'rect' })
    useWorkspaceStore.getState().enterFastEdit(PANEL.id)
    press('v')
    expect(useUiStore.getState().tool).toBe('select')
  })
})

/**
 * ⇧2「缩放到选中」在单图编辑里与排版同口径（2026-10-21，取代 Codex #833 的「只认那张图」）：单图编辑的舞台画着
 * 这张画布上的全部对象，选区里的都看得见，框选区里没隐藏对象的并集。
 */
describe('单图编辑里的 ⇧2', () => {
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

  it('单图编辑里 ⌘A 后 ⇧2：框整个选区（图 ∪ 标注）', () => {
    const spy = vi.spyOn(useViewportStore.getState(), 'fitRectAnimated').mockImplementation(() => {})
    useWorkspaceStore.getState().enterFastEdit(PANEL.id)
    selectAllKey()
    shift2()
    expect(spy).toHaveBeenCalledTimes(1)
    expect(spy).toHaveBeenCalledWith({ x: 10, y: 20, w: 120, h: 68 })
  })

  it('单图编辑里只选标注：⇧2 框标注', () => {
    const spy = vi.spyOn(useViewportStore.getState(), 'fitRectAnimated').mockImplementation(() => {})
    useWorkspaceStore.getState().enterFastEdit(PANEL.id)
    useSelectionStore.getState().set([NOTE.id])
    shift2()
    expect(spy).toHaveBeenCalledWith({ x: 100, y: 80, w: 30, h: 8 })
  })
})

/**
 * 单图编辑里的 ⌘A / Delete（2026-10-21，取代 Codex #833 的「只收 / 只删那张图」）：单图编辑的舞台画着这张画布上的全部
 * 对象，选区里的都看得见，所以 ⌘A 与删除和排版同口径——全选看得见且没锁的对象，删除删选区。
 * 对照组：排版里同样。
 */
describe('单图编辑里的 ⌘A / Delete', () => {
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

  it('单图编辑里 ⌘A 选这张画布上全部看得见的对象', () => {
    useWorkspaceStore.getState().enterFastEdit(PANEL.id)
    modKey('a')
    expect(useSelectionStore.getState().ids).toEqual(['p1', 't1'])
  })

  it('单图编辑里锁着的图不被 ⌘A 选上，标注照选', () => {
    useDocumentStore.getState().silent((d) => {
      d.objects[0].locked = true
    })
    useWorkspaceStore.getState().enterFastEdit(PANEL.id)
    modKey('a')
    expect(useSelectionStore.getState().ids).toEqual(['t1'])
  })

  it('单图编辑里选中标注按 Backspace：只删标注，图原样在', () => {
    useWorkspaceStore.getState().enterFastEdit(PANEL.id)
    useSelectionStore.getState().set(['t1'])
    press('Backspace')
    expect(ids()).toEqual(['p1'])
    expect(useDocumentStore.getState().past).toHaveLength(1)
  })

  it('单图编辑里 ⌘A 后 Backspace：整选区删除（与排版一致，可撤销）', () => {
    useWorkspaceStore.getState().enterFastEdit(PANEL.id)
    modKey('a')
    press('Backspace')
    expect(ids()).toEqual([])
    expect(useDocumentStore.getState().past).toHaveLength(1)
  })
})

/**
 * ⌘1「适应」与舞台双击同一个取景框（`stageFitFrame`，Codex #833）：快速编辑里适应那张图本身的矩形（含原点，
 * `fitRectAnimated`；视口终点的几何见 `canvas/stageFitRect.test.tsx`），
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

  it('快速编辑里 ⌘1 适应正在编辑的那张图（它自己的矩形，不是 (0,0) 到右下角）', () => {
    const page = vi.spyOn(useViewportStore.getState(), 'fitAnimated').mockImplementation(() => {})
    const spy = vi.spyOn(useViewportStore.getState(), 'fitRectAnimated').mockImplementation(() => {})
    useWorkspaceStore.getState().enterFastEdit(PANEL.id)
    cmd1()
    expect(page).not.toHaveBeenCalled()
    expect(spy).toHaveBeenCalledTimes(1)
    expect(spy).toHaveBeenCalledWith({ x: 10, y: 20, w: 40, h: 30 })
  })
})

describe('单图编辑里的 Esc：只退图内编辑，没有「排版」可回（模式由图数推出）', () => {
  const enter = () => useWorkspaceStore.getState().enterFastEdit(PANEL.id)
  const mode = () => useWorkspaceStore.getState().mode

  it('对照组：排版里 Esc 只是清选区，不碰模式', () => {
    press('Escape')
    expect(useSelectionStore.getState().ids).toEqual([])
    expect(mode()).toBe('layout')
  })

  it('单图编辑里没有更内层时 Esc 清选区（标注可选了），仍是单图编辑', () => {
    enter()
    useSelectionStore.getState().set([PANEL.id])
    press('Escape')
    expect(mode()).toBe('fast_edit')
    expect(useSelectionStore.getState().ids).toEqual([])
  })

  it('图内编辑有选区：第一下 Esc 只清选区；再一下退出图内编辑并选中该面板，模式不变', () => {
    enter()
    useUiStore.getState().setElementPanel(PANEL.id)
    useUiStore.getState().setSelectedGid('axes_0.yticks')
    press('Escape')
    expect(useUiStore.getState().selectedGids).toEqual([])
    expect(useUiStore.getState().elementPanelId).toBe(PANEL.id)
    press('Escape')
    expect(mode()).toBe('fast_edit')
    expect(useUiStore.getState().elementPanelId).toBeNull()
    expect(useSelectionStore.getState().ids).toEqual([PANEL.id])
  })

  it('输入框聚焦时不抢 Esc', () => {
    enter()
    const input = document.createElement('input')
    document.body.appendChild(input)
    input.focus()
    act(() => {
      input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }))
    })
    expect(mode()).toBe('fast_edit')
  })

  it('对话框打开时不抢 Esc（对话框自己关）', () => {
    enter()
    const dlg = document.createElement('div')
    dlg.setAttribute('role', 'dialog')
    const btn = document.createElement('button')
    dlg.appendChild(btn)
    document.body.appendChild(dlg)
    btn.focus()
    act(() => {
      btn.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }))
    })
    expect(mode()).toBe('fast_edit')
  })
})
