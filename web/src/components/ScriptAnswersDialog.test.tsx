/**
 * 「记住的输入」管理（ADR 0099 §七）：改答案 = 一条带新答案的请求 + 对这个脚本发起一次重新运行。
 * 另钉一件事：没开着的时候它也挂在 App 上，选择器必须稳定（每次新建 `[]` 会无限重渲染——
 * 真浏览器第一次跑 e2e 就是这么红的，React #185）。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api', async (orig) => {
  const real = await orig<typeof import('@/lib/api')>()
  return { ...real, updateScriptAnswer: vi.fn(), forgetScriptAnswer: vi.fn() }
})

import { forgetScriptAnswer, updateScriptAnswer } from '@/lib/api'
import { ScriptAnswersDialog } from '@/components/ScriptAnswersDialog'
import { useScriptInputStore } from '@/store/scriptInputStore'
import { useScriptRunStore } from '@/store/scriptRunStore'
import { setCurrentProjectId } from '@/lib/session'
import { useUiStore } from '@/store/uiStore'
import { TooltipProvider } from '@/components/ui/Tooltip'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const mockUpdate = vi.mocked(updateScriptAnswer)
const mockForget = vi.mocked(forgetScriptAnswer)
const ANSWERS = { 'pick.py': [{ index: 1, prompt: 'numbers: ', answer: '1,2', kind: 'input' }] }
const TWO = {
  'pick.py': [
    { index: 1, prompt: 'numbers: ', answer: '1,2', kind: 'input' },
    { index: 2, prompt: 'letter: ', answer: 'a', kind: 'input' },
  ],
}

let root: Root
let host: HTMLDivElement
let runSpy: ReturnType<typeof vi.fn>

const render = () =>
  act(() =>
    root.render(
      <TooltipProvider>
        <ScriptAnswersDialog />
      </TooltipProvider>,
    ),
  )
const dialog = () => document.body.querySelector('[data-dialog="script-answers"]')
const buttonWith = (needle: string) =>
  Array.from(document.body.querySelectorAll('button')).find((b) =>
    (b.textContent ?? '').includes(needle),
  )!
const typeInto = async (box: HTMLInputElement, value: string) => {
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(box, value)
    box.dispatchEvent(new Event('input', { bubbles: true }))
  })
}
const saveButton = () =>
  document.body.querySelector<HTMLButtonElement>('[data-script-answers-save]')!
/**
 * 行尾 ⋯ →「删除」（暂存）。Radix 的触发器认 pointerdown（button 0），jsdom 里 `.click()` 打不开它；
 * jsdom 没有 PointerEvent 构造器，同名的 MouseEvent 照样按事件名派发。
 */
