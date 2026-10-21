import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  ApiError,
  approveNativePending,
  buildNativeSession,
  cancelNativePending,
  continueNativeSession,
  detachNativeSession,
  fetchNativePending,
  fetchNativeSessions,
  fetchPanels,
  fetchRuntimeAssets,
  terminateNativeSession,
  type CapturedFigureDescriptor,
  type FigureSnapshotInvalidation,
  type NativePending,
  type NativeSessionInfo,
  type NativeSessionState,
} from '@/lib/api'
import { addRuntimePanelToCanvas } from '@/store/workspace'
import { setCurrentProjectId } from '@/lib/session'
import { beginRestore, resetRestoreGateForTests, settleRestore, useDocumentStore } from '@/store/documentStore'
import { useFigurePickerStore } from '@/store/figurePickerStore'
import { registerFigureExecutionConsumer } from '@/lib/figureExecutionEvents'
import { nativePanelState, sortSessions, useNativeSessionStore } from './nativeSessionStore'

vi.mock('@/lib/api', async (orig) => {
  const real = await orig<typeof import('@/lib/api')>()
  return {
    ...real,
    fetchNativePending: vi.fn(),
    approveNativePending: vi.fn(),
    cancelNativePending: vi.fn().mockResolvedValue({ cancelled: true }),
    fetchNativeSessions: vi.fn().mockResolvedValue({ sessions: [] }),
    buildNativeSession: vi.fn(),
    continueNativeSession: vi.fn(),
    detachNativeSession: vi.fn(),
    terminateNativeSession: vi.fn(),
    // 相邻 store 的副作用：本文件只量状态机，让它们安静成功
    fetchPanels: vi.fn().mockResolvedValue({ figures_dir: '', panels: [] }),
    fetchRuntimeAssets: vi.fn().mockResolvedValue({ assets: [] }),
    fetchRuntimeStatus: vi.fn(),
  }
})

vi.mock('@/store/workspace', () => ({ addRuntimePanelToCanvas: vi.fn() }))
// These tests cover entry admission; replay deduplication belongs to the snapshot coordinator.
vi.mock('./figureSnapshotInvalidation', () => ({ hasAcceptedFigureExecution: () => false }))
const acceptedExecution = vi.fn()

const mockPending = vi.mocked(fetchNativePending)
const mockApprove = vi.mocked(approveNativePending)
const mockCancel = vi.mocked(cancelNativePending)
const mockBuild = vi.mocked(buildNativeSession)
const mockContinue = vi.mocked(continueNativeSession)
const mockDetach = vi.mocked(detachNativeSession)
const mockTerminate = vi.mocked(terminateNativeSession)
const mockList = vi.mocked(fetchNativeSessions)
const mockAddPanel = vi.mocked(addRuntimePanelToCanvas)

const ID = '0123456789abcdef0123456789abcdef'

const pending = (over: Partial<NativePending> = {}): NativePending => ({
  native_id: ID,
  created_at: 1,
  expires_at: 999,
  project_root: '/p/figures',
  interpreter: '/p/.venv/bin/python',
  cwd: '/p',
  target_kind: 'script',
  target_display: 'fig.py',
  arg_count: 0,
  command_fingerprint: 'sha256:c',
  permission_key: 'key',
  python_version: '3.12.1',
  remembered: false,
  ...over,
})

const session = (over: Partial<NativeSessionInfo> = {}): NativeSessionInfo => ({
  session_id: `native-${ID}`,
  project_root: '/p/figures',
  interpreter: '/p/.venv/bin/python',
  interpreter_fingerprint: 'fp',
  target_kind: 'script',
  target_display: 'fig.py',
  cwd: '/p',
  arg_count: 0,
  python_version: '3.12.1',
  state: 'starting_python' as NativeSessionState,
  barrier_reason: '',
  process_pid: 4242,
  stems: [],
  descriptors: [],
  script_error: null,
  terminal_error: null,
  exit_code: null,
  figures_captured: 0,
  started_at: 100,
  last_event_at: 100,
  sequence: 1,
  editable: false,
  ...over,
})

const desc = (stem: string): CapturedFigureDescriptor => ({
  asset_id: `runtime:fig.py#${stem}`,
  script: 'fig.py',
  entry: '__main__',
  stem,
  capture_source: 'pyplot',
  execution_profile: 'native',
  original_artifact: null,
  size_mm: [100, 80],
  source_fingerprint: 'sha256:x',
  can_writeback_artifact: false,
  can_writeback_source: false,
})

/** 真的 `ApiError`——`toNativeError` 认的是它，随手造一个同形状的类认不出来。 */
const apiError = (code: string, status = 409, params?: Record<string, unknown>) =>
  new ApiError('后端原文', status, { code, params })

beforeEach(() => {
  vi.resetAllMocks()
  store().clear()
  registerFigureExecutionConsumer(acceptedExecution)
  resetRestoreGateForTests()
  setCurrentProjectId('p_a')
  useNativeSessionStore.setState({
    epoch: 0,
    pendingQueue: [],
    sessions: {},
    busy: {},
    errors: {},
    conflicts: {},
    builtSeq: {},
    executedBarrierSeq: {},
  })
  useDocumentStore.setState((s) => ({ doc: { ...s.doc, objects: [] } }))
  useFigurePickerStore.getState().close()
  mockList.mockResolvedValue({ sessions: [] })
  mockCancel.mockResolvedValue({ cancelled: true })
  // `resetAllMocks` 连 vi.mock 工厂里设的默认值一起清掉，相邻 store 的
  // 只读刷新要重新给一次（不给的话它们返回 undefined，`.then` 当场抛）
  vi.mocked(fetchRuntimeAssets).mockResolvedValue({ assets: [] })
  vi.mocked(fetchPanels).mockResolvedValue({ figures_dir: '', panels: [] })
})

const store = () => useNativeSessionStore.getState()

/**
 * 直接把会话摆到某个状态。
 *
 * 刻意**不走 `applyEvent`**：进 barrier 会顺带触发一次 build，而 build 期间
 * 这条会话是忙的（单 reader 传输上本来就不该同时有两条请求在飞）。想量
 * 「动作互斥」的用例如果用 applyEvent 起手，量到的会是 build 那把闸。
 */
const seed = (over: Partial<NativeSessionInfo> = {}) => {
  const one = session(over)
  useNativeSessionStore.setState({ sessions: { [one.session_id]: one } })
  return one
}

