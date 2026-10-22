import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  startConvert: vi.fn(),
  fetchAiSession: vi.fn(),
  fetchConvert: vi.fn(),
  adoptConvert: vi.fn(),
  discardConvert: vi.fn(),
  aiCancel: vi.fn().mockResolvedValue({ ok: true }),
}))

import { aiCancel, fetchAiSession, fetchConvert, startConvert } from '@/lib/api'
import { setCurrentProjectId } from '@/lib/session'
import { useAiStore } from '@/store/aiStore'
import { POLL_MS, useConvertStore } from '@/store/convertStore'

const start = () => useConvertStore.getState().start('plot.py', 'pillow', 'out.png')
const ridOf = (n: number) => vi.mocked(startConvert).mock.calls[n]![0].client_request_id

describe('convertStore', () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['setTimeout'] })
    setCurrentProjectId('A')
    useConvertStore.setState({ entries: {} })
    useAiStore.setState({ caps: { agents: [] } as never })
    vi.mocked(startConvert).mockReset()
    vi.mocked(aiCancel).mockClear()
    vi.mocked(fetchAiSession).mockReset().mockResolvedValue({ status: 'running', final: false } as never)
  })
  afterEach(() => {
    useConvertStore.getState().clear('plot.py')
    vi.useRealTimers()
  })

  it('未决请求 id 按项目分作用域：B 的同名脚本成功不会冲掉 A 的未决 id', async () => {
    vi.mocked(startConvert).mockRejectedValueOnce(new Error('network down'))
    await start()
    expect(useConvertStore.getState().entries['plot.py']?.phase).toBe('failed')
    const ridA = ridOf(0)

    setCurrentProjectId('B')
    vi.mocked(startConvert).mockResolvedValueOnce({ session: 'sB', script: 'plot.py' })
    const b = start()
    await vi.advanceTimersByTimeAsync(0)
    expect(ridOf(1)).not.toBe(ridA)
    useConvertStore.getState().clear('plot.py')
    await vi.advanceTimersByTimeAsync(POLL_MS + 10)
    await b

    setCurrentProjectId('A')
    vi.mocked(startConvert).mockResolvedValueOnce({ session: 'sA', script: 'plot.py' })
    const a = start()
    await vi.advanceTimersByTimeAsync(0)
    expect(ridOf(2)).toBe(ridA)
    useConvertStore.getState().clear('plot.py')
    await vi.advanceTimersByTimeAsync(POLL_MS + 10)
    await a
  })

  it('停止后立刻重试：旧 POST 晚到的失败不覆盖新会话的 running 条目', async () => {
    let rejectOld!: (e: Error) => void
    vi.mocked(startConvert).mockReturnValueOnce(new Promise((_r, rej) => (rejectOld = rej)))
    const first = start()
    await vi.advanceTimersByTimeAsync(0)
    await useConvertStore.getState().stop('plot.py')
    vi.mocked(startConvert).mockResolvedValueOnce({ session: 'new1', script: 'plot.py' })
    const second = start()
    await vi.advanceTimersByTimeAsync(POLL_MS - 1)
    expect(useConvertStore.getState().entries['plot.py']).toMatchObject({ phase: 'running', session: 'new1' })
    rejectOld(new Error('late failure'))
    await first
    expect(useConvertStore.getState().entries['plot.py']).toMatchObject({ phase: 'running', session: 'new1', error: null })
    useConvertStore.getState().clear('plot.py')
    await vi.advanceTimersByTimeAsync(POLL_MS + 10)
    await second
  })

  it('停止后立刻重试复用同一请求 id 时，旧 POST 晚到的成功不取消后端去重出的同一会话', async () => {
    let resolveOld!: (v: { session: string; script: string }) => void
    vi.mocked(startConvert).mockReturnValueOnce(new Promise((r) => (resolveOld = r)))
    const first = start()
    await vi.advanceTimersByTimeAsync(0)
    await useConvertStore.getState().stop('plot.py')
    vi.mocked(startConvert).mockResolvedValueOnce({ session: 'same', script: 'plot.py' })
    const second = start()
    await vi.advanceTimersByTimeAsync(0)
    expect(ridOf(1)).toBe(ridOf(0))
    resolveOld({ session: 'same', script: 'plot.py' })
    await first
    expect(aiCancel).not.toHaveBeenCalled()
    useConvertStore.getState().clear('plot.py')
    await vi.advanceTimersByTimeAsync(POLL_MS + 10)
    await second
  })

  it('旧 POST（同一 id）先成功不清 id：新 attempt 模糊失败后再重试仍是原 id', async () => {
    let resolveOld!: (v: { session: string; script: string }) => void
    vi.mocked(startConvert).mockReturnValueOnce(new Promise((r) => (resolveOld = r)))
    const first = start()
    await vi.advanceTimersByTimeAsync(0)
    await useConvertStore.getState().stop('plot.py')
    let rejectNew!: (e: Error) => void
    vi.mocked(startConvert).mockReturnValueOnce(new Promise((_r, rej) => (rejectNew = rej)))
    const second = start()
    await vi.advanceTimersByTimeAsync(0)
    resolveOld({ session: 'same', script: 'plot.py' })
    await first
    rejectNew(new Error('network down'))
    await second
    expect(useConvertStore.getState().entries['plot.py']?.phase).toBe('failed')
    vi.mocked(startConvert).mockResolvedValueOnce({ session: 'same', script: 'plot.py' })
    const third = start()
    await vi.advanceTimersByTimeAsync(0)
    expect(ridOf(2)).toBe(ridOf(0))
    expect(ridOf(1)).toBe(ridOf(0))
    useConvertStore.getState().clear('plot.py')
    await vi.advanceTimersByTimeAsync(POLL_MS + 10)
    await third
  })

  it('POST 已受理后 fetchConvert 临时失败：点重试不再 POST，续查同一会话', async () => {
    vi.mocked(startConvert).mockResolvedValueOnce({ session: 's1', script: 'plot.py' })
    vi.mocked(fetchAiSession).mockResolvedValue({ status: 'done', final: true, task: { outcome: 'completed' } } as never)
    vi.mocked(fetchConvert).mockReset().mockRejectedValueOnce(new Error('network down'))
    const first = start()
    await vi.advanceTimersByTimeAsync(POLL_MS + 10)
    await first
    expect(useConvertStore.getState().entries['plot.py']).toMatchObject({ phase: 'failed', session: 's1' })
    expect(startConvert).toHaveBeenCalledTimes(1)

    vi.mocked(fetchConvert).mockResolvedValueOnce({ state: 'ready' } as never)
    const retry = start()
    await vi.advanceTimersByTimeAsync(POLL_MS + 10)
    await retry
    expect(startConvert).toHaveBeenCalledTimes(1)
    expect(fetchConvert).toHaveBeenCalledTimes(2)
    expect(useConvertStore.getState().entries['plot.py']).toMatchObject({ phase: 'ready', session: 's1' })
  })
})
