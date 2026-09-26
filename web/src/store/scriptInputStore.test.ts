/**
 * 脚本 `input()` 的事件路由与项目代际（ADR 0099）。经 `handleServerEvent` 驱动——测的是「收到这条事件之后
 * store 变成什么样」，不是 jsdom 的 SSE 实现。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api', async (orig) => {
  const real = await orig<typeof import('@/lib/api')>()
  return { ...real, fetchScriptAnswers: vi.fn() }
})

import { fetchScriptAnswers, type ServerEvent } from '@/lib/api'
import { handleServerEvent } from '@/hooks/useServerEvents'
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
