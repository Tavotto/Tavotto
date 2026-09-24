/**
 * 画布快捷键**不许抢文本编辑**（QA 2026-09-24 REL-04：「快捷键不误抢文本编辑」）。
 *
 * `useKeyboard` 挂在 window 上：焦点在输入框里按 Backspace / ⌘Z / 方向键 / 空格 /
 * 工具字母 / ⌘A，事件照样冒泡到它。`inEditableTarget()` 是唯一的闸——它要是漏了，
 * 用户在属性栏里删一个字符，画布上选中的整张图就没了；在输入框里 ⌘Z 撤掉的是
 * 文档历史而不是刚打的字。已有用例只钉了「⌘S 在输入框里**照样**拦」（反方向那条），
 * 这里钉正方向：其余快捷键在 INPUT / TEXTAREA / SELECT 里一律让位给原生行为——
 * 文档不变、选区不变、工具不变、**不 preventDefault**（否则原生编辑也被吃掉）。
 *
 * 每条都配一个**对照**：同一个键派在 window 上时必须真的生效——否则「什么都没发生」
 * 也可能是判据自己没执行到（harness 没挂上、store 没摆好）。
 *
 * 盲点（写在明处）：jsdom 不实现 `isContentEditable`，contentEditable 那一支在这里
 * 量不到；真浏览器里的富文本编辑框由 E2E 负责。
 */
import { createElement } from 'react'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { literal } from '@/i18n'
import { useKeyboard } from './useKeyboard'
import { useDocumentStore } from '@/store/documentStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { useViewportStore } from '@/store/viewportStore'
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
  return createElement(
    'div',
    null,
    createElement('input', { 'aria-label': 'field' }),
    createElement('textarea', { 'aria-label': 'area' }),
    createElement(
      'select',
      { 'aria-label': 'choice' },
      createElement('option', { value: 'a' }, 'a'),
    ),
  )
}

const doc = () => useDocumentStore.getState()
const panel = () => doc().doc.objects.find((o) => o.id === PANEL.id) as PanelObject | undefined

const press = (target: EventTarget, init: KeyboardEventInit) => {
  const ev = new KeyboardEvent('keydown', { bubbles: true, cancelable: true, ...init })
  act(() => {
    target.dispatchEvent(ev)
  })
  return ev
}

const field = (sel: 'input' | 'textarea' | 'select') => {
  const el = document.querySelector(sel) as HTMLElement
  el.focus()
  return el
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
  useUiStore.setState({ tool: 'select', elementPanelId: null, selectedGids: [] })
  useViewportStore.getState().setSpaceDown(false)
  useWorkspaceStore.getState().clear()
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
  useViewportStore.getState().setSpaceDown(false)
})

describe.each(['input', 'textarea', 'select'] as const)('焦点在 <%s> 里', (sel) => {
  it.each(['Backspace', 'Delete'])('%s 删的是字符，不是画布上选中的面板', (key) => {
    const ev = press(field(sel), { key })
    expect(panel()).toBeDefined()
    expect(doc().past).toHaveLength(0)
    expect(ev.defaultPrevented).toBe(false)
  })

  it('⌘Z / Ctrl+Z / ⌘⇧Z 不动文档历史（让给原生文本撤销）', () => {
    act(() => {
      doc().commit(literal('move'), (d) => {
        const p = d.objects[0] as PanelObject
        p.x = 99
      })
    })
    expect(panel()!.x).toBe(99)
    const el = field(sel)
    const a = press(el, { key: 'z', metaKey: true })
    const b = press(el, { key: 'z', ctrlKey: true })
    const c = press(el, { key: 'z', metaKey: true, shiftKey: true })
    expect(panel()!.x).toBe(99)
    expect(doc().past).toHaveLength(1)
    expect([a, b, c].some((e) => e.defaultPrevented)).toBe(false)
  })

  it('方向键移的是光标，不是面板', () => {
    const el = field(sel)
    for (const key of ['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown']) {
      expect(press(el, { key }).defaultPrevented).toBe(false)
      press(el, { key, shiftKey: true })
    }
    expect([panel()!.x, panel()!.y]).toEqual([PANEL.x, PANEL.y])
  })

  it('工具字母是在打字，不切工具', () => {
    const el = field(sel)
    for (const key of ['t', 'a', 'r', 'o', 'l', 'R']) {
      expect(press(el, { key }).defaultPrevented).toBe(false)
    }
    expect(useUiStore.getState().tool).toBe('select')
  })

  it('空格是空格，不进平移态', () => {
    const ev = press(field(sel), { key: ' ', code: 'Space' })
    expect(useViewportStore.getState().spaceDown).toBe(false)
    expect(ev.defaultPrevented).toBe(false)
  })

  it('⌘A 全选文字，不全选画布；⌘D 不复制面板', () => {
    useSelectionStore.getState().clear()
    const el = field(sel)
    const a = press(el, { key: 'a', metaKey: true })
    const d = press(el, { key: 'd', metaKey: true })
    expect(useSelectionStore.getState().ids).toEqual([])
    expect(doc().doc.objects).toHaveLength(1)
    expect(a.defaultPrevented || d.defaultPrevented).toBe(false)
  })
})

describe('对照：同样的键派在 window 上，画布快捷键照常生效', () => {
  it('Backspace 删掉选中的面板', () => {
    const ev = press(window, { key: 'Backspace' })
    expect(panel()).toBeUndefined()
    expect(ev.defaultPrevented).toBe(true)
  })

  it('⌘Z 撤掉上一步', () => {
    act(() => {
      doc().commit(literal('move'), (d) => {
        const p = d.objects[0] as PanelObject
        p.x = 99
      })
    })
    press(window, { key: 'z', metaKey: true })
    expect(panel()!.x).toBe(PANEL.x)
  })

  it('方向键推动面板', () => {
    press(window, { key: 'ArrowRight' })
    expect(panel()!.x).toBeCloseTo(PANEL.x + 0.5)
  })

  it('R 切到矩形工具', () => {
    press(window, { key: 'r' })
    expect(useUiStore.getState().tool).toBe('rect')
  })

  it('空格进平移态', () => {
    press(window, { key: ' ', code: 'Space' })
    expect(useViewportStore.getState().spaceDown).toBe(true)
  })

  it('⌘A 全选画布对象', () => {
    useSelectionStore.getState().clear()
    press(window, { key: 'a', metaKey: true })
    expect(useSelectionStore.getState().ids).toEqual([PANEL.id])
  })
})
