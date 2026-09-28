/**
 * 离散动作闸门的结构性看护（`runDiscreteAction`，Codex #671 同一家族第三次之后收成结构）。
 *
 * 不经 keydown / 指针按下的离散入口——系统菜单的**每一个** action id、原生 copy / paste
 * 事件——逐个在两种前提下调用：
 *
 *   A. 方向键微调这一段开着、焦点在画布上：每一个入口都先把这一段落定（不再开着、事务已结），
 *      会写文档的那几个另起一条撤销，不并进移动那一条；
 *   B. 属性字段的连续编辑开着、焦点在输入框里：画布动作（下面 `YIELDS` 那几条，独立写在这里，
 *      不读 `MENU_SCOPE`——两侧同源等于自己验自己）让位、**不动**这轮编辑；应用级动作照常先收。
 *
 * 新增一个菜单项而不经闸门：A 里它不收微调 → 红。
 */
import { act, createElement } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { CLIPBOARD_FORMAT } from '@/lib/brand'
import { MENU_ACTIONS, type MenuAction } from '@/lib/desktop'
import { nudgeActive, resetNudge } from '@/canvas/nudge'
import { useDocumentStore } from '@/store/documentStore'
import { registerGesture, resetGestureCoordinator } from '@/store/gestureCoordinator'
import { useInteractionStore } from '@/store/interactionStore'
import { useProjectStore } from '@/store/projectStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { useUpdateStore } from '@/store/updateStore'
import { useViewportStore } from '@/store/viewportStore'
import { useWorkspaceStore } from '@/store/workspace'
import { canvasToDoc, type CanvasData, type ShapeObject, type TextObject } from '@/types/document'
import { runMenuAction } from './menuActions'
import { useKeyboard } from './useKeyboard'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

// 保存走真盘会碰网络；换成桩也让用例更严：它自己不再替闸门收手势
vi.mock('@/store/actions', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/store/actions')>()),
  runManualSave: vi.fn(async () => {}),
}))

/** 焦点在输入框 / 对话框里时该让位的菜单项（与 keydown 在那里 return 的几条一一对应） */
const YIELDS: ReadonlySet<MenuAction> = new Set([
  'menu-undo',
  'menu-redo',
  'menu-duplicate',
  'menu-delete',
  'menu-zoom-in',
  'menu-zoom-out',
  'menu-zoom-actual',
  'menu-zoom-fit',
])

/** 会往文档里写一条历史的：它们那一条必须与微调那条分开 */
const WRITES: ReadonlySet<MenuAction> = new Set([
  'menu-duplicate',
  'menu-delete',
  ...MENU_ACTIONS.filter((a) => a.startsWith('menu-align-')),
])

const rect = (id: string, x: number, y: number): ShapeObject =>
  ({
    id,
    type: 'shape',
    shape: 'rect',
    x,
    y,
    w: 20,
    h: 10,
    stroke: '#000',
    strokeWidth: 0.5,
    fill: null,
  }) as unknown as ShapeObject

const IDS = ['a', 'b', 'c']
const ORIGIN: Record<string, [number, number]> = { a: [10, 10], b: [50, 40], c: [120, 90] }

let root: Root | null = null
const doc = () => useDocumentStore.getState()
const xOf = (id: string) => doc().doc.objects.find((o) => o.id === id)?.x

function Harness() {
  useKeyboard()
  return createElement('input', { 'aria-label': 'field' })
}

function seed() {
  const canvas: CanvasData = {
    id: 'c1',
    name: 'Fig 1',
    page: { w: 300, h: 200 },
    objects: IDS.map((id) => rect(id, ...ORIGIN[id])),
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
  useSelectionStore.getState().set([...IDS])
  useUiStore.setState({ tool: 'select', elementPanelId: null, selectedGids: [], status: null })
}

function tapRight() {
  act(() => {
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true, cancelable: true }))
    window.dispatchEvent(new KeyboardEvent('keyup', { key: 'ArrowRight', bubbles: true }))
  })
}

const focusCanvas = () => (document.activeElement as HTMLElement | null)?.blur()
const input = () => document.querySelector('input') as HTMLInputElement

/** 一次原生剪贴板事件（document 上的监听认 `e.target`） */
function clipboardEvent(type: 'copy' | 'paste', target: EventTarget, text = '') {
  const ev = new Event(type, { bubbles: true, cancelable: true })
  Object.defineProperty(ev, 'clipboardData', { value: { getData: () => text, setData: vi.fn() } })
  act(() => {
    target.dispatchEvent(ev)
  })
}

