/**
 * ⌘S / Ctrl+S 这条键位本身：按下去真的走 `runManualSave`，而且**在输入框和
 * 对话框里也拦**（否则浏览器弹「保存网页」，用户以为存了文档，存下来的是
 * 一张 HTML）。store 层的 `saveNow` 早有用例；这里钉的是键到动作那一跳。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useUiStore } from '@/store/uiStore'
import { useKeyboard } from './useKeyboard'

const runManualSave = vi.fn().mockResolvedValue(undefined)
vi.mock('@/store/actions', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/store/actions')>()),
  runManualSave: () => runManualSave(),
}))

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

function Host() {
  useKeyboard()
  return <input aria-label="name" />
}

let root: Root
let host: HTMLDivElement

const press = (target: EventTarget, init: KeyboardEventInit) => {
  const ev = new KeyboardEvent('keydown', { bubbles: true, cancelable: true, ...init })
  target.dispatchEvent(ev)
  return ev
}

beforeEach(async () => {
  runManualSave.mockClear()
  useUiStore.setState({ layoutOpen: false })
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => {
    root.render(<Host />)
  })
})

afterEach(async () => {
  await act(async () => {
    root.unmount()
  })
  document.body.innerHTML = ''
})

describe('⌘S 绑定', () => {
  it('⌘S 与 Ctrl+S 都调 runManualSave 并吃掉默认动作', async () => {
    const a = press(window, { key: 's', metaKey: true })
    const b = press(window, { key: 'S', ctrlKey: true })
    expect(runManualSave).toHaveBeenCalledTimes(2)
    expect(a.defaultPrevented && b.defaultPrevented).toBe(true)
  })

  it('焦点在输入框里也照存——那正是用户最想按它的时刻', async () => {
    const input = host.querySelector('input')!
    input.focus()
    const ev = press(input, { key: 's', metaKey: true })
    expect(runManualSave).toHaveBeenCalledTimes(1)
    expect(ev.defaultPrevented).toBe(true)
  })

  it('⇧⌘S 是「另存为」：开命名画布对话框，不走保存', async () => {
    press(window, { key: 's', metaKey: true, shiftKey: true })
    expect(runManualSave).not.toHaveBeenCalled()
    expect(useUiStore.getState().layoutOpen).toBe(true)
  })

  it('没按修饰键的 s 什么都不做', async () => {
    const ev = press(window, { key: 's' })
    expect(runManualSave).not.toHaveBeenCalled()
    expect(ev.defaultPrevented).toBe(false)
  })
})
