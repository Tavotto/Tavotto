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
  return { ...real, updateScriptAnswer: vi.fn(), forgetScriptAnswer: vi.fn(), probeScript: vi.fn() }
})

import { forgetScriptAnswer, probeScript, updateScriptAnswer } from '@/lib/api'
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
const mockProbe = vi.mocked(probeScript)
/** 真的 `run`（beforeEach 把它换成 spy；下面两条要走真的同脚本防并发闸） */
const realRun = useScriptRunStore.getState().run
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
    expect(mockForget).toHaveBeenCalledWith('pick.py', 1, null)
    expect(runSpy).toHaveBeenCalledTimes(1)
    expect(runSpy).toHaveBeenCalledWith('pick.py', null)
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
    expect(mockUpdate.mock.calls).toEqual([['pick.py', 1, '3', null]])
    expect(mockForget.mock.calls).toEqual([['pick.py', 2, null]])
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

  it('批量保存在飞时对话框锁住（Codex #831 P1）：Esc、点外面、× 都关不掉，所有暂存改动发完、只重跑一次', async () => {
    let resolve!: (v: Awaited<ReturnType<typeof updateScriptAnswer>>) => void
    mockUpdate.mockReturnValueOnce(new Promise((r) => (resolve = r)))
    useScriptInputStore.setState({ answers: TWO })
    useScriptInputStore.getState().openManager('pick.py')
    render()
    // 两条暂存：改第 1 条、删第 2 条
    await typeInto(dialog()!.querySelectorAll<HTMLInputElement>('input')[0], '3')
    await forgetRow(dialog()!.querySelector('[data-script-answer="2"]')!)
    expect(dialog()!.querySelector('[data-dialog-close]'), '没在保存时右上角应有 ×').toBeTruthy()
    await click(saveButton())
    expect(mockUpdate).toHaveBeenCalledTimes(1)
    // 在飞：× 不给、Esc 与点外面都不关
    expect(dialog()!.querySelector('[data-dialog-close]')).toBeNull()
    await act(async () => {
      document.body.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }))
    })
    await act(async () => {
      document.body.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, button: 0 }))
      document.body.dispatchEvent(new MouseEvent('pointerup', { bubbles: true, button: 0 }))
      document.body.dispatchEvent(new MouseEvent('click', { bubbles: true, button: 0 }))
    })
    expect(useScriptInputStore.getState().managing).toBe('pick.py')
    expect(dialog()).toBeTruthy()
    await act(async () => {
      resolve({ scripts: TWO, location: '', pending: [] })
    })
    expect(mockUpdate.mock.calls).toEqual([['pick.py', 1, '3', null]])
    expect(mockForget.mock.calls).toEqual([['pick.py', 2, null]])
    expect(runSpy).toHaveBeenCalledTimes(1)
    // 走完放开：× 回来
    expect(dialog()!.querySelector('[data-dialog-close]')).toBeTruthy()
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
    expect(mockForget).toHaveBeenCalledWith('pick.py', 1, null)
    expect(boxes().every((b) => b.disabled)).toBe(true)
    expect(menus().every((b) => b.disabled)).toBe(true)
    expect(saveButton().disabled).toBe(true)
    await act(async () => {
      resolve({ scripts: { 'pick.py': [TWO['pick.py'][1]] }, location: '', pending: [] })
    })
    expect(runSpy).toHaveBeenCalledTimes(1)
  })

  describe('答案改动锁归 store（维护者复审：关掉重开的洞；暂存式设计下整批一把锁）', () => {
    const boxes = () => [...dialog()!.querySelectorAll<HTMLInputElement>('input')]
    const menus = () => [...dialog()!.querySelectorAll<HTMLButtonElement>('[data-row-menu-trigger]')]
    const undos = () => [...dialog()!.querySelectorAll<HTMLButtonElement>('[data-script-answer-undo]')]
    const locked = () =>
      boxes().every((b) => b.disabled) &&
      menus().every((b) => b.disabled) &&
      undos().every((b) => b.disabled) &&
      saveButton().disabled

    it('删除随批量提交在飞时卸载再挂上答案管理：新对话框照样锁着（busy），回来才放开、只重跑一次', async () => {
      let resolve!: (v: Awaited<ReturnType<typeof forgetScriptAnswer>>) => void
      mockForget.mockReturnValueOnce(new Promise((r) => (resolve = r)))
      useScriptInputStore.setState({ answers: TWO })
      useScriptInputStore.getState().openManager('pick.py')
      render()
      await forgetRow(dialog()!.querySelector('[data-script-answer="1"]')!)
      await click(saveButton())
      expect(mockForget).toHaveBeenCalledWith('pick.py', 1, null)
      expect(locked()).toBe(true)
      // 在飞时 busy 拦住了 × / Esc / 点外面 / 「取消」；还能把它关掉的只剩 store 那条路
      // （closeManager()，例如别处的入口）——关掉再打开换一代 key，AnswersManager 卸载、重新挂上
      await act(async () => {
        useScriptInputStore.getState().closeManager()
      })
      await act(async () => {
        useScriptInputStore.getState().openManager('pick.py')
      })
      expect(dialog()).not.toBeNull()
      // 确实是新挂上的那份：旧实例的暂存删除没带过来，第 1 行又是输入框
      expect(dialog()!.querySelector('[data-script-answer="1"]')!.hasAttribute('data-forgetting')).toBe(false)
      expect(boxes()).toHaveLength(2)
      // 新组件读 store 的锁：输入框 / ⋯ / 保存都灰、对话框 busy（没有 ×）
      expect(locked(), 'pending batch still locks after unmount/remount').toBe(true)
      expect(dialog()!.querySelector('[data-dialog-close]')).toBeNull()
      await act(async () => {
        resolve({ scripts: { 'pick.py': [TWO['pick.py'][1]] }, location: '', pending: [] })
      })
      expect(useScriptInputStore.getState().answersBusy).toBe(false)
      expect(boxes().every((b) => !b.disabled)).toBe(true)
      expect(dialog()!.querySelector('[data-dialog-close]')).toBeTruthy()
      expect(runSpy).toHaveBeenCalledTimes(1)
      expect(mockUpdate).not.toHaveBeenCalled()
    })

    it('store 正忙时点保存（同一帧里抢先拿了锁）：整批一个请求都不发、也不重跑', async () => {
      useScriptInputStore.setState({ answers: TWO })
      useScriptInputStore.getState().openManager('pick.py')
      render()
      // 一改一删，两条暂存
      await typeInto(boxes()[0], '3')
      await forgetRow(dialog()!.querySelector('[data-script-answer="2"]')!)
      expect(saveButton().disabled).toBe(false)
      // 别的改动（例如重开前没回来的那批）已经拿着锁；按钮还没来得及重渲染成灰的
      let held: number | null = null
      await act(async () => {
        held = useScriptInputStore.getState().beginAnswersChange()
        saveButton().click()
      })
      expect(held).not.toBeNull()
      expect(mockUpdate).not.toHaveBeenCalled()
      expect(mockForget).not.toHaveBeenCalled()
      expect(runSpy).not.toHaveBeenCalled()
      // 不是持有者的 token 也发不出请求
      const save = await useScriptInputStore.getState().saveAnswer(held! + 1, 'pick.py', 1, '3')
      const forget = await useScriptInputStore.getState().forgetAnswer(held! + 1, 'pick.py', 2)
      expect(save.status).toBe('stale')
      expect(forget.status).toBe('stale')
      expect(mockUpdate).not.toHaveBeenCalled()
      expect(mockForget).not.toHaveBeenCalled()
      expect(useScriptInputStore.getState().beginAnswersChange()).toBeNull()
      useScriptInputStore.getState().endAnswersChange(held!)
      expect(useScriptInputStore.getState().answersBusy).toBe(false)
      // 暂存的改动还在，没被拒掉的那一下吃掉
      expect(saveButton().textContent).toBe('保存并重新运行（2）')
    })

    it('换项目清掉旧锁：新项目能存；旧批次的删除回来既不放掉新锁、也不重跑', async () => {
      setCurrentProjectId('A')
      let resolveForget!: (v: Awaited<ReturnType<typeof forgetScriptAnswer>>) => void
      mockForget.mockReturnValueOnce(new Promise((r) => (resolveForget = r)))
      useScriptInputStore.setState({ answers: TWO })
      useScriptInputStore.getState().openManager('pick.py')
      render()
      await forgetRow(dialog()!.querySelector('[data-script-answer="1"]')!)
      await click(saveButton())
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

const rowsOf = () => Array.from(dialog()!.querySelectorAll<HTMLElement>('[data-script-answer]'))

it('same-index configurations have distinct rows; editing and deleting target that row', async () => {
  const rows = [
    { ...ANSWERS['pick.py'][0], run_config: 'rc_a', answer: 'alpha' },
    { ...ANSWERS['pick.py'][0], run_config: 'rc_b', answer: 'beta' },
  ]
  useScriptInputStore.setState({ answers: { 'pick.py': rows } })
  useScriptInputStore.getState().openManager('pick.py')
  render()
  const before = rowsOf()
  expect(before[0].textContent).toContain('rc_a')
  expect(before[1].textContent).toContain('rc_b')
  const box = before[1].querySelector('input')!
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(box, 'new-beta')
    box.dispatchEvent(new Event('input', { bubbles: true }))
    // Reorder incoming rows: the draft must stay with B, not with its old position.
    useScriptInputStore.setState({ answers: { 'pick.py': [rows[1], rows[0]] } })
  })
  const reordered = rowsOf()
  expect(reordered[0]).toBe(before[1])
  expect(reordered[0].querySelector('input')!.value).toBe('new-beta')
  mockUpdate.mockResolvedValue({ scripts: { 'pick.py': [rows[1], rows[0]] }, location: '', pending: [] })
  await click(saveButton())
  expect(mockUpdate).toHaveBeenCalledWith('pick.py', 1, 'new-beta', 'rc_b')
  expect(runSpy).toHaveBeenLastCalledWith('pick.py', 'rc_b')
  // 删除是暂存的：标 rc_a 那一行、脚部提交，forget 只带 rc_a
  await forgetRow(rowsOf()[1])
  await click(saveButton())
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
  await forgetRow(rowsOf()[0])
  await click(saveButton())
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
  for (const [i, v] of ['x1', 'x2', 'x3'].entries()) await typeInto(boxes[i], v)
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

describe('批量保存涉及多份配置：逐份真的重跑（Codex #816 P1）', () => {
  const rows = [
    { ...ANSWERS['pick.py'][0], run_config: 'rc_a', answer: 'alpha' },
    { ...ANSWERS['pick.py'][0], run_config: 'rc_b', answer: 'beta' },
  ]
  const openTwoConfigs = async () => {
    mockUpdate.mockResolvedValue({ scripts: { 'pick.py': rows }, location: '', pending: [] })
    useScriptRunStore.setState({ run: realRun, byScript: {} })
    useScriptInputStore.setState({ answers: { 'pick.py': rows } })
    useScriptInputStore.getState().openManager('pick.py')
    render()
    const boxes = dialog()!.querySelectorAll<HTMLInputElement>('input')
    await typeInto(boxes[0], 'x1')
    await typeInto(boxes[1], 'x2')
  }
  const flush = () => act(async () => { await new Promise((r) => setTimeout(r, 0)) })

  it('真实 run：两份配置都收到探测，第二份等第一份结束才发', async () => {
    const releases: Array<() => void> = []
    mockProbe.mockImplementation(
      () => new Promise((resolve) => releases.push(() => resolve({ descriptors: [] } as never))),
    )
    await openTwoConfigs()
    await click(saveButton())
    await flush()
    // 第一份在飞：第二份不能被同脚本防并发闸吞掉，也不能抢跑
    expect(mockProbe.mock.calls.map((c) => c[2])).toEqual([{ run_config: 'rc_a' }])
    await act(async () => releases[0]())
    await flush()
    expect(mockProbe.mock.calls.map((c) => c[2])).toEqual([{ run_config: 'rc_a' }, { run_config: 'rc_b' }])
    await act(async () => releases[1]())
    await flush()
    expect(useScriptRunStore.getState().byScript['pick.py']?.runConfig).toBe('rc_b')
  })

  it('第一份失败：第二份照样运行，并说出哪一份失败', async () => {
    mockProbe.mockRejectedValueOnce(new Error('boom')).mockResolvedValueOnce({ descriptors: [] } as never)
    await openTwoConfigs()
    await click(saveButton())
    await flush()
    await flush()
    expect(mockProbe.mock.calls.map((c) => c[2])).toEqual([{ run_config: 'rc_a' }, { run_config: 'rc_b' }])
    expect(useUiStore.getState().statusTone).toBe('error')
    expect(JSON.stringify(useUiStore.getState().status)).toContain('manageRerunFailed')
  })

  it('重跑期间换了项目：A 的「某份失败」提示不出现在 B 上（await 之后复核项目）', async () => {
    let reject!: (e: Error) => void
    mockProbe
      .mockImplementationOnce(() => new Promise((_, rej) => (reject = rej)))
      .mockResolvedValue({ descriptors: [] } as never)
    setCurrentProjectId('pj-a')
    try {
      await openTwoConfigs()
      await click(saveButton())
      await flush()
      setCurrentProjectId('pj-b') // 第一份还在飞，用户切走了
      await act(async () => reject(new Error('boom')))
      await flush()
      await flush()
      expect(JSON.stringify(useUiStore.getState().status)).not.toContain('manageRerunFailed')
    } finally {
      setCurrentProjectId(null)
    }
  })

  it('第一份停在运行目录门上：后面的挂起，门有了答案后两份各用自己的 run_config 重跑（Codex #816 r4221169169）', async () => {
    const confirmation = {
      kind: 'workdir', code: 'workdir_confirmation_required', script: 'pick.py', reason: 'script_dir_evidence',
      recommended: 'project', options: [], conflicts: [], reads: [],
    }
    mockProbe
      .mockResolvedValueOnce({
        descriptors: [],
        error: { code: 'workdir_confirmation_required', message: 'x', confirmation },
      } as never)
      .mockResolvedValue({ descriptors: [] } as never)
    await openTwoConfigs()
    await click(saveButton())
    await flush()
    await flush()
    // 门没答之前不发 rc_b（否则它会盖掉停在门上的 rc_a）
    expect(mockProbe.mock.calls.map((c) => c[2])).toEqual([{ run_config: 'rc_a' }])
    expect(useScriptRunStore.getState().byScript['pick.py']?.phase).toBe('needs_workdir')
    expect(JSON.stringify(useUiStore.getState().status)).not.toContain('manageRerunFailed')
    await act(async () => useScriptRunStore.getState().rerunGated('needs_workdir'))
    await flush()
    await flush()
    expect(mockProbe.mock.calls.map((c) => c[2])).toEqual([
      { run_config: 'rc_a' },
      { run_config: 'rc_a' },
      { run_config: 'rc_b' },
    ])
    expect(useScriptRunStore.getState().byScript['pick.py']?.runConfig).toBe('rc_b')
  })
})