const pastePayload = () =>
  JSON.stringify({
    magic: CLIPBOARD_FORMAT,
    sourceDocId: 'd_elsewhere',
    objects: [
      {
        id: 'o1',
        type: 'text',
        text: 'hi',
        sizePt: 9,
        bold: false,
        color: '#000',
        align: 'left',
        x: 200,
        y: 150,
        w: 20,
        h: 8,
      } satisfies TextObject,
    ],
    layoutGroups: [],
  })

/** 前提 A 的公共断言：微调这一段已经落定成**自己那一条**撤销 */
function expectNudgeSettledAlone(label: string) {
  expect(nudgeActive(), label).toBe(false)
  expect(doc().txn, label).toBeNull()
  // 把微调之后的历史撤到只剩第一条：留下的必须恰好是「三个对象右移 0.5 mm」
  const extra = doc().past.length - 1
  expect(extra, label).toBeGreaterThanOrEqual(0)
  act(() => {
    for (let i = 0; i < extra; i++) doc().undo()
  })
  expect(doc().doc.objects.map((o) => o.id).sort(), label).toEqual(IDS)
  for (const id of IDS) expect(xOf(id), `${label} ${id}`).toBeCloseTo(ORIGIN[id][0] + 0.5, 9)
}

beforeEach(() => {
  resetNudge()
  resetGestureCoordinator()
  useProjectStore.setState({ phase: 'open', showPicker: vi.fn() } as never)
  useUpdateStore.setState({ checkDesktop: vi.fn(async () => {}) } as never)
  useViewportStore.setState({ zoomBy: vi.fn(), setZoomCentered: vi.fn(), fitAnimated: vi.fn() } as never)
  useWorkspaceStore.getState().clear()
  useInteractionStore.getState().end()
  seed()
  const el = document.createElement('div')
  document.body.appendChild(el)
  root = createRoot(el)
  act(() => root!.render(createElement(Harness)))
  Object.defineProperty(document, 'execCommand', { value: vi.fn(() => true), configurable: true })
})

afterEach(() => {
  resetNudge()
  resetGestureCoordinator()
  act(() => root?.unmount())
  root = null
  document.body.innerHTML = ''
})

describe('前提 A：方向键微调这一段开着，焦点在画布', () => {
  it.each(MENU_ACTIONS.filter((a) => a !== 'menu-undo' && a !== 'menu-redo'))(
    '%s 先把这一段落定，写文档的另起一条撤销',
    (action) => {
      focusCanvas()
      tapRight()
      expect(nudgeActive()).toBe(true)
      const before = doc().past.length
      act(() => runMenuAction(action))
      if (WRITES.has(action)) expect(doc().past.length, action).toBeGreaterThan(before)
      expectNudgeSettledAlone(action)
    },
  )

  it('menu-undo：先落定这一段、再撤掉它（不是撤掉更早的那一条）', () => {
    focusCanvas()
    tapRight()
    act(() => runMenuAction('menu-undo'))
    expect(nudgeActive()).toBe(false)
    for (const id of IDS) expect(xOf(id)).toBe(ORIGIN[id][0])
    expect(doc().future).toHaveLength(1)
  })

  it('menu-redo：先落定这一段（不并进之后的历史）', () => {
    focusCanvas()
    tapRight()
    act(() => runMenuAction('menu-redo'))
    expectNudgeSettledAlone('menu-redo')
  })

  it('paste 事件（桌面壳「编辑 → 粘贴」直达）：先落定这一段，粘贴另起一条撤销', () => {
    focusCanvas()
    tapRight()
    clipboardEvent('paste', document.body, pastePayload())
    expect(doc().doc.objects).toHaveLength(4)
    expect(doc().past).toHaveLength(2)
    expectNudgeSettledAlone('paste')
  })

  it('copy 事件：先落定这一段（复制到的是画面上那一版）', () => {
    focusCanvas()
    tapRight()
    clipboardEvent('copy', document.body)
    expectNudgeSettledAlone('copy')
  })
})

describe('前提 B：属性字段的连续编辑开着，焦点在输入框', () => {
  it.each(MENU_ACTIONS)('%s：画布动作让位不动这轮编辑，应用级动作先收', (action) => {
    const finish = vi.fn()
    registerGesture(finish)
    input().focus()
    act(() => runMenuAction(action))
    expect(finish).toHaveBeenCalledTimes(YIELDS.has(action) ? 0 : 1)
  })

  it.each(['copy', 'paste'] as const)('%s 事件在输入框里：让位，不动这轮编辑、不接管', (type) => {
    const finish = vi.fn()
    registerGesture(finish)
    input().focus()
    clipboardEvent(type, input(), pastePayload())
    expect(finish).not.toHaveBeenCalled()
    expect(doc().doc.objects).toHaveLength(3)
  })
})