describe('待确认的交接', () => {
  it('取回 descriptor 之后展示的是那条 invocation，前端只拿得到 ID', async () => {
    mockPending.mockResolvedValue({ pending: pending({ arg_count: 3 }) })
    await store().receive(ID)
    const head = store().pendingQueue[0]
    expect(head.info?.interpreter).toBe('/p/.venv/bin/python')
    expect(head.info?.arg_count).toBe(3)
    // 提交侧只有 remember：host/port/token/命令一律由后端从 descriptor 读
    expect(mockPending).toHaveBeenCalledWith(ID)
  })

  it('已经"记住"过的组合直接批准，不再问一次', async () => {
    mockPending.mockResolvedValue({ pending: pending({ remembered: true }) })
    mockApprove.mockResolvedValue({ session: session() })
    await store().receive(ID)
    expect(mockApprove).toHaveBeenCalledWith(ID, false)
    expect(store().pendingQueue).toHaveLength(0)
    expect(Object.keys(store().sessions)).toHaveLength(1)
  })

  it('没记住过就必须问：不自动批准', async () => {
    mockPending.mockResolvedValue({ pending: pending({ remembered: false }) })
    await store().receive(ID)
    expect(mockApprove).not.toHaveBeenCalled()
    expect(store().pendingQueue).toHaveLength(1)
  })

  it('两个终端同时跑：两条都排队，不是留一个丢一个', async () => {
    const other = 'ffffffffffffffffffffffffffffffff'
    mockPending.mockImplementation(async (id) => ({ pending: pending({ native_id: id }) }))
    await store().receive(ID)
    await store().receive(other)
    expect(store().pendingQueue.map((p) => p.native_id)).toEqual([ID, other])
  })

  it('同一条 ID 来两次（首启 URL + 单实例事件）只排一次', async () => {
    mockPending.mockResolvedValue({ pending: pending() })
    await store().receive(ID)
    await store().receive(ID)
    expect(store().pendingQueue).toHaveLength(1)
    expect(mockPending).toHaveBeenCalledTimes(1)
  })

  it('**自动批准批的是被记住的那一条，不是队首**（P1-A）', async () => {
    // 判据的主语（B 记住过）和动作的主语（队首是 A）在队列里不止一条时就
    // 分开了。批错的后果不是"多点一次"，是 **A 的 Python 在用户点任何东西
    // 之前就跑起来了**——`tavotto run` 最核心的那句承诺当场失效。
    const other = 'ffffffffffffffffffffffffffffffff'
    mockPending.mockImplementation(async (id) => ({
      pending: pending({ native_id: id, remembered: id === other }),
    }))
    mockApprove.mockResolvedValue({ session: session() })

    await store().receive(ID) // A：没记住，排在队首，等用户确认
    await store().receive(other) // B：记住过 → 应该自动批准 **B**

    expect(mockApprove).toHaveBeenCalledTimes(1)
    expect(mockApprove).toHaveBeenCalledWith(other, false)
    // A 还在队列里等着——它从来没被确认过
    expect(store().pendingQueue.map((p) => p.native_id)).toEqual([ID])
  })

  it('批准点名的那一条，队首在不在都不影响', async () => {
    const other = 'ffffffffffffffffffffffffffffffff'
    mockPending.mockImplementation(async (id) => ({ pending: pending({ native_id: id }) }))
    mockApprove.mockResolvedValue({ session: session() })
    await store().receive(ID)
    await store().receive(other)

    await store().approve(other, true)

    expect(mockApprove).toHaveBeenCalledWith(other, true)
    expect(store().pendingQueue.map((p) => p.native_id)).toEqual([ID])
  })

  it('**换项目不动待确认队列**（P1-B）', async () => {
    // pending 不属于任何一个界面项目：它自带 project / interpreter / cwd，
    // attach 也不看界面此刻开着哪个项目。跟着清的表现是终端 1 的那条**既没
    // 批准也没取消**地消失，而它白等满 300 秒的 attach 超时。
    mockPending.mockResolvedValue({ pending: pending() })
    await store().receive(ID)
    expect(store().pendingQueue).toHaveLength(1)

    store().clear() // 换项目

    expect(store().pendingQueue.map((p) => p.native_id)).toEqual([ID])
  })

  it('换项目之后那条 pending 仍然批得动（代际不该作废它）', async () => {
    mockPending.mockResolvedValue({ pending: pending() })
    await store().receive(ID)
    store().clear()
    mockApprove.mockResolvedValue({ session: session() })

    await store().approve(ID, false)

    expect(mockApprove).toHaveBeenCalledWith(ID, false)
    expect(store().pendingQueue).toHaveLength(0)
  })

  it('换项目仍然清掉 live 会话（那才是项目状态）', () => {
    seed({ state: 'barrier', sequence: 4 })
    store().clear()
    expect(store().sessions).toEqual({})
  })

  it('取不到（过期/已处理）时留下的是一条错误，不是一个转圈的对话框', async () => {
    mockPending.mockRejectedValue(apiError('native_handoff_expired'))
    await store().receive(ID)
    const head = store().pendingQueue[0]
    expect(head.loading).toBe(false)
    expect(head.error?.code).toBe('native_handoff_expired')
    // 存的是 code，不是翻好的句子（切语言之后还要能换）
    expect(head.error).not.toHaveProperty('text')
  })

  it('批准失败：**留在队列里**并说明原因，不悄悄关掉', async () => {
    mockPending.mockResolvedValue({ pending: pending() })
    await store().receive(ID)
    mockApprove.mockRejectedValue(apiError('environment_mutating'))
    await store().approve(ID, true)
    const head = store().pendingQueue[0]
    expect(head.native_id).toBe(ID)
    expect(head.submitting).toBe(false)
    expect(head.error?.code).toBe('environment_mutating')
  })

  it('关闭点名的那一条，不是"丢队首"', async () => {
    // 上一版是 slice(1)。今天它碰巧安全（确认屏渲染的就是队首），但那份
    // 安全性依赖的是**另一个文件里的渲染约定**——与 P1-A 同一类跨文件不变式。
    const other = 'ffffffffffffffffffffffffffffffff'
    mockPending.mockImplementation(async (id) => ({ pending: pending({ native_id: id }) }))
    await store().receive(ID)
    await store().receive(other)

    store().dismissPending(other)

    expect(store().pendingQueue.map((p) => p.native_id)).toEqual([ID])
  })

  it('取消：即使取消请求本身失败也照样出队', async () => {
    mockPending.mockResolvedValue({ pending: pending() })
    await store().receive(ID)
    mockCancel.mockRejectedValue(new Error('网络断了'))
    await store().cancel(ID)
    expect(store().pendingQueue).toHaveLength(0)
  })

  it('`记住` 是用户勾的那个值，原样送到后端', async () => {
    mockPending.mockResolvedValue({ pending: pending() })
    mockApprove.mockResolvedValue({ session: session() })
    await store().receive(ID)
    await store().approve(ID, true)
    expect(mockApprove).toHaveBeenCalledWith(ID, true)
  })
})

