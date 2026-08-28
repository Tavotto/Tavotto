import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  ApiError,
  approveNativePending,
  buildNativeSession,
  cancelNativePending,
  continueNativeSession,
  fetchNativePending,
  fetchNativeSessions,
  fetchPanels,
  fetchRuntimeAssets,
  terminateNativeSession,
  type CapturedFigureDescriptor,
  type NativePending,
  type NativeSessionInfo,
  type NativeSessionState,
} from '@/lib/api'
import { addRuntimePanel } from '@/store/actions'
import { useDocumentStore } from '@/store/documentStore'
import { useFigurePickerStore } from '@/store/figurePickerStore'
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

vi.mock('@/store/actions', () => ({ addRuntimePanel: vi.fn() }))

const mockPending = vi.mocked(fetchNativePending)
const mockApprove = vi.mocked(approveNativePending)
const mockCancel = vi.mocked(cancelNativePending)
const mockBuild = vi.mocked(buildNativeSession)
const mockContinue = vi.mocked(continueNativeSession)
const mockTerminate = vi.mocked(terminateNativeSession)
const mockList = vi.mocked(fetchNativeSessions)
const mockAddPanel = vi.mocked(addRuntimePanel)

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
  useNativeSessionStore.setState({
    epoch: 0,
    pendingQueue: [],
    sessions: {},
    busy: {},
    errors: {},
    conflicts: {},
    builtSeq: {},
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

describe('屏障 → build → 图进画布', () => {
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
