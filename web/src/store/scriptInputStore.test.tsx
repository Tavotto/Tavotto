/**
 * 脚本 `input()` 的事件路由与项目代际（ADR 0099）。经 `handleServerEvent` 驱动——测的是「收到这条事件之后
 * store 变成什么样」，不是 jsdom 的 SSE 实现。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api', async (orig) => {
  const real = await orig<typeof import('@/lib/api')>()
  return {
    ...real,
    fetchScriptAnswers: vi.fn(),
    listenScriptInput: vi.fn().mockResolvedValue({ ok: true }),
    subscribeEvents: vi.fn(() => () => {}),
  }
})

import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { fetchScriptAnswers, listenScriptInput, type ServerEvent } from '@/lib/api'
import { handleServerEvent, useServerEvents } from '@/hooks/useServerEvents'
import { useProjectStore } from '@/store/projectStore'
import { setCurrentProjectId } from '@/lib/session'
import { useScriptInputStore } from '@/store/scriptInputStore'

const mockFetch = vi.mocked(fetchScriptAnswers)

const requested = (pj: string, id = 'q1'): ServerEvent => ({
  kind: 'script.input_requested',
  pj,
  id,
  script: 'pick.py',
  index: 1,
  input_kind: 'input',
  prompt: 'numbers: ',
  stdout_tail: '1. a',
})

beforeEach(() => {
  vi.clearAllMocks()
  setCurrentProjectId('A')
  useScriptInputStore.getState().clear()
})

afterEach(() => {
  useScriptInputStore.getState().clear()
  setCurrentProjectId(null)
})

describe('scriptInputStore', () => {
  it('本项目的问进队列；别的项目的问不进', () => {
    handleServerEvent(requested('B', 'other'))
    handleServerEvent(requested('A'))
    expect(useScriptInputStore.getState().queue.map((q) => q.id)).toEqual(['q1'])
  })

  it('同一问重复到达只排一次；closed 出队', () => {
    handleServerEvent(requested('A'))
    handleServerEvent(requested('A'))
    expect(useScriptInputStore.getState().queue).toHaveLength(1)
    handleServerEvent({ kind: 'script.input_closed', pj: 'A', id: 'q1', reason: 'finished' })
    expect(useScriptInputStore.getState().queue).toHaveLength(0)
  })

  it('自动回填给一条带答案的轻提示', () => {
    handleServerEvent({
      kind: 'script.input_autofilled',
      pj: 'A',
      script: 'pick.py',
      index: 1,
      prompt: 'numbers: ',
      answer: '1,2,4',
    })
    expect(useScriptInputStore.getState().autofilled).toMatchObject({
      script: 'pick.py',
      answer: '1,2,4',
    })
  })

  it('重连后从 pending 把还在等的问接回来，并取到记住的答案', async () => {
    mockFetch.mockResolvedValue({
      scripts: { 'pick.py': [{ index: 1, prompt: 'numbers: ', answer: '2', kind: 'input' }] },
      location: 'tavottofile/_script_inputs.json',
      pending: [
        {
          id: 'q9',
          script: 'pick.py',
          index: 2,
          input_kind: 'input',
          prompt: 'again: ',
          stdout_tail: '',
        },
      ],
    })
    await useScriptInputStore.getState().loadAnswers()
    const s = useScriptInputStore.getState()
    expect(s.queue.map((q) => q.id)).toEqual(['q9'])
    expect(s.answers?.['pick.py']?.[0]?.answer).toBe('2')
  })

  it('发请求之后换了项目：旧项目的答案与问不落进新项目', async () => {
    let resolve!: (v: Awaited<ReturnType<typeof fetchScriptAnswers>>) => void
    mockFetch.mockReturnValue(new Promise((r) => (resolve = r)))
    const pending = useScriptInputStore.getState().loadAnswers()
    useScriptInputStore.getState().clear()
    setCurrentProjectId('B')
    resolve({
      scripts: { 'old.py': [{ index: 1, prompt: 'p', answer: 'x', kind: 'input' }] },
      location: '',
      pending: [
        { id: 'old', script: 'old.py', index: 1, input_kind: 'input', prompt: 'p', stdout_tail: '' },
      ],
    })
    await pending
    const s = useScriptInputStore.getState()
    expect(s.answers).toBeNull()
    expect(s.queue).toEqual([])
  })
})

describe('能答题的事件流报它在看哪个项目（Codex #680 P1）', () => {
  it('收到 hello 就报当前项目；换项目再报一次；没有项目不报', async () => {
    const listen = vi.mocked(listenScriptInput)
    setCurrentProjectId(null)
    handleServerEvent({ kind: 'stream.hello', stream_id: 's1' })
    expect(listen).not.toHaveBeenCalled()
    setCurrentProjectId('A')
    handleServerEvent({ kind: 'stream.hello', stream_id: 's1' })
    expect(listen).toHaveBeenLastCalledWith('s1', 'A')

    // 流 id 跨项目存活：换代不清它
    useScriptInputStore.getState().clear()
    expect(useScriptInputStore.getState().streamId).toBe('s1')

    const host = document.createElement('div')
    const root = createRoot(host)
    function Probe() {
      useServerEvents()
      return null
    }
    ;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
    act(() => root.render(<Probe />))
    // 报项目是串行的：先让 A 那条回来
    await act(async () => {})
    // 切项目：`adoptOpenedProject` 先认领 B、再 await 换代，`project` 最后才赋值。事件过滤从认领那一刻
    // 就按 B 了，所以必须在认领的同一时刻报 B——不能等 `project` 变（Codex #680 P1）
    useProjectStore.setState({ project: { open: true, id: 'A' } as never })
    await act(async () => {})
    listen.mockClear()
    act(() => setCurrentProjectId('B'))
    expect(useProjectStore.getState().project?.id).toBe('A') // 换代还没做完
    expect(listen).toHaveBeenCalledWith('s1', 'B')
    await act(async () => {})
    // 换代做完、`project` 赋值：不再重复报
    act(() => useProjectStore.setState({ project: { open: true, id: 'B' } as never }))
    await act(async () => {})
    expect(listen).toHaveBeenCalledTimes(1)
    // 卸载之后不再报
    act(() => root.unmount())
    setCurrentProjectId('C')
    expect(listen).toHaveBeenCalledTimes(1)
    useProjectStore.setState({ project: null })
  })

  it('报项目串行：前一条回来之前不发下一条，其间只留最新的那份（Codex #680 P1）', async () => {
    const listen = vi.mocked(listenScriptInput)
    const pending: Array<() => void> = []
    listen.mockImplementation(() => new Promise((r) => pending.push(() => r({ ok: true }))))
    useScriptInputStore.setState({ streamId: 's1' })
    const { announce } = useScriptInputStore.getState()
    announce('A')
    announce('B')
    announce('C')
    // 乱序的来源是同时在路上的两条：此刻只许有一条
    expect(listen.mock.calls).toEqual([['s1', 'A']])
    await act(async () => pending.shift()!())
    // A 回来了才发，且发的是最新的 C（B 已经过时，不发）
    expect(listen.mock.calls).toEqual([
      ['s1', 'A'],
      ['s1', 'C'],
    ])
    await act(async () => pending.shift()!())
    expect(listen).toHaveBeenCalledTimes(2)
    listen.mockResolvedValue({ ok: true })
  })
})
