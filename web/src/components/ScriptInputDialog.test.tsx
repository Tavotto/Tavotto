/**
 * 脚本 `input()` 的作答框（ADR 0099 §六 / §八）。
 *
 * 断言落在**后端调用**上：用户点的那一下最终要变成哪一条请求（带答案 / EOF / 停止）。
 * 另钉两件与安全有关的事：脚本的文字只当纯文本（`<b>` 不会变成元素），以及点外面 / Esc 不算回答。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api', async (orig) => {
  const real = await orig<typeof import('@/lib/api')>()
  return {
    ...real,
    answerScriptInput: vi.fn(),
    stopScriptInput: vi.fn(),
    fetchScriptAnswers: vi.fn().mockResolvedValue({ scripts: {}, location: '', pending: [] }),
  }
})

import { ApiError, answerScriptInput, stopScriptInput, type ScriptInputRequest } from '@/lib/api'
import { ScriptInputDialog } from '@/components/ScriptInputDialog'
import { useScriptInputStore } from '@/store/scriptInputStore'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const mockAnswer = vi.mocked(answerScriptInput)
const mockStop = vi.mocked(stopScriptInput)

const req = (over: Partial<ScriptInputRequest> = {}): ScriptInputRequest => ({
  id: 'q1',
  script: 'pick.py',
  index: 1,
  input_kind: 'input',
  prompt: 'numbers: ',
  stdout_tail: '1. a.dat\n2. b.dat',
  ...over,
})

let root: Root
let host: HTMLDivElement

const render = () => act(() => root.render(<ScriptInputDialog />))
const dialog = () => document.body.querySelector('[data-dialog="script-input"]')
const answerBox = () =>
  document.body.querySelector<HTMLInputElement>('[data-script-input-answer]')!
const buttonWith = (needle: string) =>
  Array.from(document.body.querySelectorAll('button')).find((b) =>
    (b.textContent ?? '').includes(needle),
  )!
const click = async (el: Element) => {
  await act(async () => {
    ;(el as HTMLElement).click()
  })
}
const type = async (value: string) => {
  const el = answerBox()
  await act(async () => {
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
    setter.call(el, value)
    el.dispatchEvent(new Event('input', { bubbles: true }))
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  mockAnswer.mockResolvedValue({ ok: true })
  mockStop.mockResolvedValue({ ok: true })
  useScriptInputStore.getState().clear()
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})

afterEach(() => {
  act(() => root.unmount())
  host.remove()
  useScriptInputStore.getState().clear()
})

describe('ScriptInputDialog', () => {
  it('没有在等的问时什么都不渲染', () => {
    render()
    expect(dialog()).toBeNull()
  })

  it('用户看得到脚本列出的编号清单和提示', () => {
    useScriptInputStore.getState().onRequested(req())
    render()
    const out = document.body.querySelector('[data-script-input-output]')!
    expect(out.textContent).toBe('1. a.dat\n2. b.dat')
    expect(document.body.querySelector('[data-script-input-prompt]')!.textContent).toBe('numbers: ')
  })

  it('脚本的文字只当纯文本：不会变成元素', () => {
    useScriptInputStore.getState().onRequested(
      req({ prompt: '<b data-x="p">p</b>', stdout_tail: '<img data-x="o" src=x>' }),
    )
    render()
    expect(document.body.querySelector('[data-x]')).toBeNull()
    expect(document.body.querySelector('[data-script-input-prompt]')!.textContent).toBe(
      '<b data-x="p">p</b>',
    )
  })

  it('提交把输入框里的原文发给后端，答完就关', async () => {
    useScriptInputStore.getState().onRequested(req())
    render()
    await type('1,2')
    await click(buttonWith('提交'))
    expect(mockAnswer).toHaveBeenCalledWith('q1', '1,2')
    render()
    expect(dialog()).toBeNull()
  })

  it('「结束输入」发 EOF，不发答案', async () => {
    useScriptInputStore.getState().onRequested(req())
    render()
    await type('ignored')
    await click(buttonWith('结束输入'))
    expect(mockAnswer).toHaveBeenCalledWith('q1', null)
  })

  it('「停止脚本」走停止端点', async () => {
    useScriptInputStore.getState().onRequested(req())
    render()
    await click(buttonWith('停止脚本'))
    expect(mockStop).toHaveBeenCalledWith('q1')
    expect(mockAnswer).not.toHaveBeenCalled()
  })

  it('Esc 不算回答：框还在，也没有发任何请求', async () => {
    useScriptInputStore.getState().onRequested(req())
    render()
    await act(async () => {
      document.activeElement?.dispatchEvent(
        new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }),
      )
    })
    expect(dialog()).not.toBeNull()
    expect(mockAnswer).not.toHaveBeenCalled()
    expect(mockStop).not.toHaveBeenCalled()
  })

  it('这一问已经不在等了（404）就收起，不留一个答不了的框', async () => {
    mockAnswer.mockRejectedValue(new ApiError('gone', 404, { code: 'script_input_not_pending' }))
    useScriptInputStore.getState().onRequested(req())
    render()
    await click(buttonWith('提交'))
    render()
    expect(dialog()).toBeNull()
  })

  it('后端说不等了（input_closed）对话框就关；下一问来了换内容', () => {
    const store = useScriptInputStore.getState()
    store.onRequested(req())
    render()
    store.onClosed('q1')
    store.onRequested(req({ id: 'q2', index: 2, prompt: 'again: ' }))
    render()
    expect(document.body.querySelector('[data-script-input-prompt]')!.textContent).toBe('again: ')
    expect(document.body.textContent).toContain('第 2 个问题')
  })

  it('getpass 那一问说清楚：明文显示、不会被记住', () => {
    useScriptInputStore.getState().onRequested(req({ input_kind: 'getpass', prompt: 'Password: ' }))
    render()
    expect(document.body.textContent).toContain('不会被记住')
  })

  it('换项目之后旧项目的问不再显示', () => {
    useScriptInputStore.getState().onRequested(req())
    useScriptInputStore.getState().clear()
    render()
    expect(dialog()).toBeNull()
  })
})
