/**
 * 导入即扫描的前端持有者（T02）。
 *
 * 守五件事：
 *
 * 1. **A→B→A 晚到响应**：旧项目的快照（含在途补拉与轮询）绝不落进新项目——判据是项目换代 +
 *    发请求那一刻认领的项目 id，不是「谁最后返回」；
 * 2. **只认最后发出的那次**，同一轮扫描里 `observation_seq` 不许倒退；
 * 3. **重复认领便宜**：同一项目在途的 `start()` 合并成一次请求；
 * 4. **三个动作不同**：`hide()` 只改呈现、`cancel()` 只打取消端点、`clear()` 换代并停掉轮询；
 * 5. **后端不记得（404）= 丢掉旧快照等下一次 start()**，而不是把旧事实当现状。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  startProjectScan: vi.fn(),
  fetchProjectScan: vi.fn(),
  cancelProjectScan: vi.fn(),
}))

import {
  ApiError,
  cancelProjectScan,
  fetchProjectScan,
  startProjectScan,
  type ProjectScan,
} from '@/lib/api'
import { setCurrentProjectId } from '@/lib/session'
import {
  SLOW_SCAN_MS,
  resetProjectScanBookkeeping,
  useProjectScanStore,
} from './projectScanStore'

const mockStart = vi.mocked(startProjectScan)
const mockFetch = vi.mocked(fetchProjectScan)
const mockCancel = vi.mocked(cancelProjectScan)

const scan = (over: Partial<ProjectScan> = {}): ProjectScan => ({
  scan_version: 1,
  project_id: 'pj-a',
  scan_id: 's1',
  epoch: 1,
  observation_seq: 1,
  reason: 'claim',
  state: 'complete',
  phase: 'completed',
  outcome: { kind: 'static_source' },
  budget: { entries: 3, scripts: 0, assets: 2, elapsed_s: 0.01 },
  issues: [],
  checks: [],
  actions: [],
  ...over,
})

const deferred = <T,>() => {
  let resolve!: (v: T) => void
  let reject!: (e: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

beforeEach(() => {
  vi.useFakeTimers()
  resetProjectScanBookkeeping()
  useProjectScanStore.getState().clear()
  setCurrentProjectId('pj-a')
  mockStart.mockReset()
  mockFetch.mockReset()
  mockCancel.mockReset()
})
afterEach(() => {
  useProjectScanStore.getState().clear()
  vi.useRealTimers()
})

describe('A→B→A 晚到响应', () => {
  it('A 的在途补拉在切到 B 之后才回来：不落进 B', async () => {
    const late = deferred<ProjectScan>()
    mockStart.mockResolvedValueOnce(scan({ state: 'running', phase: 'scanning', outcome: { kind: 'scanning' } }))
    await useProjectScanStore.getState().start()
    mockFetch.mockReturnValueOnce(late.promise)
    const pending = useProjectScanStore.getState().refresh()

    setCurrentProjectId('pj-b')
    useProjectScanStore.getState().clear()
    late.resolve(scan({ scan_id: 'a-late', project_id: 'pj-a' }))
    await pending

    expect(useProjectScanStore.getState().scan).toBeNull()
  })

  it('A→B→A：切回 A 之后，A 第一次留下的旧响应仍然进不来', async () => {
    const late = deferred<ProjectScan>()
    mockFetch.mockReturnValueOnce(late.promise)
    const stale = useProjectScanStore.getState().refresh() // 发自第一次的 A

    setCurrentProjectId('pj-b')
    useProjectScanStore.getState().clear()
    setCurrentProjectId('pj-a')
    useProjectScanStore.getState().clear()
    mockStart.mockResolvedValueOnce(scan({ scan_id: 'fresh', epoch: 2 }))
    await useProjectScanStore.getState().start()

    late.resolve(scan({ scan_id: 'old', epoch: 1 }))
    await stale
    expect(useProjectScanStore.getState().scan?.scan_id).toBe('fresh')
  })

  it('切项目会停掉轮询：换代之后不再有补拉请求', async () => {
    mockStart.mockResolvedValueOnce(scan({ state: 'running', phase: 'scanning', outcome: { kind: 'scanning' } }))
    await useProjectScanStore.getState().start()
    expect(vi.getTimerCount()).toBeGreaterThan(0) // 轮询 + 「慢」计时器确实挂着
    useProjectScanStore.getState().clear()
    expect(vi.getTimerCount()).toBe(0) // 换代把它们都摘了，不留着一个迟早白醒的句柄
    await vi.advanceTimersByTimeAsync(10_000)
    expect(mockFetch).not.toHaveBeenCalled()
  })
})

describe('请求序号与快照序号', () => {
  it('只认最后发出的那次：先发的晚到被丢', async () => {
    const first = deferred<ProjectScan>()
    const second = deferred<ProjectScan>()
    mockFetch.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise)
    const a = useProjectScanStore.getState().refresh()
    const b = useProjectScanStore.getState().refresh()
    second.resolve(scan({ observation_seq: 5 }))
    await b
    first.resolve(scan({ observation_seq: 2 }))
    await a
    expect(useProjectScanStore.getState().scan?.observation_seq).toBe(5)
  })

  it('同一轮扫描里 observation_seq 不许倒退', async () => {
    mockStart.mockResolvedValueOnce(scan({ observation_seq: 7, state: 'running', phase: 'scanning', outcome: { kind: 'scanning' } }))
    await useProjectScanStore.getState().start()
    mockFetch.mockResolvedValueOnce(scan({ observation_seq: 3, state: 'running', phase: 'scanning', outcome: { kind: 'scanning' } }))
    await useProjectScanStore.getState().refresh()
    expect(useProjectScanStore.getState().scan?.observation_seq).toBe(7)
  })
})

describe('权威回包不被请求重排吞掉', () => {
  const running = (over: Partial<ProjectScan> = {}) =>
    scan({ state: 'running', phase: 'scanning', outcome: { kind: 'scanning' }, ...over })

  it('先发的重扫 POST 晚于后发的 GET 返回上一轮终局：POST 的新扫描仍落地并继续轮询', async () => {
    const post = deferred<ProjectScan>()
    mockStart.mockReturnValueOnce(post.promise)
    mockFetch.mockResolvedValueOnce(scan({ scan_id: 'old', observation_seq: 4 }))
    const started = useProjectScanStore.getState().start({ force: true, reason: 'manual' })
    // 迟到的 SSE 提示触发的 GET 在 POST 之后发出，却先带着上一轮终局回来
    await useProjectScanStore.getState().refresh()
    expect(useProjectScanStore.getState().scan?.scan_id).toBe('old')
    post.resolve(running({ scan_id: 'new', observation_seq: 1 }))
    await started
    expect(useProjectScanStore.getState().scan?.scan_id).toBe('new')
    expect(useProjectScanStore.getState().scan?.state).toBe('running')
    // 落地的是运行中快照 => 轮询继续，不会卡在旧结果上
    mockFetch.mockResolvedValueOnce(scan({ scan_id: 'new', observation_seq: 9 }))
    await vi.advanceTimersByTimeAsync(500)
    expect(useProjectScanStore.getState().scan?.observation_seq).toBe(9)
  })

  it('权威回包仍受项目换代约束，且同一轮内 observation_seq 不倒退', async () => {
    const post = deferred<ProjectScan>()
    mockStart.mockReturnValueOnce(post.promise)
    const started = useProjectScanStore.getState().start({ force: true })
    useProjectScanStore.getState().clear()
    post.resolve(running({ scan_id: 'new' }))
    await started
    expect(useProjectScanStore.getState().scan).toBeNull()

    mockFetch.mockResolvedValueOnce(running({ scan_id: 'n2', observation_seq: 8 }))
    await useProjectScanStore.getState().refresh()
    mockStart.mockResolvedValueOnce(running({ scan_id: 'n2', observation_seq: 2 }))
    await useProjectScanStore.getState().start({ force: true })
    expect(useProjectScanStore.getState().scan?.observation_seq).toBe(8)
  })
})

describe('start() 去重', () => {
  it('同一项目在途的 start 合并成一次请求', async () => {
    const d = deferred<ProjectScan>()
    mockStart.mockReturnValueOnce(d.promise)
    const a = useProjectScanStore.getState().start()
    const b = useProjectScanStore.getState().start({ reason: 'restore' })
    expect(mockStart).toHaveBeenCalledTimes(1)
    d.resolve(scan())
    await Promise.all([a, b])
  })

  it('force 总是新发一次，并让被关掉的条回来', async () => {
    mockStart.mockResolvedValue(scan({ scan_id: 's1' }))
    await useProjectScanStore.getState().start()
    useProjectScanStore.getState().hide()
    expect(useProjectScanStore.getState().dismissedScanId).toBe('s1')
    mockStart.mockResolvedValue(scan({ scan_id: 's2' }))
    await useProjectScanStore.getState().start({ force: true, reason: 'manual' })
    expect(mockStart).toHaveBeenLastCalledWith({ force: true, reason: 'manual' })
    expect(useProjectScanStore.getState().dismissedScanId).toBeNull()
  })
})

describe('运行中的轮询与「慢」', () => {
  it('跑得快的扫描不闪：slow 只在超过阈值仍在跑时才亮', async () => {
    mockStart.mockResolvedValueOnce(scan({ state: 'running', phase: 'scanning', outcome: { kind: 'scanning' } }))
    await useProjectScanStore.getState().start()
    expect(useProjectScanStore.getState().slow).toBe(false)
    mockFetch.mockResolvedValue(scan({ state: 'running', phase: 'scanning', outcome: { kind: 'scanning' }, observation_seq: 2 }))
    await vi.advanceTimersByTimeAsync(SLOW_SCAN_MS + 10)
    expect(useProjectScanStore.getState().slow).toBe(true)
  })

  it('终局之后停止轮询、slow 熄灭', async () => {
    mockStart.mockResolvedValueOnce(scan({ state: 'running', phase: 'scanning', outcome: { kind: 'scanning' } }))
    await useProjectScanStore.getState().start()
    mockFetch.mockResolvedValue(scan({ observation_seq: 4 }))
    await vi.advanceTimersByTimeAsync(500)
    const calls = mockFetch.mock.calls.length
    await vi.advanceTimersByTimeAsync(10_000)
    expect(mockFetch.mock.calls.length).toBe(calls)
    expect(useProjectScanStore.getState().slow).toBe(false)
  })
})

describe('三个不同的动作', () => {
  it('hide 只改呈现：不打任何请求', () => {
    useProjectScanStore.setState({ scan: scan() })
    useProjectScanStore.getState().hide()
    expect(useProjectScanStore.getState().dismissedScanId).toBe('s1')
    expect(mockStart).not.toHaveBeenCalled()
    expect(mockCancel).not.toHaveBeenCalled()
  })

  it('cancel 只打取消扫描的端点，随后补拉终局', async () => {
    mockCancel.mockResolvedValueOnce(scan({ state: 'running', phase: 'scanning', outcome: { kind: 'scanning' } }))
    mockFetch.mockResolvedValueOnce(scan({ state: 'cancelled', phase: 'cancelled', outcome: { kind: 'cancelled' } }))
    await useProjectScanStore.getState().cancel()
    expect(mockCancel).toHaveBeenCalledTimes(1)
    expect(useProjectScanStore.getState().scan?.state).toBe('cancelled')
  })

  it('reopen 让条在没有需要关注的事时也显示', () => {
    useProjectScanStore.setState({ scan: scan(), dismissedScanId: 's1' })
    useProjectScanStore.getState().reopen()
    expect(useProjectScanStore.getState()).toMatchObject({ dismissedScanId: null, forced: true })
  })
})

describe('失败语义', () => {
  it('后端不记得（404）：丢掉旧快照，等下一次 start()', async () => {
    useProjectScanStore.setState({ scan: scan() })
    mockFetch.mockRejectedValueOnce(new ApiError('x', 404, { code: 'project_scan_not_started' }))
    await useProjectScanStore.getState().refresh()
    expect(useProjectScanStore.getState().scan).toBeNull()
  })

  it('补拉失败保留上一次成功的快照', async () => {
    useProjectScanStore.setState({ scan: scan() })
    mockFetch.mockRejectedValueOnce(new Error('network'))
    await useProjectScanStore.getState().refresh()
    expect(useProjectScanStore.getState().scan?.scan_id).toBe('s1')
    expect(useProjectScanStore.getState().error).toBe('network')
  })
})
