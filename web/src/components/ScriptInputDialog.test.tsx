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
import {
  ScriptInputActions,
  ScriptInputFields,
  useScriptInputAnswer,
} from '@/components/ScriptInputForm'
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

  it('getpass 那一问用密码框：输入被遮住、不自动补全，并说清 Tavotto 不保存它', () => {
    useScriptInputStore.getState().onRequested(
      req({ input_kind: 'getpass', prompt: 'Password: ', secret: true }),
    )
    render()
    expect(answerBox().type).toBe('password')
    expect(answerBox().getAttribute('autocomplete')).toBe('off')
    expect(document.body.textContent).toContain('不保存它')
  })

  it('口令交出去就从输入框里清掉，也不进 store', async () => {
    useScriptInputStore.getState().onRequested(
      req({ input_kind: 'getpass', prompt: 'Password: ', secret: true }),
    )
    let release!: () => void
    mockAnswer.mockReturnValue(new Promise((r) => (release = () => r({ ok: true }))))
    render()
    await type('hunter2')
    await click(buttonWith('提交'))
    expect(mockAnswer).toHaveBeenCalledWith('q1', 'hunter2')
    // 请求还在路上：框还开着，但里面已经没有口令了
    expect(answerBox().value).toBe('')
    expect(JSON.stringify(useScriptInputStore.getState())).not.toContain('hunter2')
    await act(async () => release())
  })

  it('普通问题的答案在请求失败时留在框里，改好再交', async () => {
    mockAnswer.mockRejectedValue(new ApiError('too long', 400, { code: 'script_input_invalid' }))
    useScriptInputStore.getState().onRequested(req())
    render()
    await type('1,2')
    await click(buttonWith('提交'))
    expect(answerBox().value).toBe('1,2')
  })

  it('上次的回答只是建议：说给人看，不预填、不自动交', () => {
    useScriptInputStore.getState().onRequested(
      req({ suggestion: '2', recheck: 'context_changed' }),
    )
    render()
    expect(document.body.querySelector('[data-script-input-suggestion]')!.textContent).toContain(
      '「2」',
    )
    expect(answerBox().value).toBe('')
    expect(mockAnswer).not.toHaveBeenCalled()
  })

  it.each([
    ['context_changed', '这次的输出和上次不一样'],
    ['config_changed', '这次的参数和上次不一样'],
    ['legacy_answer', '这台电脑上没有当时的记录'],
  ] as const)('重新确认的原因分开说（T13b）：%s', (recheck, reason) => {
    useScriptInputStore.getState().onRequested(req({ suggestion: '2', recheck }))
    render()
    const text = document.body.querySelector('[data-script-input-suggestion]')!.textContent!
    expect(text).toContain('「2」')
    expect(text).toContain(reason)
    // 换了机器（没有本机记录）不能说成「输出变了」——那是误导
    if (recheck === 'legacy_answer') expect(text).not.toContain('不一样')
  })

  it('口令永远不显示建议', () => {
    useScriptInputStore.getState().onRequested(
      req({ input_kind: 'getpass', secret: true, suggestion: 'leak' }),
    )
    render()
    expect(document.body.querySelector('[data-script-input-suggestion]')).toBeNull()
    expect(document.body.textContent).not.toContain('leak')
  })

  it('打开时焦点直接在答案框上（initialFocusRef），停止脚本在页脚 start 槽、危险浅底胶囊', () => {
    useScriptInputStore.getState().onRequested(req())
    render()
    expect(document.activeElement).toBe(answerBox())
    const stop = document.body.querySelector('[data-script-input-stop]')!
    expect(stop.closest('[data-dialog-footer]')).not.toBeNull()
    expect(stop.getAttribute('data-variant')).toBe('danger-tinted')
    const primaries = dialog()!.parentElement!.querySelectorAll('[data-variant="primary"]')
    expect(primaries).toHaveLength(1)
  })

  it('换项目之后旧项目的问不再显示', () => {
    useScriptInputStore.getState().onRequested(req())
    useScriptInputStore.getState().clear()
    render()
    expect(dialog()).toBeNull()
  })
})

/**
 * 同一问只有一个展示面（T08）：准备面板认领展示时原对话框让开；面板关掉（放手）时对话框接着显示**同一问**，
 * 不取消脚本、不丢掉唯一的答题入口。面板用的是同一份 `ScriptInputForm`，答的是同一个请求 id。
 */
describe('ScriptInputDialog 与其它展示面', () => {
  function Panel() {
    const answer = useScriptInputAnswer()
    return (
      <div data-panel="">
        <ScriptInputFields answer={answer} />
        <ScriptInputActions answer={answer} />
      </div>
    )
  }
  let panelRoot: Root
  let panelHost: HTMLDivElement
  beforeEach(() => {
    panelHost = document.createElement('div')
    document.body.appendChild(panelHost)
    panelRoot = createRoot(panelHost)
  })
  afterEach(() => {
    act(() => panelRoot.unmount())
    panelHost.remove()
    for (const p of useScriptInputStore.getState().presenters) {
      useScriptInputStore.getState().releasePresentation(p)
    }
  })

  it('面板认领时对话框不出现，面板里答的就是这一问', async () => {
    useScriptInputStore.getState().onRequested(req())
    useScriptInputStore.getState().claimPresentation('prep-panel')
    render()
    act(() => panelRoot.render(<Panel />))
    expect(dialog()).toBeNull()
    expect(panelHost.querySelector('[data-script-input-prompt]')!.textContent).toBe('numbers: ')
    await type('2')
    await click(buttonWith('提交'))
    expect(mockAnswer).toHaveBeenCalledWith('q1', '2')
  })

  it('面板关掉（放手）后对话框接着显示同一问，脚本没被停', () => {
    useScriptInputStore.getState().onRequested(req())
    useScriptInputStore.getState().claimPresentation('prep-panel')
    render()
    expect(dialog()).toBeNull()
    act(() => useScriptInputStore.getState().releasePresentation('prep-panel'))
    render()
    expect(dialog()).not.toBeNull()
    expect(document.body.querySelector('[data-script-input-prompt]')!.textContent).toBe('numbers: ')
    expect(mockStop).not.toHaveBeenCalled()
    expect(useScriptInputStore.getState().queue.map((q) => q.id)).toEqual(['q1'])
  })
})