describe('事件按 sequence 落地', () => {
  it('迟到的旧事件不覆盖新状态', () => {
    store().applyEvent(session({ sequence: 5, state: 'barrier' }))
    store().applyEvent(session({ sequence: 3, state: 'running_script' }))
    expect(store().sessions[`native-${ID}`].state).toBe('barrier')
  })

  it('终态不回头——脚本退出之后不会又变回"正在运行"', () => {
    store().applyEvent(session({ sequence: 7, state: 'ended', exit_code: 0 }))
    store().applyEvent(session({ sequence: 2, state: 'running_script' }))
    expect(store().sessions[`native-${ID}`].state).toBe('ended')
  })

  it('序号更大的事件照常落地', () => {
    store().applyEvent(session({ sequence: 1, state: 'starting_python' }))
    store().applyEvent(session({ sequence: 2, state: 'waiting_for_figure' }))
    expect(store().sessions[`native-${ID}`].state).toBe('waiting_for_figure')
  })
})

const executionFact = (over: Partial<FigureSnapshotInvalidation> = {}): FigureSnapshotInvalidation => ({
  scope: 'sources', script: 'fig.py', run_config: null,
  source_ids: ['runtime:fig.py#previous'], execution_id: 'native-execution-1',
  ...over,
})

describe('屏障 → build → 图进画布', () => {
  it('a new native actual-start event is accepted even when the script fails without capture', () => {
    store().applyEvent(session({ state: 'starting_python', sequence: 1 }))
    const fact = executionFact()
    store().applyEvent(session({
      state: 'failed', sequence: 3, figure_snapshot_invalidation: fact,
      script_error: { type: 'RuntimeError', message: 'failed' },
    }))
    expect(acceptedExecution).toHaveBeenCalledExactlyOnceWith(fact, 'p_a')
    expect(mockBuild).not.toHaveBeenCalled()
  })

  it('initial session facts stay passive baselines through later refreshes and builds', async () => {
    const barrier = session({ state: 'barrier', sequence: 4, figure_snapshot_invalidation: executionFact() })
    mockBuild.mockResolvedValue({ session: barrier, stems: {}, descriptors: [desc('Fig1')] })
    mockList.mockResolvedValue({ sessions: [barrier] })
    await store().refresh('/p/figures')
    await store().build(`native-${ID}`)
    expect(acceptedExecution).not.toHaveBeenCalled()
    mockList.mockResolvedValue({ sessions: [{ ...barrier, sequence: 5 }] })
    await store().refresh('/p/figures')
    expect(acceptedExecution).not.toHaveBeenCalled()
    store().clear()
    store().applyEvent(barrier)
    await vi.waitFor(() => expect(mockBuild).toHaveBeenCalledTimes(2))
    expect(acceptedExecution).not.toHaveBeenCalled()
  })

  it('a newer refresh admits a new token after the baseline even for a cancelled execution', async () => {
    mockList.mockResolvedValueOnce({ sessions: [session({
      state: 'running_script', sequence: 2, figure_snapshot_invalidation: executionFact(),
    })] })
    await store().refresh('/p/figures')
    const nextFact = executionFact({ execution_id: 'native-execution-2' })
    mockList.mockResolvedValueOnce({ sessions: [session({
      state: 'ended', sequence: 5, cancelled: true, figure_snapshot_invalidation: nextFact,
    })] })
    await store().refresh('/p/figures')
    expect(acceptedExecution).toHaveBeenCalledExactlyOnceWith(nextFact, 'p_a')
  })

  it('running/barrier transitions and successful descriptors cannot replace a missing execution fact', async () => {
    const barrier = session({ state: 'barrier', sequence: 4 })
    mockBuild.mockResolvedValue({ session: barrier, stems: {}, descriptors: [desc('Fig1')] })
    store().applyEvent(session({ state: 'running_script', sequence: 3 }))
    store().applyEvent(barrier)
    await vi.waitFor(() => expect(mockAddPanel).toHaveBeenCalledTimes(1))
    expect(acceptedExecution).not.toHaveBeenCalled()
  })

  it('a build after an observed start accepts a trusted fact even with zero captured figures', async () => {
    const barrier = session({ state: 'barrier', sequence: 4 })
    const fact = executionFact({ source_ids: [], receipt_ids: ['receipt-old'] })
    mockBuild.mockResolvedValue({ session: barrier, stems: {}, descriptors: [], figure_snapshot_invalidation: fact })
    store().applyEvent(session({ state: 'running_script', sequence: 3 }))
    store().applyEvent(barrier)
    await vi.waitFor(() => expect(acceptedExecution).toHaveBeenCalledExactlyOnceWith(fact, 'p_a'))
    expect(mockAddPanel).not.toHaveBeenCalled()
  })

  it.each([
    { script_error: { type: 'RuntimeError', message: 'failed' } },
    { terminal_error: { code: 'disconnected' } },
    { cancelled: true },
  ])('a trusted build fact is accepted after a failed/cancelled execution: %j', async (failure) => {
    const barrier = session({ state: 'barrier', sequence: 4, ...failure })
    const fact = executionFact()
    mockBuild.mockResolvedValue({ session: barrier, stems: {}, descriptors: [], figure_snapshot_invalidation: fact })
    store().applyEvent(session({ state: 'running_script', sequence: 3 }))
    store().applyEvent(barrier)
    await vi.waitFor(() => expect(store().busy[`native-${ID}`]).toBe(false))
    expect(acceptedExecution).toHaveBeenCalledExactlyOnceWith(fact, 'p_a')
  })

  it('an HTTP build failure carries actual-start evidence independently of capture success', async () => {
    const barrier = session({ state: 'barrier', sequence: 4 })
    const fact = executionFact()
    mockBuild.mockRejectedValueOnce(new ApiError('capture failed', 500, {
      code: 'native_capture_failed', figure_snapshot_invalidation: fact,
    }))
    store().applyEvent(session({ state: 'running_script', sequence: 3 }))
    store().applyEvent(barrier)
    await vi.waitFor(() => expect(store().errors[`native-${ID}`]?.code).toBe('native_capture_failed'))
    expect(acceptedExecution).toHaveBeenCalledExactlyOnceWith(fact, 'p_a')
  })

  it('a failed build without a trusted fact preserves snapshots', async () => {
    mockBuild.mockRejectedValueOnce(new Error('capture failed'))
    store().applyEvent(session({ state: 'running_script', sequence: 3 }))
    store().applyEvent(session({ state: 'barrier', sequence: 4 }))
    await vi.waitFor(() => expect(store().errors[`native-${ID}`]?.code).toBe('internal_error'))
    expect(acceptedExecution).not.toHaveBeenCalled()
  })

  it.each(['success', 'failure'] as const)('a %s build response with a different execution token is rejected', async (outcome) => {
    const expected = executionFact()
    const wrong = executionFact({ execution_id: 'native-stale-execution', source_ids: ['runtime:other.py#wrong'] })
    const barrier = session({ state: 'barrier', sequence: 4, figure_snapshot_invalidation: expected })
    if (outcome === 'success') mockBuild.mockResolvedValueOnce({
      session: barrier, stems: {}, descriptors: [], figure_snapshot_invalidation: wrong,
    })
    else mockBuild.mockRejectedValueOnce(new ApiError('capture failed', 500, {
      code: 'native_capture_failed', figure_snapshot_invalidation: wrong,
    }))
    store().applyEvent(session({ state: 'running_script', sequence: 3 }))
    store().applyEvent(barrier)
    await vi.waitFor(() => expect(store().busy[`native-${ID}`]).toBe(false))
    expect(acceptedExecution).toHaveBeenCalledExactlyOnceWith(expected, 'p_a')
  })

  it.each(['success', 'failure'] as const)('a late %s build cannot admit old receipt deltas after a newer session token', async (outcome) => {
    const expected = executionFact()
    const barrier = session({ state: 'barrier', sequence: 4, figure_snapshot_invalidation: expected })
    let resolve!: (r: Awaited<ReturnType<typeof buildNativeSession>>) => void
    let reject!: (e: unknown) => void
    mockBuild.mockImplementationOnce(() => new Promise((yes, no) => { resolve = yes; reject = no }))
    store().applyEvent(session({ state: 'running_script', sequence: 3 }))
    store().applyEvent(barrier)
    const next = executionFact({ execution_id: 'native-execution-2' })
    store().applyEvent(session({ state: 'running_script', sequence: 5, figure_snapshot_invalidation: next }))
    acceptedExecution.mockClear()
    const late = { ...expected, receipt_ids: ['old-worker-late-receipt'] }
    if (outcome === 'success') resolve({ session: barrier, stems: {}, descriptors: [], figure_snapshot_invalidation: late })
    else reject(new ApiError('capture failed', 500, { figure_snapshot_invalidation: late }))
    await vi.waitFor(() => expect(store().busy[`native-${ID}`]).toBe(false))
    expect(acceptedExecution).not.toHaveBeenCalled()
  })

  it('a known current execution can add receipt evidence through a retry error after an earlier successful build', async () => {
    const expected = executionFact()
    const barrier = session({ state: 'barrier', sequence: 4, figure_snapshot_invalidation: expected })
    mockBuild.mockResolvedValueOnce({ session: barrier, stems: {}, descriptors: [], figure_snapshot_invalidation: expected })
    store().applyEvent(session({ state: 'running_script', sequence: 3 }))
    store().applyEvent(barrier)
    await vi.waitFor(() => expect(store().busy[`native-${ID}`]).toBe(false))
    expect(store().executedBarrierSeq[`native-${ID}`]).toBe(-1)
    acceptedExecution.mockClear()
    const delta = { ...expected, receipt_ids: ['late-archive-receipt'] }
    mockBuild.mockRejectedValueOnce(new ApiError('capture failed', 500, { figure_snapshot_invalidation: delta }))
    await store().build(`native-${ID}`)
    expect(acceptedExecution).toHaveBeenCalledExactlyOnceWith(delta, 'p_a')
  })

  it('unknown-config native execution keeps source scope and exact backend bindings', () => {
    const fact = executionFact({ source_ids: ['runtime:fig.py#Fig1@variant-a'], receipt_ids: ['receipt-a'] })
    store().applyEvent(session({ state: 'starting_python', sequence: 1 }))
    store().applyEvent(session({ state: 'running_script', sequence: 2, figure_snapshot_invalidation: fact }))
    expect(acceptedExecution).toHaveBeenCalledExactlyOnceWith(fact, 'p_a')
    store().applyEvent(session({ state: 'running_script', sequence: 2, figure_snapshot_invalidation: fact }))
    expect(acceptedExecution).toHaveBeenCalledTimes(1)
    // The same execution can gain bindings later; deduplication belongs to the coordinator.
    const enriched = { ...fact, source_ids: [...fact.source_ids, 'runtime:fig.py#Fig2@variant-a'] }
    store().applyEvent(session({ state: 'running_script', sequence: 3, figure_snapshot_invalidation: enriched }))
    expect(acceptedExecution).toHaveBeenLastCalledWith(enriched, 'p_a')
  })

  it('actual execution is admitted while placement waits for restoration', async () => {
    const token = beginRestore('p_a')
    settleRestore(token, {
      state: 'needs_user',
      issue: { pj: 'p_a', gen: token.gen, kind: 'index_degraded', ref: null, alternatives: [] },
    })
    const barrier = session({ state: 'barrier', sequence: 4 })
    const fact = executionFact()
    mockBuild.mockResolvedValue({ session: barrier, stems: {}, descriptors: [desc('Fig1')], figure_snapshot_invalidation: fact })
    store().applyEvent(session({ state: 'running_script', sequence: 3 }))
    store().applyEvent(barrier)
    await vi.waitFor(() => expect(acceptedExecution).toHaveBeenCalledExactlyOnceWith(fact, 'p_a'))
    expect(mockAddPanel).not.toHaveBeenCalled()
    settleRestore(beginRestore('p_a'), { state: 'ready', reason: 'empty' })
    await vi.waitFor(() => expect(mockAddPanel).toHaveBeenCalledTimes(1))
    expect(acceptedExecution).toHaveBeenCalledTimes(1)
  })

  it('an accepted fact remains registered but stale placement cannot revive after A → B → A', async () => {
    beginRestore('p_a')
    const barrier = session({ state: 'barrier', sequence: 4 })
    mockBuild.mockResolvedValue({
      session: barrier, stems: {}, descriptors: [desc('Fig1')], figure_snapshot_invalidation: executionFact(),
    })
    store().applyEvent(session({ state: 'running_script', sequence: 3 }))
    store().applyEvent(barrier)
    await vi.waitFor(() => expect(acceptedExecution).toHaveBeenCalledTimes(1))
    setCurrentProjectId('p_b')
    setCurrentProjectId('p_a')
    settleRestore(beginRestore('p_a'), { state: 'ready', reason: 'empty' })
    await Promise.resolve()
    expect(acceptedExecution).toHaveBeenCalledTimes(1)
    expect(mockAddPanel).not.toHaveBeenCalled()
  })

  it('late native build evidence is discarded after a project A → B → A round trip', async () => {
    const barrier = session({ state: 'barrier', sequence: 4 })
    let resolve!: (r: Awaited<ReturnType<typeof buildNativeSession>>) => void
    mockBuild.mockImplementationOnce(() => new Promise((r) => { resolve = r }))
    store().applyEvent(session({ state: 'running_script', sequence: 3 }))
    store().applyEvent(barrier)
    setCurrentProjectId('p_b')
    setCurrentProjectId('p_a')
    resolve({ session: barrier, stems: {}, descriptors: [desc('Fig1')], figure_snapshot_invalidation: executionFact() })
    await Promise.resolve()
    expect(acceptedExecution).not.toHaveBeenCalled()
    expect(mockAddPanel).not.toHaveBeenCalled()
  })

  it('恢复待用户处理时保留原生交接，打开文档之后才放入一次', async () => {
    const token = beginRestore('p_a')
    settleRestore(token, {
      state: 'needs_user',
      issue: { pj: 'p_a', gen: token.gen, kind: 'index_degraded', ref: null, alternatives: [] },
    })
    mockBuild.mockResolvedValue({
      session: session({ state: 'barrier', sequence: 4 }),
      stems: {},
      descriptors: [desc('Fig1')],
    })
    seed({ state: 'barrier', sequence: 4 })
    await store().build(`native-${ID}`)
    expect(mockAddPanel).not.toHaveBeenCalled()
    settleRestore(beginRestore('p_a'), { state: 'ready', reason: 'empty' })
    await vi.waitFor(() => expect(mockAddPanel).toHaveBeenCalledTimes(1))
  })

  it('等待恢复期间切项目，原生交接不放到另一个项目', async () => {
    const token = beginRestore('p_a')
    settleRestore(token, {
      state: 'needs_user',
      issue: { pj: 'p_a', gen: token.gen, kind: 'index_degraded', ref: null, alternatives: [] },
    })
    mockBuild.mockResolvedValue({
      session: session({ state: 'barrier', sequence: 4 }),
      stems: {},
      descriptors: [desc('Fig1')],
    })
    seed({ state: 'barrier', sequence: 4 })
    await store().build(`native-${ID}`)
    setCurrentProjectId('p_b')
    const next = beginRestore('p_b')
    settleRestore(next, { state: 'ready', reason: 'empty' })
    await Promise.resolve()
    expect(mockAddPanel).not.toHaveBeenCalled()
  })

  it('停在屏障上时由界面显式发一次 build', async () => {
    mockBuild.mockResolvedValue({
      session: session({ state: 'barrier', sequence: 4 }),
      stems: {},
      descriptors: [desc('Fig1')],
    })
    store().applyEvent(session({ state: 'barrier', sequence: 4 }))
    await vi.waitFor(() => expect(mockBuild).toHaveBeenCalledWith(`native-${ID}`))
    expect(mockAddPanel).toHaveBeenCalledTimes(1)
  })

  it('桌面拥有、由准备会话管理的会话停在屏障上：不 build、不放置、不开选择器（交给准备会话的「进入编辑」，V2 PR4）', async () => {
    mockBuild.mockResolvedValue({
      session: session({ state: 'barrier', sequence: 4, owner: 'desktop' }),
      stems: {},
      descriptors: [desc('Fig1'), desc('Fig2')],
    })
    store().applyEvent(
      session({ state: 'barrier', sequence: 4, owner: 'desktop', descriptors: [desc('Fig1'), desc('Fig2')] }),
    )
    await Promise.resolve()
    await Promise.resolve()
    expect(mockBuild).not.toHaveBeenCalled()
    expect(mockAddPanel).not.toHaveBeenCalled()
    expect(useFigurePickerStore.getState().script).toBeNull()
    // 状态仍然进 store：面板的「能不能编辑」读它
    expect(store().sessions[`native-${ID}`].state).toBe('barrier')
  })

  it('终端拥有的会话照旧在屏障处 build 并放置（过滤只认 owner===desktop；老后端没有 owner 也照旧）', async () => {
    mockBuild.mockResolvedValue({
      session: session({ state: 'barrier', sequence: 4, owner: 'terminal' }),
      stems: {},
      descriptors: [desc('Fig1')],
    })
    store().applyEvent(session({ state: 'barrier', sequence: 4, owner: 'terminal' }))
    await vi.waitFor(() => expect(mockBuild).toHaveBeenCalledTimes(1))
    expect(mockAddPanel).toHaveBeenCalledTimes(1)
  })

  it('不在屏障上绝不 build——那条 409 描述的是正常状态，不是故障', async () => {
    store().applyEvent(session({ state: 'running_script', sequence: 2 }))
    await store().build(`native-${ID}`)
    expect(mockBuild).not.toHaveBeenCalled()
  })

  it('同一个屏障不重复 build', async () => {
    mockBuild.mockResolvedValue({
      session: session({ state: 'barrier', sequence: 4 }),
      stems: {},
      descriptors: [],
    })
    store().applyEvent(session({ state: 'barrier', sequence: 4 }))
    await vi.waitFor(() => expect(mockBuild).toHaveBeenCalledTimes(1))
    // 同一序号再来一次（SSE 补发）不该再打一次后端
    store().applyEvent(session({ state: 'barrier', sequence: 4 }))
    expect(mockBuild).toHaveBeenCalledTimes(1)
  })

  it('多张图打开选择器，**绝不静默选第一张**', async () => {
    mockBuild.mockResolvedValue({
      session: session({ state: 'barrier', sequence: 4 }),
      stems: {},
      descriptors: [desc('Fig1'), desc('Fig2')],
    })
    store().applyEvent(session({ state: 'barrier', sequence: 4 }))
    await vi.waitFor(() => expect(useFigurePickerStore.getState().script).toBe('fig.py'))
    expect(mockAddPanel).not.toHaveBeenCalled()
  })

  it('画布上已经有这张图就只选中，不叠第二份', async () => {
    const fileId = `runtime:fig.py#Fig1`
    useDocumentStore.setState((s) => ({
      doc: {
        ...s.doc,
        objects: [
          {
            id: 'p1',
            type: 'panel',
            fileId,
            fileKind: 'runtime',
            x: 0,
            y: 0,
            w: 10,
            h: 10,
            nativeW: 10,
            nativeH: 10,
            overrides: [],
            name: 'Fig1',
          } as never,
        ],
      },
    }))
    mockBuild.mockResolvedValue({
      session: session({ state: 'barrier', sequence: 4 }),
      stems: {},
      descriptors: [desc('Fig1')],
    })
    store().applyEvent(session({ state: 'barrier', sequence: 4 }))
    await vi.waitFor(() => expect(mockBuild).toHaveBeenCalled())
    expect(mockAddPanel).not.toHaveBeenCalled()
  })

  it('冲突**如实报**：被另一条会话占着的 stem 记下来给界面显示', async () => {
    mockBuild.mockResolvedValue({
      session: session({ state: 'barrier', sequence: 4 }),
      stems: {},
      descriptors: [],
      conflicts: { code: 'native_asset_conflict', stems: ['Fig1'] },
    })
    store().applyEvent(session({ state: 'barrier', sequence: 4 }))
    await vi.waitFor(() => expect(store().conflicts[`native-${ID}`]).toEqual(['Fig1']))
  })

  it('build 失败不改变会话状态（那归 SSE 管），只记一条原因', async () => {
    mockBuild.mockRejectedValue(apiError('native_session_disconnected'))
    store().applyEvent(session({ state: 'barrier', sequence: 4 }))
    await vi.waitFor(() => expect(store().errors[`native-${ID}`]?.code).toBe(
      'native_session_disconnected',
    ))
    expect(store().sessions[`native-${ID}`].state).toBe('barrier')
  })
})

