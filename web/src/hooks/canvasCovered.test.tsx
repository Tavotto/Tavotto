/**
 * 「数据」临时标签激活时画布被盖住（底下的画布与文档还在）：画布动作的让位判据（`yieldsCanvasShortcuts`，键盘 / 系统菜单 / 原生剪贴板共用）
 * 认「被盖住」，会改文档的入口函数（撤销重做 / 删除 / 创建副本——顶栏与命令面板也走它们）也读同一个标志，
 * 所以哪条路都改不了看不见的文档；临时标签关掉 / 切回画布后全部恢复。
 *
 * 过了一遍的入口：键盘（Delete / ⌘Z / ⌘D / ⌘A / 方向键 / ⌘V），系统菜单（撤销 / 重做 / 删除 / 创建副本 / 全选），
 * 原生剪贴板（copy / paste 负载与图片），顶栏撤销重做钮（`runUndoRedo`），命令面板（同样落在 `deleteSelection` / `duplicateSelected` / `runUndoRedo`）。
 */
import { createElement } from 'react'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { CLIPBOARD_FORMAT } from '@/lib/brand'
import { handleCopyEvent, handlePasteEvent } from '@/lib/clipboard'
import { useDataFigureStore } from '@/store/dataFigureStore'
import { useDocumentStore } from '@/store/documentStore'
import { deleteSelected } from '@/store/actions'
import { yieldsCanvasShortcuts, runDiscreteAction } from '@/store/gestureCoordinator'
import { useProjectStore } from '@/store/projectStore'
import { useSelectionStore } from '@/store/selectionStore'
import { canvasToDoc, type CanvasData, type PanelObject } from '@/types/document'
import { runMenuAction } from './menuActions'
import { runUndoRedo, useKeyboard } from './useKeyboard'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const panel = (id: string, x: number): PanelObject => ({ id, type: 'panel', fileId: `${id}.pdf`, fileKind: 'pdf', nativeW: 80, nativeH: 60, overrides: [], x, y: 20, w: 40, h: 30 })
const doc = () => useDocumentStore.getState()
const cover = (on: boolean) => act(() => useDataFigureStore.setState({ draftActive: on, draftOpen: on }))
const snap = () => ({ objects: doc().doc.objects.length, past: doc().past.length, future: doc().future.length })

let root: Root | null = null
function Harness() {
  useKeyboard()
  return createElement('div')
}

function seed() {
  const canvas: CanvasData = { id: 'c1', name: 'Fig 1', page: { w: 300, h: 200 }, objects: [panel('p1', 10), panel('p2', 120)], guides: [] }
  useDocumentStore.setState({ doc: canvasToDoc(canvas), canvases: [canvas], activeCanvasId: 'c1', openTabs: ['c1'], past: [], future: [], txn: null })
  useSelectionStore.getState().set(['p1'])
}

const key = (k: string, mod = false) =>
  act(() => {
    window.dispatchEvent(new KeyboardEvent('keydown', { key: k, ctrlKey: mod, metaKey: mod, bubbles: true, cancelable: true }))
  })

beforeEach(() => {
  useProjectStore.setState({ phase: 'open' })
  seed()
  const el = document.createElement('div')
  document.body.appendChild(el)
  root = createRoot(el)
  act(() => root!.render(createElement(Harness)))
})
afterEach(() => {
  cover(false)
  act(() => root?.unmount())
  document.body.innerHTML = ''
})

describe('画布被「数据」临时标签盖住', () => {
  it('共用判据：盖住时对任何目标都让位，runDiscreteAction 不执行；关掉后恢复', () => {
    expect(yieldsCanvasShortcuts(document.body)).toBe(false)
    cover(true)
    expect(yieldsCanvasShortcuts(document.body)).toBe(true)
    expect(yieldsCanvasShortcuts(null)).toBe(true)
    let ran = false
    runDiscreteAction('canvas', document.body, () => (ran = true))
    expect(ran).toBe(false)
    runDiscreteAction('app', document.body, () => (ran = true)) // 应用级动作（保存等）不受影响
    expect(ran).toBe(true)
    cover(false)
    expect(yieldsCanvasShortcuts(document.body)).toBe(false)
  })

  it('键盘：Delete / ⌘D / ⌘Z / ⌘A / 方向键都不改文档；关掉后 Delete 恢复', () => {
    // 先留一步历史，让 ⌘Z 有东西可撤
    act(() => { useSelectionStore.getState().set(['p2']); deleteSelected(); useSelectionStore.getState().set(['p1']) })
    const before = snap()
    cover(true)
    key('Delete')
    key('d', true)
    key('z', true)
    key('a', true)
    key('ArrowRight')
    expect(snap()).toEqual(before)
    expect(useSelectionStore.getState().ids).toEqual(['p1'])
    cover(false)
    key('Delete')
    expect(snap().objects).toBe(before.objects - 1)
  })

  it('系统菜单：撤销 / 重做 / 删除 / 创建副本 / 对齐都不改文档、不改选区；关掉后恢复', () => {
    act(() => { useSelectionStore.getState().set(['p2']); deleteSelected(); useSelectionStore.getState().set(['p1']) })
    const before = snap()
    cover(true)
    for (const a of ['menu-undo', 'menu-redo', 'menu-delete', 'menu-duplicate', 'menu-align-left', 'menu-align-hcenter', 'menu-zoom-in'] as const) act(() => runMenuAction(a))
    expect(snap()).toEqual(before)
    expect(useSelectionStore.getState().ids).toEqual(['p1'])
    cover(false)
    act(() => runMenuAction('menu-undo'))
    expect(snap().objects).toBe(before.objects + 1)
  })

  it('顶栏撤销重做钮 / 命令面板落在的入口（runUndoRedo）：盖住时不动，关掉后恢复', () => {
    act(() => { useSelectionStore.getState().set(['p2']); deleteSelected(); useSelectionStore.getState().set(['p1']) })
    const before = snap()
    cover(true)
    act(() => runUndoRedo(false))
    expect(snap()).toEqual(before)
    cover(false)
    act(() => runUndoRedo(false))
    expect(snap().objects).toBe(before.objects + 1)
  })

  it('原生剪贴板：盖住时 copy / paste（负载）都不接管、不改文档；关掉后恢复', () => {
    const payload = JSON.stringify({ magic: CLIPBOARD_FORMAT, sourceDocId: 'd_x', objects: [{ id: 'q1', type: 'text', text: 'hi', sizePt: 9, bold: false, color: '#000', align: 'left', x: 10, y: 10, w: 20, h: 8 }], layoutGroups: [] })
    const mk = (getData: (t: string) => string) => {
      let prevented = false
      const e = { target: document.body, clipboardData: { getData, setData: () => {} }, preventDefault: () => (prevented = true) } as unknown as ClipboardEvent
      return { e, prevented: () => prevented }
    }
    const before = snap()
    cover(true)
    const c = mk(() => '')
    expect(handleCopyEvent(c.e)).toBe(false)
    const p = mk(() => payload)
    expect(handlePasteEvent(p.e)).toBe(false)
    expect(p.prevented()).toBe(false)
    expect(snap()).toEqual(before)
    cover(false)
    const p2 = mk(() => payload)
    expect(handlePasteEvent(p2.e)).toBe(true)
    expect(snap().objects).toBeGreaterThan(before.objects)
  })
})
