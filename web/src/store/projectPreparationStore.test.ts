/**
 * 准备会话的前端持有者（T09，ADR 0116）：只保存报告投影、参数快照、订阅与网络状态。
 *
 * 看护的几条界线：
 *   * 参数草稿在**打开那一刻**取拷贝（之后改草稿不动这份会话；改了才提示按新参数检查）；
 *   * 同一会话里修订 / 观察序号只许前进，旧会话 id 的迟到回包不把重建过的会话换回去；
 *   * A → B → A：换代之后的迟到回包不落地；
 *   * 动作只交后端生成的 id + 看到的修订（依赖授权回显影响摘要）；被拒（409）只说一句、重读报告，**不重发、不运行**；
 *   * 应用重启（旧会话 404 `unknown_or_restarted`）：只重建**检查**会话，不认领 run；
 *   * 网络看门狗只改连接事实：取报告超时 → `connection: lost`，phase 原样，补拉成功即恢复，绝不标失败、不重提交；
 *   * 关面板不取消；切项目不取消（零请求）；环境 / 数据有了答案只 `recheck`，不运行。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  createPreparationSession: vi.fn(),
  fetchPreparationSession: vi.fn(),
  actOnPreparationSession: vi.fn(),
  fetchEngineEnvironment: vi.fn().mockResolvedValue({}),
}))

import {
  actOnPreparationSession,
  ApiError,
  createPreparationSession,
  fetchPreparationSession,
  type PreparationAction,
  type PreparationReport,
} from '@/lib/api'
import { setCurrentProjectId } from '@/lib/session'
import {
  __setPreparationTimingForTests,
  draftDiffers,
  scriptTarget,
  useProjectPreparationStore,
} from './projectPreparationStore'
import { useEnvStore } from './envStore'
import { useScriptArgvStore } from './scriptArgvStore'
import { useUiStore } from './uiStore'

const mockCreate = vi.mocked(createPreparationSession)
const mockGet = vi.mocked(fetchPreparationSession)
const mockAct = vi.mocked(actOnPreparationSession)

const action = (kind: PreparationAction['kind'], over: Partial<PreparationAction> = {}): PreparationAction => ({
  id: `act-${kind}`,
  kind,
  config_revision: 1,
  impact: { executes_user_script: kind === 'run', installs_packages: false, changes_environment: false, writes_to_project: [] },
  ...over,
})

export const prepReport = (over: Partial<PreparationReport> = {}): PreparationReport => ({
  session_version: 1,
  session_id: 'psess-1',
  project_id: 'pj-a',
  target: { kind: 'script', script: 'plot.py', entry: '__main__', asset_id: null, stem: null },
  config_revision: 1,
  observation_seq: 1,
  phase: 'ready_to_run',
  outcome: { kind: 'pending' },
  facts: { execution_finished: null, figure_captured: null },
  checks: [{ id: 'target', status: 'ok' }],
  requirements: [],
  actions: [action('run'), action('recheck')],
  provider: { plan_id: 'prep-plan', attempt_id: null, attempts: 0, dependency: null },
  runtime_input: null,
  captured: [],
  result: null,
  ...over,
})

const flush = async () => {
  for (let i = 0; i < 5; i++) await Promise.resolve()
  await new Promise((r) => setTimeout(r, 0))
}

const KEY = 'script:plot.py'
const entry = () => useProjectPreparationStore.getState().entries[KEY]

beforeEach(() => {
  vi.useRealTimers()
  mockCreate.mockReset()
  mockGet.mockReset()
  mockAct.mockReset()
  __setPreparationTimingForTests({ requestTimeoutMs: 15_000, pollMs: [10_000] })
  useProjectPreparationStore.getState().clear()
  useScriptArgvStore.getState().clear()
  useUiStore.setState({ preparationOpen: false })
  setCurrentProjectId('pj-a')
})

afterEach(() => {
  useProjectPreparationStore.getState().clear()
  setCurrentProjectId(null)
})

describe('打开：参数在这一刻冻结', () => {
  it('草稿里的 token 原样带上（含空串与 --），发请求那一刻的项目随请求走；面板打开', async () => {
    useScriptArgvStore.getState().setTokens('plot.py', ['--tag', '', '--', '-1'])
    mockCreate.mockResolvedValueOnce(prepReport())
    await useProjectPreparationStore.getState().open(scriptTarget('plot.py'))
    expect(mockCreate).toHaveBeenCalledTimes(1)
    const [target, pj] = mockCreate.mock.calls[0]
    expect(target).toEqual({ script: 'plot.py', argv: ['--tag', '', '--', '-1'], argv_sensitive: false })
    expect(pj).toBe('pj-a')
    expect(entry().report?.session_id).toBe('psess-1')
    expect(useUiStore.getState().preparationOpen).toBe(true)
    expect(mockAct).not.toHaveBeenCalled() // 打开只检查，不运行
  })

  it('空草稿 = 不带参数（目标里没有 argv）', () => {
    expect(scriptTarget('plot.py')).toEqual({ script: 'plot.py' })
  })

  it('打开之后再改草稿：会话的目标不变；与草稿不同了才提示按新参数检查', async () => {
    useScriptArgvStore.getState().setTokens('plot.py', ['--n', '1'])
    mockCreate.mockResolvedValueOnce(prepReport())
    await useProjectPreparationStore.getState().open(scriptTarget('plot.py'))
    expect(draftDiffers(entry().target)).toBe(false)
    useScriptArgvStore.getState().setToken('plot.py', 1, '2')
    expect((entry().target as { argv?: string[] }).argv).toEqual(['--n', '1'])
    expect(draftDiffers(entry().target)).toBe(true)
  })
})

describe('同一脚本换参数再打开', () => {
  it('旧参数的报告不留：新会话建不出来（超时）时重试的是新目标，不去补拉旧会话', async () => {
    useScriptArgvStore.getState().setTokens('plot.py', ['--n', '1'])
    mockCreate.mockResolvedValueOnce(prepReport({ session_id: 'psess-old' }))
    await useProjectPreparationStore.getState().open(scriptTarget('plot.py'))
    expect(entry().report?.session_id).toBe('psess-old')

    useScriptArgvStore.getState().setTokens('plot.py', ['--n', '2'])
    mockCreate.mockRejectedValueOnce(new DOMException('timeout', 'AbortError'))
    await useProjectPreparationStore.getState().open(scriptTarget('plot.py'))
    expect(entry().report).toBeNull() // 旧参数的报告 / 动作不再显示
    expect(entry().connection).toBe('lost')
    expect(draftDiffers(entry().target)).toBe(false)

    mockCreate.mockResolvedValueOnce(prepReport({ session_id: 'psess-new' }))
    await useProjectPreparationStore.getState().refresh(KEY)
    expect(mockGet).not.toHaveBeenCalled() // 没有补拉旧会话
    expect(mockCreate).toHaveBeenCalledTimes(3)
    expect(mockCreate.mock.calls[2][0]).toEqual({ script: 'plot.py', argv: ['--n', '2'], argv_sensitive: false })
    expect(entry().report?.session_id).toBe('psess-new')
  })

  it('换参数之前发出的补拉，回包不能把旧会话的报告落回新目标上', async () => {
    useScriptArgvStore.getState().setTokens('plot.py', ['--n', '1'])
    mockCreate.mockResolvedValueOnce(prepReport({ session_id: 'psess-old' }))
    await useProjectPreparationStore.getState().open(scriptTarget('plot.py'))
    let late!: (r: PreparationReport) => void
    mockGet.mockReturnValueOnce(new Promise((res) => (late = res)))
    const pending = useProjectPreparationStore.getState().refresh(KEY)

    useScriptArgvStore.getState().setTokens('plot.py', ['--n', '2'])
    mockCreate.mockRejectedValueOnce(new DOMException('timeout', 'AbortError'))
    await useProjectPreparationStore.getState().open(scriptTarget('plot.py'))
    late(prepReport({ session_id: 'psess-old', observation_seq: 9 }))
    await pending
    await flush()
    expect(entry().report).toBeNull()
  })

  it('同参数再打开仍保留上一份报告（不闪空）', async () => {
    useScriptArgvStore.getState().setTokens('plot.py', ['--n', '1'])
    mockCreate.mockResolvedValueOnce(prepReport())
    await useProjectPreparationStore.getState().open(scriptTarget('plot.py'))
    mockCreate.mockRejectedValueOnce(new DOMException('timeout', 'AbortError'))
    await useProjectPreparationStore.getState().open(scriptTarget('plot.py'))
    expect(entry().report?.session_id).toBe('psess-1')
  })
})

describe('换参数再打开：每目标的状态一并清空', () => {
  it('「进入编辑」的记录不带进新参数的结果（否则新结果一出来就被当成已在编辑）', async () => {
    useScriptArgvStore.getState().setTokens('plot.py', ['--n', '1'])
    mockCreate.mockResolvedValueOnce(prepReport({ session_id: 'psess-old' }))
    await useProjectPreparationStore.getState().open(scriptTarget('plot.py'))
    useProjectPreparationStore.getState().noteEditing(KEY, 'runtime:plot.py#a')
    expect(entry().editing).toEqual(['runtime:plot.py#a'])

    useScriptArgvStore.getState().setTokens('plot.py', ['--n', '2'])
    mockCreate.mockResolvedValueOnce(prepReport({ session_id: 'psess-new' }))
    await useProjectPreparationStore.getState().open(scriptTarget('plot.py'))
    expect(entry().editing).toEqual([])
  })

  it('同参数再打开仍保留编辑记录', async () => {
    mockCreate.mockResolvedValueOnce(prepReport())
    await useProjectPreparationStore.getState().open(scriptTarget('plot.py'))
    useProjectPreparationStore.getState().noteEditing(KEY, 'runtime:plot.py#a')
    mockCreate.mockResolvedValueOnce(prepReport())
    await useProjectPreparationStore.getState().open(scriptTarget('plot.py'))
    expect(entry().editing).toEqual(['runtime:plot.py#a'])
  })
})

describe('完成回调的项目归属（动态 import 之后复核）', () => {
  const done = (seq: number) =>
    prepReport({
      phase: 'completed',
      outcome: { kind: 'succeeded' },
      observation_seq: seq,
      captured: [{ asset_id: 'runtime:plot.py#a' } as never],
    })

  it('A 刚完成就切到 B：A 的素材 id 不去动 B 的运行态 / 渲染态 store', async () => {
    const { useRuntimeAssetStore } = await import('./runtimeAssetStore')
    const { useRenderStore } = await import('./renderStore')
    const invalidate = vi.fn()
    const markStale = vi.fn()
    const realInv = useRuntimeAssetStore.getState().invalidate
    const realStale = useRenderStore.getState().markStale
    useRuntimeAssetStore.setState({ invalidate })
    useRenderStore.setState({ markStale })
    try {
      mockCreate.mockResolvedValueOnce(prepReport())
      await useProjectPreparationStore.getState().open(scriptTarget('plot.py'))
      mockGet.mockResolvedValueOnce(done(2))
      const p = useProjectPreparationStore.getState().refresh(KEY)
      await p // accept 已触发 onCompleted，动态 import 还没回来
      setCurrentProjectId('pj-b')
      useProjectPreparationStore.getState().clear()
      await flush()
      expect(invalidate).not.toHaveBeenCalled()
      expect(markStale).not.toHaveBeenCalled()
    } finally {
      useRuntimeAssetStore.setState({ invalidate: realInv })
      useRenderStore.setState({ markStale: realStale })
    }
  })

  it('依赖装完的重排同理：切项目之后不重排 B 的渲染', async () => {
    const { useRenderStore } = await import('./renderStore')
    const retry = vi.fn()
    const real = useRenderStore.getState().retryEnvironmentFailures
    useRenderStore.setState({ retryEnvironmentFailures: retry })
    const withDep = (state: string, seq: number) =>
      prepReport({
        observation_seq: seq,
        provider: {
          plan_id: 'prep-plan',
          attempt_id: null,
          attempts: 0,
          dependency: { plan_id: 'dp-1', joined: false, origin: 'joint', state, code: '', committed: state === 'done', impact_digest: 'd' } as never,
        },
      })
    try {
      mockCreate.mockResolvedValueOnce(withDep('running', 1))
      await useProjectPreparationStore.getState().open(scriptTarget('plot.py'))
      mockGet.mockResolvedValueOnce(withDep('done', 2))
      await useProjectPreparationStore.getState().refresh(KEY)
      setCurrentProjectId('pj-b')
      useProjectPreparationStore.getState().clear()
      await flush()
      expect(retry).not.toHaveBeenCalled()
    } finally {
      useRenderStore.setState({ retryEnvironmentFailures: real })
    }
  })
})

describe('迟到响应', () => {
  it('同一修订里观察序号不倒退；修订前进的那份即使序号小也赢', async () => {
    mockCreate.mockResolvedValueOnce(prepReport({ observation_seq: 5 }))
    await useProjectPreparationStore.getState().open(scriptTarget('plot.py'))
    mockGet.mockResolvedValueOnce(prepReport({ observation_seq: 3, phase: 'running' }))
    await useProjectPreparationStore.getState().refresh(KEY)
    expect(entry().report?.observation_seq).toBe(5)
    expect(entry().report?.phase).toBe('ready_to_run')
    mockGet.mockResolvedValueOnce(prepReport({ config_revision: 2, observation_seq: 1, phase: 'action_required' }))
    await useProjectPreparationStore.getState().refresh(KEY)
    expect(entry().report?.config_revision).toBe(2)
  })

  it('会话重建之后，旧会话的迟到 GET 不把它换回去', async () => {
    mockCreate.mockResolvedValueOnce(prepReport({ session_id: 'psess-old' }))
    await useProjectPreparationStore.getState().open(scriptTarget('plot.py'))
    let release!: (r: PreparationReport) => void
    mockGet.mockReturnValueOnce(new Promise((res) => (release = res)))
    const late = useProjectPreparationStore.getState().refresh(KEY)
    mockCreate.mockResolvedValueOnce(prepReport({ session_id: 'psess-new' }))
    await useProjectPreparationStore.getState().open(scriptTarget('plot.py'))
    release(prepReport({ session_id: 'psess-old', observation_seq: 99, phase: 'running' }))
    await late
    expect(entry().report?.session_id).toBe('psess-new')
  })

  it('A → B → A：A 第一次打开时发出的检查晚到，不落进回到 A 之后的状态', async () => {
    let release!: (r: PreparationReport) => void
    mockCreate.mockReturnValueOnce(new Promise((res) => (release = res)))
    const old = useProjectPreparationStore.getState().open(scriptTarget('plot.py'))
    useProjectPreparationStore.getState().clear()
    setCurrentProjectId('pj-b')
    useProjectPreparationStore.getState().clear()
    setCurrentProjectId('pj-a')
    release(prepReport({ session_id: 'psess-stale' }))
    await old
    expect(useProjectPreparationStore.getState().entries).toEqual({})
  })
})

describe('动作只交后端生成的 id', () => {
  it('依赖授权回显影响摘要；回包的报告直接落地', async () => {
    const prepare = action('prepare_dependencies', {
      impact: {
        executes_user_script: false,
        installs_packages: true,
        changes_environment: true,
        writes_to_project: [],
        impact_digest: 'imp-123',
      },
    })
    mockCreate.mockResolvedValueOnce(prepReport({ phase: 'awaiting_confirmation', actions: [prepare] }))
    await useProjectPreparationStore.getState().open(scriptTarget('plot.py'))
    mockAct.mockResolvedValueOnce({ claimed: true, report: prepReport({ phase: 'preparing_environment', observation_seq: 2 }) })
    await useProjectPreparationStore.getState().act(KEY, 'prepare_dependencies')
    expect(mockAct).toHaveBeenCalledWith(
      'psess-1',
      { action_id: 'act-prepare_dependencies', expected_config_revision: 1, impact_digest: 'imp-123' },
      'pj-a',
      expect.any(AbortSignal),
    )
    expect(entry().report?.phase).toBe('preparing_environment')
  })

  it('报告里没有这个动作：什么都不发', async () => {
    mockCreate.mockResolvedValueOnce(prepReport({ actions: [action('recheck')] }))
    await useProjectPreparationStore.getState().open(scriptTarget('plot.py'))
    await useProjectPreparationStore.getState().act(KEY, 'run')
    expect(mockAct).not.toHaveBeenCalled()
  })

  it('409（修订变了）：说一句、重读报告，不重发也不运行', async () => {
    mockCreate.mockResolvedValueOnce(prepReport())
    await useProjectPreparationStore.getState().open(scriptTarget('plot.py'))
    mockAct.mockRejectedValueOnce(
      new ApiError('changed', 409, { code: 'preparation_config_revision_changed', params: { config_revision: 2 } }),
    )
    mockGet.mockResolvedValueOnce(prepReport({ config_revision: 2, observation_seq: 1 }))
    await useProjectPreparationStore.getState().act(KEY, 'run')
    expect(mockAct).toHaveBeenCalledTimes(1)
    expect(mockGet).toHaveBeenCalledTimes(1)
    expect(entry().rejection?.code).toBe('preparation_config_revision_changed')
    expect(entry().report?.config_revision).toBe(2)
  })
})

describe('应用重启：重建检查会话，不重跑', () => {
  it('GET 404 unknown_or_restarted → 再 POST 创建（只读检查），绝不认领 run', async () => {
    mockCreate.mockResolvedValueOnce(prepReport({ phase: 'running', actions: [action('cancel')] }))
    await useProjectPreparationStore.getState().open(scriptTarget('plot.py'))
    mockGet.mockRejectedValueOnce(
      new ApiError('gone', 404, {
        code: 'preparation_session_not_found',
        params: { reason: 'unknown_or_restarted' },
      }),
    )
    mockCreate.mockResolvedValueOnce(prepReport({ session_id: 'psess-after-restart' }))
    await useProjectPreparationStore.getState().refresh(KEY)
    expect(mockCreate).toHaveBeenCalledTimes(2)
    expect(mockCreate.mock.calls[1][0]).toEqual({ script: 'plot.py' })
    expect(entry().report?.session_id).toBe('psess-after-restart')
    expect(entry().restarted).toBe(true)
    expect(mockAct).not.toHaveBeenCalled()
  })
})

describe('事件流重连', () => {
  it('refreshAll：每个会话以 GET 补拉一次，不发动作', async () => {
    mockCreate.mockResolvedValueOnce(prepReport({ phase: 'completed', outcome: { kind: 'succeeded' } }))
    await useProjectPreparationStore.getState().open(scriptTarget('plot.py'))
    mockGet.mockResolvedValueOnce(prepReport({ phase: 'completed', outcome: { kind: 'succeeded' }, observation_seq: 2 }))
    useProjectPreparationStore.getState().refreshAll()
    await flush()
    expect(mockGet).toHaveBeenCalledTimes(1)
    expect(mockAct).not.toHaveBeenCalled()
  })
})

describe('网络看门狗只改连接事实', () => {
  it('取报告超时：connection=lost、phase 原样、不标失败、不重提交；补拉成功即恢复', async () => {
    __setPreparationTimingForTests({ requestTimeoutMs: 20, pollMs: [15] })
    mockCreate.mockResolvedValueOnce(prepReport({ phase: 'running', actions: [action('cancel')] }))
    await useProjectPreparationStore.getState().open(scriptTarget('plot.py'))
    // 第一次 GET 卡住（只认 abort）：后台照样在跑
    mockGet.mockImplementationOnce(
      (_id, _pj, signal) =>
        new Promise((_res, rej) => {
          signal?.addEventListener('abort', () => rej(new DOMException('aborted', 'AbortError')))
        }),
    )
    mockGet.mockResolvedValue(prepReport({ phase: 'completed', outcome: { kind: 'succeeded' }, observation_seq: 4 }))
    await useProjectPreparationStore.getState().refresh(KEY)
    expect(entry().connection).toBe('lost')
    expect(entry().report?.phase).toBe('running') // 失联不等于失败
    expect(mockAct).not.toHaveBeenCalled()
    await vi.waitFor(() => expect(entry().connection).toBe('ok'), { timeout: 1000 })
    expect(entry().report?.phase).toBe('completed')
    expect(mockCreate).toHaveBeenCalledTimes(1) // 没有因为失联重新提交任何东西
  })
})

describe('动作 POST 也走网络看门狗', () => {
  it('动作永不返回：超时后 pending 清除、补拉一次报告、不重发动作', async () => {
    __setPreparationTimingForTests({ requestTimeoutMs: 20, pollMs: [10_000] })
    mockCreate.mockResolvedValueOnce(prepReport({ phase: 'running', actions: [action('cancel')] }))
    await useProjectPreparationStore.getState().open(scriptTarget('plot.py'))
    mockAct.mockImplementationOnce(
      (_id, _body, _pj, signal) =>
        new Promise((_res, rej) => {
          signal?.addEventListener('abort', () => rej(new DOMException('aborted', 'AbortError')))
        }),
    )
    mockGet.mockClear()
    mockGet.mockResolvedValue(prepReport({ phase: 'running', actions: [action('cancel')], observation_seq: 3 }))
    vi.useFakeTimers()
    try {
      const done = useProjectPreparationStore.getState().act(KEY, 'cancel')
      expect(entry().pending).toBe('cancel')
      await vi.advanceTimersByTimeAsync(25)
      await done
    } finally {
      vi.useRealTimers()
    }
    expect(entry().pending).toBeNull()
    expect(mockAct).toHaveBeenCalledTimes(1) // 不盲目重发
    expect(mockGet).toHaveBeenCalledTimes(1) // 补拉一次报告
  })
})

describe('三个不同的动作', () => {
  it('关面板只改呈现：不发取消，订阅照旧', async () => {
    mockCreate.mockResolvedValueOnce(prepReport({ phase: 'running', actions: [action('cancel')] }))
    await useProjectPreparationStore.getState().open(scriptTarget('plot.py'))
    useUiStore.getState().setPreparationOpen(false)
    expect(mockAct).not.toHaveBeenCalled()
    expect(entry().report?.phase).toBe('running')
  })

  it('取消 = 认领后端的 cancel 动作（按 owner 退役本会话的工作）', async () => {
    mockCreate.mockResolvedValueOnce(prepReport({ phase: 'running', actions: [action('cancel')] }))
    await useProjectPreparationStore.getState().open(scriptTarget('plot.py'))
    mockAct.mockResolvedValueOnce({ claimed: true, report: prepReport({ phase: 'cancelled', observation_seq: 2 }) })
    await useProjectPreparationStore.getState().act(KEY, 'cancel')
    expect(mockAct.mock.calls[0][1].action_id).toBe('act-cancel')
  })

  it('切项目：零请求（不取消后端的任何东西），条目与订阅全丢', async () => {
    mockCreate.mockResolvedValueOnce(prepReport({ phase: 'running', actions: [action('cancel')] }))
    await useProjectPreparationStore.getState().open(scriptTarget('plot.py'))
    const calls = mockAct.mock.calls.length + mockGet.mock.calls.length
    useProjectPreparationStore.getState().clear()
    await flush()
    expect(mockAct.mock.calls.length + mockGet.mock.calls.length).toBe(calls)
    expect(useProjectPreparationStore.getState().entries).toEqual({})
  })

  it('环境 / 数据有了答案：空闲的会话只重新检查，不运行', async () => {
    mockCreate.mockResolvedValueOnce(prepReport({ phase: 'action_required', actions: [action('run'), action('recheck')] }))
    await useProjectPreparationStore.getState().open(scriptTarget('plot.py'))
    mockAct.mockResolvedValue({ claimed: true, report: prepReport({ observation_seq: 2 }) })
    useEnvStore.setState((s) => ({ inputRemapGeneration: s.inputRemapGeneration + 1 }))
    await flush()
    expect(mockAct).toHaveBeenCalledTimes(1)
    expect(mockAct.mock.calls[0][1].action_id).toBe('act-recheck')
  })
})

describe('依赖准备装完：画布上停在依赖门上的渲染重排（T09b）', () => {
  const dep = (state: string, plan_id = 'dp-1') => ({
    plan_id,
    joined: false,
    origin: 'joint' as const,
    state,
    code: '',
    committed: state === 'done',
    impact_digest: 'd',
  })
  const withDep = (state: string, seq: number, plan_id?: string) =>
    prepReport({
      phase: state === 'done' ? 'ready_to_run' : 'preparing_environment',
      observation_seq: seq,
      provider: { plan_id: 'prep-plan', attempt_id: null, attempts: 0, dependency: dep(state, plan_id) },
    })

  it('同一会话里的依赖作业从进行中变成 done：重排一次（与原授权框装完同一个出口），不认领 run', async () => {
    const { useRenderStore } = await import('./renderStore')
    const retry = vi.fn()
    const real = useRenderStore.getState().retryEnvironmentFailures
    useRenderStore.setState({ retryEnvironmentFailures: retry })
    try {
      mockCreate.mockResolvedValueOnce(withDep('running', 1))
      await useProjectPreparationStore.getState().open(scriptTarget('plot.py'))
      expect(retry).not.toHaveBeenCalled()
      mockGet.mockResolvedValueOnce(withDep('done', 2))
      await useProjectPreparationStore.getState().refresh(KEY)
      await flush()
      expect(retry).toHaveBeenCalledTimes(1)
      // 同一作业再读一次 done：不再重排
      mockGet.mockResolvedValueOnce(withDep('done', 3))
      await useProjectPreparationStore.getState().refresh(KEY)
      await flush()
      expect(retry).toHaveBeenCalledTimes(1)
      expect(mockAct).not.toHaveBeenCalled()
    } finally {
      useRenderStore.setState({ retryEnvironmentFailures: real })
    }
  })

  it('打开时报告里已经是 done（之前装好的）：不重排——只认这一份会话里看到的那次转变', async () => {
    const { useRenderStore } = await import('./renderStore')
    const retry = vi.fn()
    const real = useRenderStore.getState().retryEnvironmentFailures
    useRenderStore.setState({ retryEnvironmentFailures: retry })
    try {
      mockCreate.mockResolvedValueOnce(withDep('done', 1))
      await useProjectPreparationStore.getState().open(scriptTarget('plot.py'))
      await flush()
      expect(retry).not.toHaveBeenCalled()
    } finally {
      useRenderStore.setState({ retryEnvironmentFailures: real })
    }
  })
})

describe('同一目标的修订前进：上一轮的编辑记录作废', () => {
  const done = (over: Partial<PreparationReport> = {}) =>
    prepReport({
      phase: 'completed',
      outcome: { kind: 'succeeded' },
      captured: [{ asset_id: 'runtime:plot.py#a' } as never],
      provider: { plan_id: 'prep-plan', attempt_id: 'att-1', attempts: 1, dependency: null },
      ...over,
    })

  it('重开同一目标、后端已是更高修订的新一轮结果：editing 清空（入口按钮回来）', async () => {
    mockCreate.mockResolvedValueOnce(done())
    await useProjectPreparationStore.getState().open(scriptTarget('plot.py'))
    useProjectPreparationStore.getState().noteEditing(KEY, 'runtime:plot.py#a', { panelId: 'p1', renderKey: 'k1' })
    expect(entry().editing).toEqual(['runtime:plot.py#a'])
    mockCreate.mockResolvedValueOnce(
      done({
        config_revision: 2,
        provider: { plan_id: 'prep-plan', attempt_id: 'att-2', attempts: 2, dependency: null },
      }),
    )
    await useProjectPreparationStore.getState().open(scriptTarget('plot.py'))
    expect(entry().report?.config_revision).toBe(2)
    expect(entry().editing).toEqual([])
    expect(entry().editRenders).toEqual({})
  })

  it('补拉到更高修订（同一会话）同样清空；同一修订的补拉保留', async () => {
    mockCreate.mockResolvedValueOnce(done())
    await useProjectPreparationStore.getState().open(scriptTarget('plot.py'))
    useProjectPreparationStore.getState().noteEditing(KEY, 'runtime:plot.py#a')
    mockGet.mockResolvedValueOnce(done({ observation_seq: 2 }))
    await useProjectPreparationStore.getState().refresh(KEY)
    expect(entry().editing).toEqual(['runtime:plot.py#a'])
    mockGet.mockResolvedValueOnce(done({ config_revision: 2, observation_seq: 1 }))
    await useProjectPreparationStore.getState().refresh(KEY)
    expect(entry().editing).toEqual([])
  })

  it('同一会话里换了一轮尝试（attempt_id 变了）也清空', async () => {
    mockCreate.mockResolvedValueOnce(done())
    await useProjectPreparationStore.getState().open(scriptTarget('plot.py'))
    useProjectPreparationStore.getState().noteEditing(KEY, 'runtime:plot.py#a')
    mockGet.mockResolvedValueOnce(
      done({ observation_seq: 2, provider: { plan_id: 'prep-plan', attempt_id: 'att-2', attempts: 2, dependency: null } }),
    )
    await useProjectPreparationStore.getState().refresh(KEY)
    expect(entry().editing).toEqual([])
  })
})
