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

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const mockUpdate = vi.mocked(updateScriptAnswer)
const mockForget = vi.mocked(forgetScriptAnswer)
const ANSWERS = { 'pick.py': [{ index: 1, prompt: 'numbers: ', answer: '1,2', kind: 'input' }] }

let root: Root
let host: HTMLDivElement
let runSpy: ReturnType<typeof vi.fn>

const render = () => act(() => root.render(<ScriptAnswersDialog />))
const dialog = () => document.body.querySelector('[data-dialog="script-answers"]')
const buttonWith = (needle: string) =>
  Array.from(document.body.querySelectorAll('button')).find((b) =>
    (b.textContent ?? '').includes(needle),
  )!
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
    expect(mockUpdate).toHaveBeenCalledWith('pick.py', 1, '2', null)
    expect(runSpy).toHaveBeenCalledWith('pick.py', null)
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

  it('删除：删这一条并重新运行', async () => {
    useScriptInputStore.setState({ answers: ANSWERS })
    useScriptInputStore.getState().openManager('pick.py')
    render()
    await click(buttonWith('删除'))
    expect(mockForget).toHaveBeenCalledWith('pick.py', 1, null)
    expect(runSpy).toHaveBeenCalledWith('pick.py', null)
  })
})


it('same-index configurations have distinct rows; editing and deleting target that row', async () => {
  const rows = [
    { ...ANSWERS['pick.py'][0], run_config: 'rc_a', answer: 'alpha' },
    { ...ANSWERS['pick.py'][0], run_config: 'rc_b', answer: 'beta' },
  ]
  useScriptInputStore.setState({ answers: { 'pick.py': rows } })
  useScriptInputStore.getState().openManager('pick.py')
  render()
  const before = Array.from(dialog()!.querySelectorAll<HTMLLIElement>('li'))
  expect(before[0].textContent).toContain('rc_a')
  expect(before[1].textContent).toContain('rc_b')
  const box = before[1].querySelector('input')!
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(box, 'new-beta')
    box.dispatchEvent(new Event('input', { bubbles: true }))
    // Reorder incoming rows: the draft must stay with B, not with its old position.
    useScriptInputStore.setState({ answers: { 'pick.py': [rows[1], rows[0]] } })
  })
  const reordered = Array.from(dialog()!.querySelectorAll<HTMLLIElement>('li'))
  expect(reordered[0]).toBe(before[1])
  expect(reordered[0].querySelector('input')!.value).toBe('new-beta')
  mockUpdate.mockResolvedValue({ scripts: { 'pick.py': [rows[1], rows[0]] }, location: '', pending: [] })
  await click(reordered[0].querySelector('button')!)
  expect(mockUpdate).toHaveBeenCalledWith('pick.py', 1, 'new-beta', 'rc_b')
  expect(runSpy).toHaveBeenLastCalledWith('pick.py', 'rc_b')
  await click(reordered[1].querySelectorAll('button')[1])
  expect(mockForget).toHaveBeenCalledWith('pick.py', 1, 'rc_a')
  expect(runSpy).toHaveBeenLastCalledWith('pick.py', 'rc_a')
})

it('a delayed configured delete cannot rerun after switching projects', async () => {
  setCurrentProjectId('A')
  let resolve!: (v: Awaited<ReturnType<typeof forgetScriptAnswer>>) => void
  mockForget.mockReturnValue(new Promise((r) => (resolve = r)))
  useScriptInputStore.setState({ answers: { 'pick.py': [{ ...ANSWERS['pick.py'][0], run_config: 'rc_a' }] } })
  useScriptInputStore.getState().openManager('pick.py')
  render()
  await click(buttonWith('删除'))
  expect(mockForget).toHaveBeenCalledWith('pick.py', 1, 'rc_a')
  await act(async () => {
    useScriptInputStore.getState().clear()
    setCurrentProjectId('B')
    resolve({ scripts: {}, location: '', pending: [] })
  })
  expect(runSpy).not.toHaveBeenCalled()
  expect(useScriptInputStore.getState().answers).toBeNull()
  setCurrentProjectId(null)
})
