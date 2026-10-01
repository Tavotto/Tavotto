/**
 * 排版时间线的快捷键与命令面板入口（ADR 0101）。
 *
 * * ⇧⌘H 开 / 关时间线——⌥⌘H 是 macOS「隐藏其他」，⇧⌘Y 会落进 ⌘Y 的重做分支；
 * * ⌥⌘S 打开顶部的命名小框（不开抽屉）——必须排在 ⌘S 前面：Windows 上 Ctrl+Alt+S
 *   的 `key` 仍是 s，落进 ⌘S 那条就成了「保存」；macOS 上 ⌥ 把 `key` 变成 ß；
 *   在输入框里让位（与其余快捷键同一条 `yieldsCanvasShortcuts`），Windows 的 AltGr
 *   （报成 Ctrl+Alt）是在打字，不算（Codex #679）；
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
  useTimelineStore.setState({ preview: null, namingOpen: false })
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
  it('macOS：key 是 ß，按 code 认出来；打开顶部命名小框（不开抽屉），不触发保存', () => {
    press({ key: 'ß', code: 'KeyS', metaKey: true, altKey: true })
    expect(useTimelineStore.getState().namingOpen).toBe(true)
    expect(useUiStore.getState().versionsOpen).toBe(false)
    expect(runManualSave).not.toHaveBeenCalled()
  })

  it('Windows：Ctrl+Alt+S 的 key 仍是 s——不能落进 ⌘S 变成保存', () => {
    press({ key: 's', code: 'KeyS', ctrlKey: true, altKey: true })
    expect(useTimelineStore.getState().namingOpen).toBe(true)
    expect(runManualSave).not.toHaveBeenCalled()
  })

  it('Windows 的 AltGr+S（波兰语 ś）报成 Ctrl+Alt：那是在打字——不开浮层、不拦输入', () => {
    const input = document.createElement('input')
    document.body.appendChild(input)
    const ev = new KeyboardEvent('keydown', {
      key: 'ś',
      code: 'KeyS',
      ctrlKey: true,
      altKey: true,
      modifierAltGraph: true,
      bubbles: true,
      cancelable: true,
    } as KeyboardEventInit)
    expect(ev.getModifierState('AltGraph')).toBe(true) // 判据真的看得见这个修饰键
    act(() => {
      input.dispatchEvent(ev)
    })
    expect(ev.defaultPrevented).toBe(false)
    expect(useTimelineStore.getState().namingOpen).toBe(false)
    expect(runManualSave).not.toHaveBeenCalled()
    input.remove()
  })

  it('AltGr 不在输入框里也不算快捷键（画布上打不出字，但也不该弹浮层）', () => {
    press({ key: 'ś', code: 'KeyS', ctrlKey: true, altKey: true, modifierAltGraph: true } as KeyboardEventInit)
    expect(useTimelineStore.getState().namingOpen).toBe(false)
  })

  it('在输入框里与其余快捷键一样让位：不开浮层，也不落进 ⌘S 变成保存', () => {
    const input = document.createElement('input')
    document.body.appendChild(input)
    const ev = new KeyboardEvent('keydown', {
      key: 's',
      code: 'KeyS',
      ctrlKey: true,
      altKey: true,
      bubbles: true,
      cancelable: true,
    })
    act(() => {
      input.dispatchEvent(ev)
    })
    expect(ev.defaultPrevented).toBe(false)
    expect(useTimelineStore.getState().namingOpen).toBe(false)
    expect(runManualSave).not.toHaveBeenCalled()
    input.remove()
  })

  it('对照：输入框里 ⌘S 仍拦下来保存（上面那条让位不是因为事件没到监听器）', () => {
    const input = document.createElement('input')
    document.body.appendChild(input)
    act(() => {
      input.dispatchEvent(
        new KeyboardEvent('keydown', { key: 's', code: 'KeyS', metaKey: true, bubbles: true, cancelable: true }),
      )
    })
    expect(runManualSave).toHaveBeenCalledTimes(1)
    input.remove()
  })

  it('对照：⌘S 仍是保存', () => {
    press({ key: 's', code: 'KeyS', metaKey: true })
    expect(runManualSave).toHaveBeenCalledTimes(1)
    expect(useTimelineStore.getState().namingOpen).toBe(false)
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