describe('动作互斥与代际', () => {
  it.each(['p_a', 'p_b', null, undefined])('approval uses only the response project authority: %s', async (pj) => {
    useNativeSessionStore.setState({ pendingQueue: [{ native_id: ID, info: pending(), loading: false, submitting: false, error: null }] })
    mockApprove.mockResolvedValueOnce({ session: session({ state: 'running_script', sequence: 5, figure_snapshot_invalidation: executionFact() }), pj })
    await store().approve(ID, false)
    expect(acceptedExecution).toHaveBeenCalledTimes(pj === 'p_a' ? 1 : 0)
    expect(store().pendingQueue).toEqual([])
  })

  it.each(['project-round-trip', 'newer-sequence'] as const)('late approval cannot retire references after %s', async (change) => {
    useNativeSessionStore.setState({ pendingQueue: [{ native_id: ID, info: pending(), loading: false, submitting: false, error: null }] })
    let resolve!: (value: Awaited<ReturnType<typeof approveNativePending>>) => void
    mockApprove.mockImplementationOnce(() => new Promise(done => { resolve = done }))
    const approval = store().approve(ID, false)
    if (change === 'project-round-trip') { setCurrentProjectId('p_b'); setCurrentProjectId('p_a') }
    else store().applyEvent(session({ state: 'running_script', sequence: 6, figure_snapshot_invalidation: executionFact({ execution_id: 'newer-token' }) }))
    acceptedExecution.mockClear()
    resolve({ session: session({ state: 'running_script', sequence: 5, figure_snapshot_invalidation: executionFact() }), pj: 'p_a' })
    await approval
    expect(acceptedExecution).not.toHaveBeenCalled()
    if (change === 'newer-sequence') expect(store().sessions[`native-${ID}`].sequence).toBe(6)
  })

  describe.each([
    ['resume', mockContinue], ['detach', mockDetach], ['terminate', mockTerminate],
  ] as const)('%s execution evidence', (action, call) => {
    it.each(['success', 'error'] as const)('accepts a trusted new token from the %s response without SSE', async (outcome) => {
      seed({ state: 'barrier', sequence: 4, figure_snapshot_invalidation: executionFact({ execution_id: 'request-token' }) })
      const fact = executionFact({ receipt_ids: ['old-receipt'] })
      if (outcome === 'success') call.mockResolvedValueOnce({ session: session({ state: 'ended', sequence: 5, figure_snapshot_invalidation: fact }) })
      else call.mockRejectedValueOnce(new ApiError('failed after start', 500, { figure_snapshot_invalidation: fact }))
      await store()[action](`native-${ID}`)
      expect(acceptedExecution).toHaveBeenCalledExactlyOnceWith(fact, 'p_a')
    })

    it.each(['cancelled', 'failed', 'pre-start-error'] as const)('does not infer execution from %s without a fact', async (outcome) => {
      seed({ state: 'barrier', sequence: 4 })
      if (outcome === 'pre-start-error') call.mockRejectedValueOnce(apiError('native_session_disconnected'))
      else call.mockResolvedValueOnce({ session: session({ state: outcome === 'failed' ? 'failed' : 'ended', sequence: 5, cancelled: outcome === 'cancelled' }) })
      await store()[action](`native-${ID}`)
      expect(acceptedExecution).not.toHaveBeenCalled()
    })

    it('does not replay a passive baseline fact', async () => {
      const baseline = session({ state: 'running_script', sequence: 4, figure_snapshot_invalidation: executionFact() })
      mockList.mockResolvedValueOnce({ sessions: [baseline] })
      await store().refresh('/p/figures')
      call.mockResolvedValueOnce({ session: { ...baseline, state: 'ended', sequence: 5 } })
      await store()[action](`native-${ID}`)
      expect(acceptedExecution).not.toHaveBeenCalled()
    })

    it.each(['success', 'error'] as const)('rejects late %s evidence after another current token has been observed', async (outcome) => {
      seed({ state: 'barrier', sequence: 4, figure_snapshot_invalidation: executionFact({ execution_id: 'request-token' }) })
      let resolve!: (value: { session: NativeSessionInfo }) => void
      let reject!: (error: unknown) => void
      call.mockImplementationOnce(() => new Promise((yes, no) => { resolve = yes; reject = no }))
      const pending = store()[action](`native-${ID}`)
      store().applyEvent(session({ state: 'running_script', sequence: 6, figure_snapshot_invalidation: executionFact({ execution_id: 'current-token' }) }))
      acceptedExecution.mockClear()
      const late = executionFact({ receipt_ids: ['late-old-receipt'] })
      if (outcome === 'success') resolve({ session: session({ state: 'ended', sequence: 5, figure_snapshot_invalidation: late }) })
      else reject(new ApiError('late failure', 500, { figure_snapshot_invalidation: late }))
      await pending
      expect(acceptedExecution).not.toHaveBeenCalled()
    })

    it('accepts a newer HTTP sequence after an intermediate SSE token', async () => {
      seed({ state: 'barrier', sequence: 4, figure_snapshot_invalidation: executionFact({ execution_id: 'request-token' }) })
      let resolve!: (value: { session: NativeSessionInfo }) => void
      call.mockImplementationOnce(() => new Promise(done => { resolve = done }))
      const pending = store()[action](`native-${ID}`)
      store().applyEvent(session({ state: 'running_script', sequence: 5, figure_snapshot_invalidation: executionFact({ execution_id: 'intermediate-token' }) }))
      acceptedExecution.mockClear()
      const fact = executionFact({ execution_id: 'newest-token' })
      resolve({ session: session({ state: 'ended', sequence: 6, figure_snapshot_invalidation: fact }) })
      await pending
      expect(acceptedExecution).toHaveBeenCalledExactlyOnceWith(fact, 'p_a')
    })

    it.each(['equal-sequence-token-mismatch', 'dismissed'] as const)('rejects a response after %s', async (change) => {
      seed({ state: 'barrier', sequence: 4 })
      let resolve!: (value: { session: NativeSessionInfo }) => void
      call.mockImplementationOnce(() => new Promise(done => { resolve = done }))
      const pending = store()[action](`native-${ID}`)
      store().applyEvent(session({ state: 'ended', sequence: 5, figure_snapshot_invalidation: executionFact({ execution_id: 'current-token' }) }))
      if (change === 'dismissed') store().dismiss(`native-${ID}`)
      acceptedExecution.mockClear()
      resolve({ session: session({ state: 'ended', sequence: 5, figure_snapshot_invalidation: executionFact() }) })
      await pending
      expect(acceptedExecution).not.toHaveBeenCalled()
      if (change === 'dismissed') expect(store().sessions[`native-${ID}`]).toBeUndefined()
    })

    it.each(['success', 'error'] as const)('rejects %s facts after A → B → A even without a store reset', async (outcome) => {
      seed({ state: 'barrier', sequence: 4 })
      let resolve!: (value: { session: NativeSessionInfo }) => void
      let reject!: (error: unknown) => void
      call.mockImplementationOnce(() => new Promise((yes, no) => { resolve = yes; reject = no }))
      const pending = store()[action](`native-${ID}`)
      setCurrentProjectId('p_b'); setCurrentProjectId('p_a')
      if (outcome === 'success') resolve({ session: session({ state: 'ended', sequence: 5, figure_snapshot_invalidation: executionFact() }) })
      else reject(new ApiError('late failure', 500, { figure_snapshot_invalidation: executionFact() }))
      await pending
      expect(acceptedExecution).not.toHaveBeenCalled()
    })
  })

  it('连点两次「继续运行脚本」只发一条请求', async () => {
    seed({ state: 'barrier', sequence: 4 })
    let release!: () => void
    mockContinue.mockImplementation(
      () =>
        new Promise((r) => {
          release = () => r({ session: session({ state: 'running_script', sequence: 5 }) })
        }),
    )
    const first = store().resume(`native-${ID}`)
    await store().resume(`native-${ID}`)
    expect(mockContinue).toHaveBeenCalledTimes(1)
    release()
    await first
  })

  it('终态上的动作是 no-op（卡片上本来也不渲染按钮，这里是第二道）', async () => {
    seed({ state: 'ended', sequence: 9, exit_code: 0 })
    await store().terminate(`native-${ID}`)
    expect(mockTerminate).not.toHaveBeenCalled()
  })

  it('切项目之后，在途响应落不进新项目', async () => {
    seed({ state: 'barrier', sequence: 4 })
    let release!: () => void
    mockContinue.mockImplementation(
      () =>
        new Promise((r) => {
          release = () => r({ session: session({ state: 'running_script', sequence: 5 }) })
        }),
    )
    const inflight = store().resume(`native-${ID}`)
    store().clear() // 换项目
    release()
    await inflight
    expect(store().sessions).toEqual({})
    expect(store().busy).toEqual({})
  })

  it('clear() **不杀用户的脚本**——它只丢掉这边的状态', () => {
    seed({ state: 'barrier', sequence: 4 })
    store().clear()
    expect(mockTerminate).not.toHaveBeenCalled()
  })
})

