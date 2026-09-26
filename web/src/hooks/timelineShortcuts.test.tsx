/**
 * 排版时间线的快捷键与命令面板入口（ADR 0101）。
 *
 * * ⇧⌘H 开 / 关时间线——⌥⌘H 是 macOS「隐藏其他」，⇧⌘Y 会落进 ⌘Y 的重做分支；
 * * ⌥⌘S 打开时间线并把焦点送进名字框——必须排在 ⌘S 前面：Windows 上 Ctrl+Alt+S
 *   的 `key` 仍是 s，落进 ⌘S 那条就成了「保存」；macOS 上 ⌥ 把 `key` 变成 ß；
 * * Esc 逐层退：先退预览，再关抽屉。
 *
 * 每条配反向对照（⇧⌘Y 仍是重做、⌘S 仍是保存），否则「什么都没发生」也可能是
 * 判据自己没执行到。
 */
import { createElement } from 'react'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/store/actions', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/store/actions')>()),
  runManualSave: vi.fn(async () => {}),
}))

import { useKeyboard } from './useKeyboard'
import { runManualSave } from '@/store/actions'
import { useDocumentStore } from '@/store/documentStore'
import { useTimelineStore } from '@/store/timelineStore'
import { useUiStore } from '@/store/uiStore'
import { emptyProject } from '@/types/document'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

let root: Root | null = null

function Harness() {
  useKeyboard()
  return null
}

const press = (init: KeyboardEventInit) => {
  act(() => {
    window.dispatchEvent(new KeyboardEvent('keydown', { bubbles: true, cancelable: true, ...init }))
  })
}

beforeEach(async () => {
  vi.clearAllMocks()
  useUiStore.setState({ versionsOpen: false })
  useTimelineStore.setState({ preview: null, focusNameRequest: 0 })
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_keys')
  const el = document.createElement('div')
  document.body.appendChild(el)
  root = createRoot(el)
  act(() => root!.render(createElement(Harness)))
})

afterEach(() => {
  act(() => root?.unmount())
  root = null
})

describe('⇧⌘H：开 / 关排版时间线', () => {
  it('按一次开，再按一次关', () => {
    press({ key: 'H', code: 'KeyH', metaKey: true, shiftKey: true })
    expect(useUiStore.getState().versionsOpen).toBe(true)
    press({ key: 'H', code: 'KeyH', metaKey: true, shiftKey: true })
    expect(useUiStore.getState().versionsOpen).toBe(false)
  })

  it('Windows / Linux 的 Ctrl+Shift+H 同一条', () => {
    press({ key: 'H', code: 'KeyH', ctrlKey: true, shiftKey: true })
    expect(useUiStore.getState().versionsOpen).toBe(true)
  })

  it('对照：⌥⌘H 不碰时间线（那是系统的「隐藏其他」）', () => {
    press({ key: '˙', code: 'KeyH', metaKey: true, altKey: true })
    expect(useUiStore.getState().versionsOpen).toBe(false)
  })

  it('对照：⇧⌘Y 仍是重做，不开时间线', () => {
    const s = useDocumentStore.getState()
    s.commit({ key: 'x', ns: 'workspace' }, (d) => {
      d.guides.push({ axis: 'x', pos: 1 })
    })
    s.undo()
    expect(useDocumentStore.getState().doc.guides).toHaveLength(0)
    press({ key: 'Y', code: 'KeyY', metaKey: true, shiftKey: true })
    expect(useUiStore.getState().versionsOpen).toBe(false)
    expect(useDocumentStore.getState().doc.guides).toHaveLength(1)
  })
})

describe('⌥⌘S：把现在存为命名节点', () => {
  it('macOS：key 是 ß，按 code 认出来；打开时间线并请求名字框焦点，不触发保存', () => {
    press({ key: 'ß', code: 'KeyS', metaKey: true, altKey: true })
    expect(useUiStore.getState().versionsOpen).toBe(true)
    expect(useTimelineStore.getState().focusNameRequest).toBe(1)
    expect(runManualSave).not.toHaveBeenCalled()
  })

  it('Windows：Ctrl+Alt+S 的 key 仍是 s——不能落进 ⌘S 变成保存', () => {
    press({ key: 's', code: 'KeyS', ctrlKey: true, altKey: true })
    expect(useUiStore.getState().versionsOpen).toBe(true)
    expect(runManualSave).not.toHaveBeenCalled()
  })

  it('对照：⌘S 仍是保存', () => {
    press({ key: 's', code: 'KeyS', metaKey: true })
    expect(runManualSave).toHaveBeenCalledTimes(1)
    expect(useUiStore.getState().versionsOpen).toBe(false)
  })
})

describe('Esc：先退预览，再关抽屉', () => {
  it('预览中按 Esc 只退预览；再按一次才关抽屉', () => {
    useUiStore.setState({ versionsOpen: true })
    useTimelineStore.setState({
      preview: {
        docId: 'd_keys',
        meta: { id: 'v1', name: '', ts: 1, auto: true, description: '', objects: 0 },
        doc: null,
      },
    })
    press({ key: 'Escape', code: 'Escape' })
    expect(useTimelineStore.getState().preview).toBeNull()
    expect(useUiStore.getState().versionsOpen).toBe(true)
    press({ key: 'Escape', code: 'Escape' })
    expect(useUiStore.getState().versionsOpen).toBe(false)
  })
})
