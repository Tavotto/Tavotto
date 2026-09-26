/**
 * 系统菜单的转发（`runMenuAction`）与键盘是**同一条路**。
 *
 * 菜单加速键可能先于 webview 的 keydown 截获按键（Windows 上一定如此；macOS 取决于
 * WKWebView 先不先把键交给页面）。于是同一个 ⌘D，有的机器走 `useKeyboard`、有的
 * 机器走菜单转发——两条路的让位判断只要差一点，就是「⌘D 在输入框里变成了创建副本」
 * 这种只在一个平台上发作的坏法。这里逐个加速键比：同一个焦点位置，按键和菜单
 * 转发落下的效果必须一模一样；画布上那一格必须**真的有效果**（对照：否则两边
 * 「都没反应」也会相等）。
 *
 * ⌘E（导出）与 ⌘S（保存）不在比较表里：⌘S 两条路都「在输入框里也照存」，⌘E 的
 * 菜单一直是「项目开着就开导出框」——这是这次改动之前就有的形状，不在本文件的主张里。
 */
import { createElement } from 'react'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { literal } from '@/i18n'
import { MENU_ACTIONS, type MenuAction } from '@/lib/desktop'
import { useArrangeStore } from '@/store/arrangeStore'
import { useDocumentStore } from '@/store/documentStore'
import { useProjectStore } from '@/store/projectStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { useViewportStore } from '@/store/viewportStore'
import { useWorkspaceStore } from '@/store/workspace'
import { canvasToDoc } from '@/types/document'
import type { CanvasData, PanelObject } from '@/types/document'
import { runMenuAction } from './menuActions'
import { useKeyboard } from './useKeyboard'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const panel = (id: string, x: number, y = 20): PanelObject => ({
  id,
  type: 'panel',
  fileId: `${id}.pdf`,
  fileKind: 'pdf',
  nativeW: 80,
  nativeH: 60,
  overrides: [],
  x,
  y,
  w: 40,
  h: 30,
})

let root: Root | null = null
const zoomBy = vi.fn()
const setZoomCentered = vi.fn()
const fitAnimated = vi.fn()

function Harness() {
  useKeyboard()
  return createElement(
    'div',
    null,
    createElement('input', { 'aria-label': 'field' }),
    createElement('div', { role: 'dialog' }, createElement('button', null, 'ok')),
  )
}

const doc = () => useDocumentStore.getState()