describe('对账与收卡片', () => {
  it('filtered reconciliation upgrades unknown authority without replaying its initial execution baseline', async () => {
    const initial = session({ state: 'running_script', sequence: 4, figure_snapshot_invalidation: executionFact() })
    mockList.mockResolvedValueOnce({ sessions: [initial] })
    await store().refresh()
    mockList.mockResolvedValueOnce({ sessions: [{ ...initial, sequence: 5 }] })
    await store().refresh('/p/figures')
    expect(acceptedExecution).not.toHaveBeenCalled()
    const next = executionFact({ execution_id: 'new-after-filter' })
    mockContinue.mockResolvedValueOnce({ session: { ...initial, sequence: 6, figure_snapshot_invalidation: next } })
    await store().resume(initial.session_id)
    expect(acceptedExecution).toHaveBeenCalledExactlyOnceWith(next, 'p_a')
  })

  it('an unfiltered first listing cannot authorize later actions; a filtered current-project listing can', async () => {
    const unknown = session({ state: 'running_script', sequence: 4 })
    mockList.mockResolvedValueOnce({ sessions: [unknown] })
    await store().refresh()
    mockContinue.mockResolvedValueOnce({ session: { ...unknown, sequence: 5, figure_snapshot_invalidation: executionFact() } })
    await store().resume(unknown.session_id)
    expect(acceptedExecution).not.toHaveBeenCalled()
    const current = { ...unknown, sequence: 6, figure_snapshot_invalidation: executionFact({ execution_id: 'filtered-new-token' }) }
    mockList.mockResolvedValueOnce({ sessions: [current] })
    await store().refresh('/p/figures')
    expect(acceptedExecution).toHaveBeenCalledExactlyOnceWith(current.figure_snapshot_invalidation, 'p_a')
  })

  it('a filtered listing cannot prove a returned session with a different project root', async () => {
    mockList.mockResolvedValueOnce({ sessions: [session({ project_root: '/foreign', state: 'running_script', sequence: 4 })] })
    await store().refresh('/p/figures')
    mockContinue.mockResolvedValueOnce({ session: session({ project_root: '/foreign', state: 'ended', sequence: 5, figure_snapshot_invalidation: executionFact() }) })
    await store().resume(`native-${ID}`)
    expect(acceptedExecution).not.toHaveBeenCalled()
  })

  it('重连之后的清单不覆盖 SSE 已经送到的更新状态', async () => {
    seed({ state: 'ended', sequence: 9, exit_code: 0 })
    mockList.mockResolvedValue({ sessions: [session({ state: 'barrier', sequence: 4 })] })
    await store().refresh('/p/figures')
    expect(store().sessions[`native-${ID}`].state).toBe('ended')
  })

  it('清单取不到时不清空已知会话', async () => {
    seed({ state: 'barrier', sequence: 4 })
    mockList.mockRejectedValue(new Error('断了'))
    await store().refresh('/p/figures')
    expect(store().sessions[`native-${ID}`].state).toBe('barrier')
  })

  it('只收得起终态的卡片：活着的会话收起来就再也找不回来', () => {
    seed({ state: 'barrier', sequence: 4 })
    store().dismiss(`native-${ID}`)
    expect(store().sessions[`native-${ID}`]).toBeTruthy()
    seed({ state: 'ended', sequence: 5, exit_code: 0 })
    store().dismiss(`native-${ID}`)
    expect(store().sessions[`native-${ID}`]).toBeUndefined()
  })

  it('排序：还活着的排在终态前面', () => {
    const live = session({ session_id: 'a', state: 'barrier', started_at: 1 })
    const done = session({ session_id: 'b', state: 'ended', started_at: 9 })
    expect(sortSessions([done, live]).map((s) => s.session_id)).toEqual(['a', 'b'])
  })
})