const forgetRow = async (row: Element) => {
  const more = row.querySelector<HTMLButtonElement>('[data-row-menu-trigger]')!
  expect(more, '这一行没有 ⋯').toBeTruthy()
  await act(async () => {
    more.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, button: 0 }))
    await Promise.resolve()
  })
  const item = document.querySelector<HTMLElement>('[data-script-answer-forget]')!
  expect(item, '⋯ 菜单里没有「删除」').toBeTruthy()
  await act(async () => item.click())
}
const click = async (el: Element) => {
  await act(async () => {
    ;(el as HTMLElement).click()
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  mockUpdate.mockResolvedValue({ scripts: { 'pick.py': [{ ...ANSWERS['pick.py'][0], answer: '2' }] }, location: 'tavottofile/_script_inputs.json', pending: [] })
  mockForget.mockResolvedValue({ scripts: {}, location: '', pending: [] })
  runSpy = vi.fn().mockResolvedValue(undefined)
  useScriptRunStore.setState({ run: runSpy as never })
  useScriptInputStore.getState().clear()
  useUiStore.getState().setConfirm(null)
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})

afterEach(() => {
  act(() => root.unmount())
  host.remove()
  useScriptInputStore.getState().clear()
})

describe('ScriptAnswersDialog', () => {
  it('没开着时什么都不渲染，也不会无限重渲染', () => {
    render()
    render()
    expect(dialog()).toBeNull()
  })

  it('改答案：发新答案并重新运行这个脚本', async () => {
    useScriptInputStore.setState({ answers: ANSWERS, location: 'tavottofile/_script_inputs.json' })
    useScriptInputStore.getState().openManager('pick.py')
    render()
    expect(dialog()!.textContent).toContain('numbers: ')
    expect(dialog()!.textContent).toContain('tavottofile/_script_inputs.json')
    const box = dialog()!.querySelector<HTMLInputElement>('input')!
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(box, '2')
      box.dispatchEvent(new Event('input', { bubbles: true }))
    })
    await click(buttonWith('保存并重新运行'))
    expect(mockUpdate).toHaveBeenCalledWith('pick.py', 1, '2')
    expect(runSpy).toHaveBeenCalledWith('pick.py')
  })

  it('请求在飞时换了项目：不在新项目里重新运行（Codex #680 P1）', async () => {
    setCurrentProjectId('A')
    let resolve!: (v: Awaited<ReturnType<typeof updateScriptAnswer>>) => void
    mockUpdate.mockReturnValue(new Promise((r) => (resolve = r)))
    useScriptInputStore.setState({ answers: ANSWERS })
    useScriptInputStore.getState().openManager('pick.py')
    render()
    const box = dialog()!.querySelector<HTMLInputElement>('input')!
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(box, '2')
      box.dispatchEvent(new Event('input', { bubbles: true }))
    })
    await click(buttonWith('保存并重新运行'))
    // 换项目：store 换代、会话认领 B
    await act(async () => {
      useScriptInputStore.getState().clear()
      setCurrentProjectId('B')
    })
    await act(async () => {
      resolve({ scripts: {}, location: '', pending: [] })
    })
    expect(runSpy).not.toHaveBeenCalled()
    expect(useScriptInputStore.getState().answers).toBeNull()
    setCurrentProjectId(null)
  })

  it('删除是暂存的：⋯ →「删除」只在行上标「将删除」，一个请求都不发；脚部主按钮提交并只重跑一次', async () => {
    useScriptInputStore.setState({ answers: ANSWERS })
    useScriptInputStore.getState().openManager('pick.py')
    render()
    const row = dialog()!.querySelector('[data-script-answer="1"]')!
    await forgetRow(row)
    // 还没提交：一个请求都没发、也没重跑、也没弹第二层确认框
    expect(mockForget).not.toHaveBeenCalled()
    expect(runSpy).not.toHaveBeenCalled()
    expect(useUiStore.getState().confirm).toBeNull()
    expect(row.hasAttribute('data-forgetting')).toBe(true)
    expect(saveButton().disabled).toBe(false)
    expect(saveButton().textContent).toBe('保存并重新运行（1）')
    await click(saveButton())
    expect(mockForget).toHaveBeenCalledWith('pick.py', 1)
    expect(runSpy).toHaveBeenCalledTimes(1)
    expect(runSpy).toHaveBeenCalledWith('pick.py')
  })

  it('删除可撤销：撤销之后什么都不发', async () => {
    useScriptInputStore.setState({ answers: ANSWERS })
    useScriptInputStore.getState().openManager('pick.py')
    render()
    const row = dialog()!.querySelector('[data-script-answer="1"]')!
    await forgetRow(row)
    await click(row.querySelector('[data-script-answer-undo]')!)
    expect(row.hasAttribute('data-forgetting')).toBe(false)
    expect(saveButton().disabled).toBe(true)
    expect(mockForget).not.toHaveBeenCalled()
    expect(runSpy).not.toHaveBeenCalled()
  })

  it('改一条、删一条：同一次提交，只重跑一次', async () => {
    useScriptInputStore.setState({ answers: TWO })
    useScriptInputStore.getState().openManager('pick.py')
    render()
    const boxes = dialog()!.querySelectorAll<HTMLInputElement>('input')
    await typeInto(boxes[0], '3')
    await forgetRow(dialog()!.querySelector('[data-script-answer="2"]')!)
    expect(saveButton().textContent).toBe('保存并重新运行（2）')
    await click(saveButton())
    expect(mockUpdate.mock.calls).toEqual([['pick.py', 1, '3']])
    expect(mockForget.mock.calls).toEqual([['pick.py', 2]])
    expect(runSpy).toHaveBeenCalledTimes(1)
  })

  it('标了删除之后换了项目：暂存的删除属于旧项目——不发请求、不重跑', async () => {
    useScriptInputStore.setState({ answers: ANSWERS })
    useScriptInputStore.getState().openManager('pick.py')
    render()
    await forgetRow(dialog()!.querySelector('[data-script-answer="1"]')!)
    await act(async () => {
      useScriptInputStore.getState().clear()
    })
    expect(mockForget).not.toHaveBeenCalled()
    expect(runSpy).not.toHaveBeenCalled()
  })

  it('行内没有主按钮：整个对话框只有脚部一颗「保存并重新运行」，没改动时是灰的', async () => {
    useScriptInputStore.setState({ answers: TWO })
    useScriptInputStore.getState().openManager('pick.py')
    render()
    const rows = dialog()!.querySelectorAll('[data-script-answer]')
    expect(rows).toHaveLength(2)
    for (const row of rows) {
      for (const b of row.querySelectorAll('button')) expect(b.className).not.toContain('bg-ink')
    }
    const primaries = [...document.body.querySelectorAll('button')].filter((b) =>
      b.className.includes('bg-ink'),
    )
    expect(primaries).toEqual([saveButton()])
    expect(saveButton().disabled).toBe(true)
    expect(saveButton().textContent).toBe('保存并重新运行')
  })

  it('改两条：依次保存两条，只重新运行一次', async () => {
    useScriptInputStore.setState({ answers: TWO })
    useScriptInputStore.getState().openManager('pick.py')
    render()
    const boxes = dialog()!.querySelectorAll<HTMLInputElement>('input')
    await typeInto(boxes[0], '3')
    expect(saveButton().textContent).toBe('保存并重新运行（1）')
    await typeInto(boxes[1], 'b')
    expect(saveButton().textContent).toBe('保存并重新运行（2）')
    // 改回原值不算改过
    await typeInto(boxes[1], 'a')
    expect(saveButton().textContent).toBe('保存并重新运行（1）')
    await typeInto(boxes[1], 'b')
    await click(saveButton())
    expect(mockUpdate.mock.calls).toEqual([
      ['pick.py', 1, '3'],
      ['pick.py', 2, 'b'],
    ])
    expect(runSpy).toHaveBeenCalledTimes(1)
    expect(runSpy).toHaveBeenCalledWith('pick.py')
  })

  it('一条保存失败：失败的那行说出原因并保留改动，存好的照样重跑一次', async () => {
    mockUpdate
      .mockRejectedValueOnce(new Error('disk full'))
      .mockResolvedValueOnce({
        scripts: { 'pick.py': [TWO['pick.py'][0], { ...TWO['pick.py'][1], answer: 'b' }] },
        location: '',
        pending: [],
      })
    useScriptInputStore.setState({ answers: TWO })
    useScriptInputStore.getState().openManager('pick.py')
    render()
    const boxes = dialog()!.querySelectorAll<HTMLInputElement>('input')
    await typeInto(boxes[0], '3')
    await typeInto(boxes[1], 'b')
    await click(saveButton())
    expect(mockUpdate).toHaveBeenCalledTimes(2)
    expect(runSpy).toHaveBeenCalledTimes(1)
    const row1 = dialog()!.querySelector('[data-script-answer="1"]')!
    expect(row1.querySelector('[role="alert"]')?.textContent).toContain('disk full')
    expect(row1.hasAttribute('data-dirty')).toBe(true)
    expect(saveButton().textContent).toBe('保存并重新运行（1）')
  })

  it('批量保存途中换了项目：后面那几条不再发，也不重跑', async () => {
    setCurrentProjectId('A')
    let resolve!: (v: Awaited<ReturnType<typeof updateScriptAnswer>>) => void
    mockUpdate.mockReturnValueOnce(new Promise((r) => (resolve = r)))
    useScriptInputStore.setState({ answers: TWO })
    useScriptInputStore.getState().openManager('pick.py')
    render()
    const boxes = dialog()!.querySelectorAll<HTMLInputElement>('input')
    await typeInto(boxes[0], '3')
    await typeInto(boxes[1], 'b')
    await click(saveButton())
    await act(async () => {
      useScriptInputStore.getState().clear()
      setCurrentProjectId('B')
    })
    await act(async () => {
      resolve({ scripts: {}, location: '', pending: [] })
    })
    expect(mockUpdate).toHaveBeenCalledTimes(1)
    expect(runSpy).not.toHaveBeenCalled()
    setCurrentProjectId(null)
  })
  it('保存在飞时关掉对话框（Codex #821 P2）：同一项目里已存好的那条照样重跑，剩下的不再发', async () => {
    let resolve!: (v: Awaited<ReturnType<typeof updateScriptAnswer>>) => void
    mockUpdate.mockReturnValueOnce(new Promise((r) => (resolve = r)))
    useScriptInputStore.setState({ answers: TWO })
    useScriptInputStore.getState().openManager('pick.py')
    render()
    const boxes = dialog()!.querySelectorAll<HTMLInputElement>('input')
    await typeInto(boxes[0], '3')
    await typeInto(boxes[1], 'b')
    await click(saveButton())
    await act(async () => {
      useScriptInputStore.getState().closeManager()
    })
    await act(async () => {
      resolve({ scripts: {}, location: '', pending: [] })
    })
    expect(mockUpdate).toHaveBeenCalledTimes(1)
    expect(runSpy).toHaveBeenCalledTimes(1)
  })
  it('批量保存在飞时输入框锁住，存好后不冲掉任何新输入（Codex #821 P1）', async () => {
    let resolve!: (v: Awaited<ReturnType<typeof updateScriptAnswer>>) => void
    mockUpdate.mockReturnValueOnce(new Promise((r) => (resolve = r)))
    useScriptInputStore.setState({ answers: TWO })
    useScriptInputStore.getState().openManager('pick.py')
    render()
    const box = () => dialog()!.querySelectorAll<HTMLInputElement>('input')[0]
    await typeInto(box(), '3')
    await click(saveButton())
    expect(box().disabled).toBe(true)
    await act(async () => {
      resolve({ scripts: TWO, location: '', pending: [] })
    })
    expect(box().disabled).toBe(false)
  })

  it('删除随保存一起发：在飞时输入框、保存与各行 ⋯ 都锁住，回来才放开、只重跑一次（Codex #821 P2，暂存式删除下的同一条保证）', async () => {
    let resolve!: (v: Awaited<ReturnType<typeof forgetScriptAnswer>>) => void
    mockForget.mockReturnValueOnce(new Promise((r) => (resolve = r)))
    useScriptInputStore.setState({ answers: TWO })
    useScriptInputStore.getState().openManager('pick.py')
    render()
    const boxes = () => [...dialog()!.querySelectorAll<HTMLInputElement>('input')]
    const menus = () => [...dialog()!.querySelectorAll<HTMLButtonElement>('[data-row-menu-trigger]')]
    await typeInto(boxes()[1], 'b')
    await forgetRow(dialog()!.querySelector('[data-script-answer="1"]')!)
    await click(saveButton())
    expect(mockForget).toHaveBeenCalledWith('pick.py', 1)
    expect(boxes().every((b) => b.disabled)).toBe(true)
    expect(menus().every((b) => b.disabled)).toBe(true)
    expect(saveButton().disabled).toBe(true)
    await act(async () => {
      resolve({ scripts: { 'pick.py': [TWO['pick.py'][1]] }, location: '', pending: [] })
    })
    expect(runSpy).toHaveBeenCalledTimes(1)
  })
})
