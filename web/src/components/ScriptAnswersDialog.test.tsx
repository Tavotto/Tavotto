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

const render = () => act(() => root.render(<ScriptAnswersDialog />))
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
/** 回答全局确认框（`askConfirm`）；返回它问的是什么 */
const answerConfirm = async (ok: boolean) => {
  const req = useUiStore.getState().confirm
  expect(req, '应当先弹确认框').toBeTruthy()
  await act(async () => {
    useUiStore.getState().setConfirm(null)
    req!.resolve(ok)
  })
  return req!
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

  it('删除：先问（danger），点头才删这一条并重新运行', async () => {
    useScriptInputStore.setState({ answers: ANSWERS })
    useScriptInputStore.getState().openManager('pick.py')
    render()
    await click(buttonWith('删除'))
    // 还没点头：一个请求都没发、也没重跑
    expect(mockForget).not.toHaveBeenCalled()
    expect(runSpy).not.toHaveBeenCalled()
    const req = await answerConfirm(true)
    expect(req.danger).toBe(true)
    expect(mockForget).toHaveBeenCalledWith('pick.py', 1, null)
    expect(runSpy).toHaveBeenCalledTimes(1)
    expect(runSpy).toHaveBeenCalledWith('pick.py', null)
  })

  it('删除：取消就什么都不做', async () => {
    useScriptInputStore.setState({ answers: ANSWERS })
    useScriptInputStore.getState().openManager('pick.py')
    render()
    await click(buttonWith('删除'))
    await answerConfirm(false)
    expect(mockForget).not.toHaveBeenCalled()
    expect(runSpy).not.toHaveBeenCalled()
  })

  it('删除：确认框开着时换了项目，点头属于旧项目——不发请求、不重跑', async () => {
    useScriptInputStore.setState({ answers: ANSWERS })
    useScriptInputStore.getState().openManager('pick.py')
    render()
    await click(buttonWith('删除'))
    const req = useUiStore.getState().confirm!
    await act(async () => {
      useScriptInputStore.getState().clear()
    })
    await act(async () => {
      useUiStore.getState().setConfirm(null)
      req.resolve(true)
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
      ['pick.py', 1, '3', null],
      ['pick.py', 2, 'b', null],
    ])
    expect(runSpy).toHaveBeenCalledTimes(1)
    expect(runSpy).toHaveBeenCalledWith('pick.py', null)
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

  it('删除在飞时同样锁住：输入框、保存与其它行的删除都不可用，回来才放开（Codex #821 P2）', async () => {
    let resolve!: (v: Awaited<ReturnType<typeof forgetScriptAnswer>>) => void
    mockForget.mockReturnValueOnce(new Promise((r) => (resolve = r)))
    useScriptInputStore.setState({ answers: TWO })
    useScriptInputStore.getState().openManager('pick.py')
    render()
    const boxes = () => [...dialog()!.querySelectorAll<HTMLInputElement>('input')]
    const forgets = () => [...dialog()!.querySelectorAll<HTMLButtonElement>('[data-script-answer-forget]')]
    await typeInto(boxes()[1], 'b')
    await click(forgets()[0])
    await answerConfirm(true)
    expect(boxes().every((b) => b.disabled)).toBe(true)
    expect(forgets().every((b) => b.disabled)).toBe(true)
    expect(saveButton().disabled).toBe(true)
    await act(async () => {
      resolve({ scripts: { 'pick.py': [TWO['pick.py'][1]] }, location: '', pending: [] })
    })
    expect(boxes().every((b) => !b.disabled)).toBe(true)
    expect(runSpy).toHaveBeenCalledTimes(1)
  })

  describe('答案改动锁归 store（维护者复审：关掉重开的洞）', () => {
    const boxes = () => [...dialog()!.querySelectorAll<HTMLInputElement>('input')]
    const forgets = () => [
      ...dialog()!.querySelectorAll<HTMLButtonElement>('[data-script-answer-forget]'),
    ]
    const locked = () =>
      boxes().every((b) => b.disabled) &&
      forgets().every((b) => b.disabled) &&
      saveButton().disabled

    it('删除在飞时关掉再打开：新对话框照样锁着，删除回来才放开', async () => {
      let resolve!: (v: Awaited<ReturnType<typeof forgetScriptAnswer>>) => void
      mockForget.mockReturnValueOnce(new Promise((r) => (resolve = r)))
      useScriptInputStore.setState({ answers: TWO })
      useScriptInputStore.getState().openManager('pick.py')
      render()
      await click(forgets()[0])
      await answerConfirm(true)
      expect(locked()).toBe(true)
      // 点「关闭」：AnswersManager 卸载
      await click(buttonWith('关闭'))
      expect(dialog()).toBeNull()
      // 重开同一个脚本的答案管理：新挂上的组件读 store 的锁
      await act(async () => {
        useScriptInputStore.getState().openManager('pick.py')
      })
      expect(dialog()).not.toBeNull()
      expect(locked(), 'pending forget still locks after Close/reopen').toBe(true)
      await act(async () => {
        resolve({ scripts: { 'pick.py': [TWO['pick.py'][1]] }, location: '', pending: [] })
      })
      expect(useScriptInputStore.getState().answersBusy).toBe(false)
      expect(boxes().every((b) => !b.disabled)).toBe(true)
      expect(runSpy).toHaveBeenCalledTimes(1)
      expect(mockUpdate).not.toHaveBeenCalled()
    })

    it('store 正忙时点保存（同一帧里抢先拿了锁）：一个请求都不发、也不重跑', async () => {
      useScriptInputStore.setState({ answers: TWO })
      useScriptInputStore.getState().openManager('pick.py')
      render()
      await typeInto(boxes()[0], '3')
      expect(saveButton().disabled).toBe(false)
      // 别的改动（例如重开前没回来的删除）已经拿着锁；按钮还没来得及重渲染成灰的
      let held: number | null = null
      await act(async () => {
        held = useScriptInputStore.getState().beginAnswersChange()
        saveButton().click()
      })
      expect(held).not.toBeNull()
      expect(mockUpdate).not.toHaveBeenCalled()
      expect(runSpy).not.toHaveBeenCalled()
      // 不是持有者的 token 也发不出请求
      const res = await useScriptInputStore.getState().saveAnswer(held! + 1, 'pick.py', 1, '3')
      expect(res.status).toBe('stale')
      expect(mockUpdate).not.toHaveBeenCalled()
      expect(useScriptInputStore.getState().beginAnswersChange()).toBeNull()
      useScriptInputStore.getState().endAnswersChange(held!)
      expect(useScriptInputStore.getState().answersBusy).toBe(false)
    })

    it('换项目清掉旧锁：新项目能存；旧删除回来既不放掉新锁、也不重跑', async () => {
      setCurrentProjectId('A')
      let resolveForget!: (v: Awaited<ReturnType<typeof forgetScriptAnswer>>) => void
      mockForget.mockReturnValueOnce(new Promise((r) => (resolveForget = r)))
      useScriptInputStore.setState({ answers: TWO })
      useScriptInputStore.getState().openManager('pick.py')
      render()
      await click(forgets()[0])
      await answerConfirm(true)
      expect(useScriptInputStore.getState().answersBusy).toBe(true)
      await act(async () => {
        useScriptInputStore.getState().clear()
        setCurrentProjectId('B')
      })
      expect(useScriptInputStore.getState().answersBusy).toBe(false)
      // 新项目里打开同名脚本的答案管理并保存（保存挂起）
      let resolveSave!: (v: Awaited<ReturnType<typeof updateScriptAnswer>>) => void
      mockUpdate.mockReturnValueOnce(new Promise((r) => (resolveSave = r)))
      await act(async () => {
        useScriptInputStore.setState({ answers: TWO })
        useScriptInputStore.getState().openManager('pick.py')
      })
      expect(locked()).toBe(false)
      await typeInto(boxes()[0], '3')
      await click(saveButton())
      expect(mockUpdate).toHaveBeenCalledWith('pick.py', 1, '3', null)
      expect(locked()).toBe(true)
      // 旧项目的删除回来：快照丢弃、不重跑、也不放掉新项目那把锁
      await act(async () => {
        resolveForget({ scripts: {}, location: '', pending: [] })
      })
      expect(locked()).toBe(true)
      expect(useScriptInputStore.getState().answers).toEqual(TWO)
      expect(runSpy).not.toHaveBeenCalled()
      await act(async () => {
        resolveSave({ scripts: TWO, location: '', pending: [] })
      })
      expect(locked()).toBe(false)
      expect(runSpy).toHaveBeenCalledTimes(1)
      setCurrentProjectId(null)
    })
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
  await click(saveButton())
  expect(mockUpdate).toHaveBeenCalledWith('pick.py', 1, 'new-beta', 'rc_b')
  expect(runSpy).toHaveBeenLastCalledWith('pick.py', 'rc_b')
  await click(reordered[1].querySelector<HTMLButtonElement>('[data-script-answer-forget]')!)
  await answerConfirm(true)
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
  await answerConfirm(true)
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

it('batch save over two configurations reruns each configuration exactly once', async () => {
  const rows = [
    { ...ANSWERS['pick.py'][0], run_config: 'rc_a', answer: 'alpha' },
    { ...ANSWERS['pick.py'][0], run_config: 'rc_a', index: 2, prompt: 'p2', answer: 'a2' },
    { ...ANSWERS['pick.py'][0], run_config: 'rc_b', answer: 'beta' },
  ]
  mockUpdate.mockResolvedValue({ scripts: { 'pick.py': rows }, location: '', pending: [] })
  useScriptInputStore.setState({ answers: { 'pick.py': rows } })
  useScriptInputStore.getState().openManager('pick.py')
  render()
  const boxes = dialog()!.querySelectorAll<HTMLInputElement>('input')
  for (const [i, v] of ['x1', 'x2', 'x3'].entries()) {
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(boxes[i], v)
      boxes[i].dispatchEvent(new Event('input', { bubbles: true }))
    })
  }
  await click(saveButton())
  expect(mockUpdate.mock.calls).toEqual([
    ['pick.py', 1, 'x1', 'rc_a'],
    ['pick.py', 2, 'x2', 'rc_a'],
    ['pick.py', 1, 'x3', 'rc_b'],
  ])
  expect(runSpy.mock.calls).toEqual([
    ['pick.py', 'rc_a'],
    ['pick.py', 'rc_b'],
  ])
})