describe('面板与会话的关系（角标判据）', () => {
  const bound = (over: Partial<NativeSessionInfo> = {}) => ({
    [`native-${ID}`]: session({ descriptors: [desc('Fig1')], ...over }),
  })

  it('停在屏障上：不打扰——此刻编辑一切正常', () => {
    const s = bound({ state: 'barrier', editable: true })
    expect(nativePanelState(s, 'runtime:fig.py#Fig1', 'native')).toBeNull()
  })

  it('脚本正在跑：先说一句，别让用户点进去撞 409', () => {
    const s = bound({ state: 'running_script', editable: false })
    expect(nativePanelState(s, 'runtime:fig.py#Fig1', 'native')).toBe('running')
  })

  it('会话结束了：cache 里那张是 last-known preview，说清楚', () => {
    const s = bound({ state: 'ended', editable: false })
    expect(nativePanelState(s, 'runtime:fig.py#Fig1', 'native')).toBe('offline')
  })

  it('停在屏障上、但引擎说图与文档不一致：inconsistent（Codex #549 第八轮）', () => {
    const s = bound({ state: 'barrier', editable: true })
    expect(nativePanelState(s, 'runtime:fig.py#Fig1', 'native', true)).toBe('inconsistent')
    // 干净了（下一次渲染报 0）就解除——判据只看这一位，不留尾巴
    expect(nativePanelState(s, 'runtime:fig.py#Fig1', 'native', false)).toBeNull()
  })

  it('不一致但脚本正在跑 / 会话已结束：说此刻真正挡住编辑的那一句', () => {
    const running = bound({ state: 'running_script', editable: false })
    expect(nativePanelState(running, 'runtime:fig.py#Fig1', 'native', true)).toBe('running')
    const ended = bound({ state: 'ended', editable: false })
    expect(nativePanelState(ended, 'runtime:fig.py#Fig1', 'native', true)).toBe('offline')
  })

  it('safe 面板上的不一致标记不挂 native 角标', () => {
    const s = bound({ state: 'barrier', editable: true })
    expect(nativePanelState(s, 'Fig1.pdf', 'safe', true)).toBeNull()
  })

  it('桌面会话：屏障事件先到（还没 build、descriptors 为空），build 后同状态更大序号的快照把角标认回来', () => {
    const id = `native-${ID}`
    store().applyEvent(session({ owner: 'desktop', state: 'barrier', editable: true, descriptors: [], sequence: 4 }))
    // 空 descriptors 时面板认领不到会话：一张活着的图被说成「会话已结束」（修复前的现象）
    expect(nativePanelState(store().sessions, 'runtime:fig.py#Fig1', 'native')).toBe('offline')
    store().applyEvent(
      session({ owner: 'desktop', state: 'barrier', editable: true, descriptors: [desc('Fig1')], sequence: 5 }),
    )
    expect(store().sessions[id]?.descriptors).toHaveLength(1)
    expect(nativePanelState(store().sessions, 'runtime:fig.py#Fig1', 'native')).toBeNull()
  })

  it('一条会话都没有、但这张图出自 native：同样是 offline', () => {
    expect(nativePanelState({}, 'runtime:fig.py#Fig1', 'native')).toBe('offline')
  })

  it('**按 asset id 认领，不按 stem 猜**：同名 stem 在两个项目里到处都是', () => {
    const s = bound({ state: 'running_script', editable: false })
    // 同一个 stem、不同项目 → 不同的 asset id：这张图不归那条会话管
    expect(nativePanelState(s, 'runtime:other/fig.py#Fig1', 'native')).toBe('offline')
  })

  it('safe 面板不受任何影响——哪怕正好有一条 native 会话在跑', () => {
    const s = bound({ state: 'running_script', editable: false })
    expect(nativePanelState(s, 'Fig1.pdf', 'safe')).toBeNull()
  })

  it('**未知不等于 native**：老后端不给 profile 时不挂「会话已结束」', () => {
    expect(nativePanelState({}, 'runtime:fig.py#Fig1', undefined)).toBeNull()
  })

  it('editable 由后端说了算，不按 state 名字自己推', () => {
    // 后端说停在屏障上、但 editable 是 false（正在处理上一条请求）——
    // 以 editable 为准，那是对端报上来的事实
    const s = bound({ state: 'barrier', editable: false })
    expect(nativePanelState(s, 'runtime:fig.py#Fig1', 'native')).toBe('running')
  })
})