function seed() {
  const canvas: CanvasData = {
    id: 'c1',
    name: 'Fig 1',
    page: { w: 300, h: 200 },
    objects: [panel('p1', 10), panel('p2', 120, 60)],
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
  useSelectionStore.getState().set(['p1', 'p2'])
  useUiStore.setState({
    tool: 'select',
    elementPanelId: null,
    selectedGids: [],
    status: null,
    settingsOpen: false,
    shortcutHelpOpen: false,
  })
  zoomBy.mockClear()
  setZoomCentered.mockClear()
  fitAnimated.mockClear()
}

/** 两条路都会碰到的全部可观察效果 */
const observe = () => ({
  objects: doc().doc.objects.length,
  history: doc().past.length,
  zoomBy: zoomBy.mock.calls.map((c) => c[0]),
  zoomCentered: setZoomCentered.mock.calls.map((c) => c[0]),
  fit: fitAnimated.mock.calls.length,
})

type Where = 'input' | 'dialog' | 'canvas'
const focusAt = (where: Where): HTMLElement => {
  const el =
    where === 'input'
      ? (document.querySelector('input') as HTMLElement)
      : where === 'dialog'
        ? (document.querySelector('[role=dialog] button') as HTMLElement)
        : document.body
  if (where === 'canvas') (document.activeElement as HTMLElement | null)?.blur()
  else el.focus()
  return el
}

beforeEach(() => {
  useProjectStore.setState({ phase: 'open' })
  useViewportStore.setState({ zoomBy, setZoomCentered, fitAnimated })
  useWorkspaceStore.getState().clear()
  useArrangeStore.setState({ alignRef: 'selection' })
  seed()
  const el = document.createElement('div')
  document.body.appendChild(el)
  root = createRoot(el)
  act(() => {
    root!.render(createElement(Harness))
  })
})

afterEach(() => {
  act(() => root?.unmount())
  root = null
  document.body.innerHTML = ''
  useWorkspaceStore.getState().clear()
})

/** 挂了加速键、且前端 keydown 本来就认的那几条 */
const ACCELERATED: [MenuAction, KeyboardEventInit][] = [
  ['menu-duplicate', { key: 'd', metaKey: true }],
  ['menu-zoom-in', { key: '=', metaKey: true }],
  ['menu-zoom-out', { key: '-', metaKey: true }],
  ['menu-zoom-actual', { key: '0', metaKey: true }],
  ['menu-zoom-fit', { key: '1', metaKey: true }],
]

describe.each(['canvas', 'input', 'dialog'] as const)('焦点在 %s', (where) => {
  it.each(ACCELERATED)('%s 与按键落下的效果相同', (action, key) => {
    const target = focusAt(where)
    act(() => {
      target.dispatchEvent(new KeyboardEvent('keydown', { bubbles: true, cancelable: true, ...key }))
    })
    const byKey = observe()

    seed()
    focusAt(where)
    act(() => runMenuAction(action))
    const byMenu = observe()

    expect(byMenu).toEqual(byKey)
    // 对照：画布上那一格必须真的有效果，否则两边「都没反应」也相等
    const untouched = { objects: 2, history: 0, zoomBy: [], zoomCentered: [], fit: 0 }
    if (where === 'canvas') expect(byMenu).not.toEqual(untouched)
    else expect(byMenu).toEqual(untouched)
  })
})

describe('不带加速键的几条', () => {
  it('删除：画布上删对象，输入框里交给原生删字', () => {
    const exec = vi.fn(() => true)
    Object.defineProperty(document, 'execCommand', { value: exec, configurable: true })

    focusAt('input')
    act(() => runMenuAction('menu-delete'))
    expect(exec).toHaveBeenCalledWith('delete')
    expect(doc().doc.objects).toHaveLength(2)

    focusAt('canvas')
    act(() => runMenuAction('menu-delete'))
    expect(doc().doc.objects).toHaveLength(0)
  })

  it('对齐按 arrangeStore 的参照落到 alignSelectedTo：一条历史', () => {
    useArrangeStore.setState({ alignRef: 'page' })
    act(() => runMenuAction('menu-align-right'))
    const xs = doc().doc.objects.map((o) => o.x + o.w)
    expect(xs).toEqual([300, 300])
    expect(doc().past).toHaveLength(1)
  })

  it('没选东西时对齐要说出口，而不是点了没反应', () => {
    useSelectionStore.getState().set([])
    act(() => runMenuAction('menu-align-left'))
    expect(doc().past).toHaveLength(0)
    expect(useUiStore.getState().status).not.toBeNull()
    expect(useUiStore.getState().statusTone).toBe('error')
  })

  it('每一个 menu-align-* 的后缀都是 alignSelectedTo 认得的模式（落下一条历史）', () => {
    for (const a of MENU_ACTIONS.filter((x) => x.startsWith('menu-align-'))) {
      seed()
      // 等距要 ≥3 个对象：补一个
      act(() => {
        doc().commit(literal('third'), (d) => {
          d.objects.push(panel('p3', 250, 140))
        })
      })
      useSelectionStore.getState().set(['p1', 'p2', 'p3'])
      const before = doc().past.length
      act(() => runMenuAction(a))
      expect(doc().past.length, a).toBe(before + 1)
    }
  })

  it('项目没开时只认「打开项目」与撤销/重做', () => {
    useProjectStore.setState({ phase: 'none' })
    for (const a of MENU_ACTIONS) {
      if (a === 'menu-open-project' || a === 'menu-undo' || a === 'menu-redo') continue
      act(() => runMenuAction(a))
    }
    expect(useUiStore.getState().settingsOpen).toBe(false)
    expect(useUiStore.getState().shortcutHelpOpen).toBe(false)
    expect(observe()).toEqual({ objects: 2, history: 0, zoomBy: [], zoomCentered: [], fit: 0 })

    const exec = vi.fn(() => true)
    Object.defineProperty(document, 'execCommand', { value: exec, configurable: true })
    focusAt('input')
    act(() => runMenuAction('menu-undo'))
    expect(exec).toHaveBeenCalledWith('undo')
  })

  it('设置 / 快捷键帮助 / 左右栏落到 uiStore 既有的 action', () => {
    act(() => runMenuAction('menu-settings'))
    expect(useUiStore.getState().settingsOpen).toBe(true)
    act(() => runMenuAction('menu-diagnostics'))
    expect(useUiStore.getState().settingsSection).toBe('diagnostics')
    act(() => runMenuAction('menu-shortcut-help'))
    expect(useUiStore.getState().shortcutHelpOpen).toBe(true)
    const left = useUiStore.getState().leftOpen
    act(() => runMenuAction('menu-toggle-left'))
    expect(useUiStore.getState().leftOpen).toBe(!left)
  })
})
