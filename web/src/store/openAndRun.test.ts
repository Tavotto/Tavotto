/**
 * 「打开并运行」黄金路径（V2 PR4，ADR 0124 / 0126 / 0127）：一次点击 → 检查 → 认领后端的 `run_when_ready` →
 * 运行（含后端会话内的 safe→native 静默回退）→ 自动进入编辑。看护的几条界线：
 *
 *   * 前端**永远不发 `execution`**：回退是后端同一会话里的第二个 attempt，前端只翻译；
 *   * 只有明确的「打开并运行」入口（`open(..., { run: true })`）会代用户认领；普通 `open` 只检查；
 *   * 必填参数没填齐 / 报告里没有 `run_when_ready`（老后端、杀开关）时不认领；
 *   * 认领带授权摘要回显；argv 换了且旧会话还有在等的意图时带 `transfer_from`；
 *   * 跑完恰好一张图才自动进入编辑；多张要用户挑、失败 / 取消 / 没图不进入；
 *   * 回退产生新的 attempt_id 不会让「已进入编辑」的记录冒充新结果，也不会多开面板（面板身份是资产 id）。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  createPreparationSession: vi.fn(),
  fetchPreparationSession: vi.fn(),
  actOnPreparationSession: vi.fn(),
  fetchEngineEnvironment: vi.fn().mockResolvedValue({}),
  fetchRuntimeAssets: vi.fn().mockResolvedValue({ assets: [] }),
  fetchPanels: vi.fn().mockResolvedValue({ figures_dir: '', panels: [] }),
}))
vi.mock('@/store/documentRestore', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/store/documentRestore')>()),
  takeDeferredEntryOutcome: vi.fn(() => null),
}))
vi.mock('@/store/workspace', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/store/workspace')>()),
  addRuntimePanelToCanvas: vi.fn(),
  openFastEdit: vi.fn(() => 'editing'),
}))

import {
  actOnPreparationSession,
  ApiError,
  createPreparationSession,
  fetchPreparationSession,
  type CapturedFigureDescriptor,
  type PreparationAction,
  type PreparationAttempt,
  type PreparationReport,
} from '@/lib/api'
import { setCurrentProjectId } from '@/lib/session'
import { useDocumentStore } from '@/store/documentStore'
import { takeDeferredEntryOutcome } from '@/store/documentRestore'
import {
  __setPreparationTimingForTests,
  draftDiffers,
  reusedArgCount,
  scriptTarget,
  useProjectPreparationStore,
} from './projectPreparationStore'
import { useRuntimeAssetStore } from './runtimeAssetStore'
import { useScriptArgvStore } from './scriptArgvStore'
import { useUiStore } from './uiStore'
import { addRuntimePanelToCanvas, openFastEdit } from '@/store/workspace'
import type { PanelObject } from '@/types/document'

const mockCreate = vi.mocked(createPreparationSession)
const mockGet = vi.mocked(fetchPreparationSession)
const mockAct = vi.mocked(actOnPreparationSession)

const DIGEST = 'grant-digest-1'

const action = (kind: PreparationAction['kind'], id = `act-${kind}`): PreparationAction => ({
  id,
  kind,
  config_revision: 1,
  impact: {
    executes_user_script: kind === 'run' || kind === 'run_when_ready',
    installs_packages: kind === 'run_when_ready',
    changes_environment: kind === 'run_when_ready',
    writes_to_project: [],
    ...(kind === 'run_when_ready' ? { impact_digest: DIGEST } : {}),
  },
})

const fig = (stem: string): CapturedFigureDescriptor =>
  ({
    asset_id: `runtime:plot.py#${stem}`,
    script: 'plot.py',
    entry: '__main__',
    stem,
    capture_source: 'pyplot',
    execution_profile: 'safe',
    original_artifact: null,
    size_mm: [80, 60],
    source_fingerprint: 'sha256:x',
    can_writeback_artifact: false,
    can_writeback_source: false,
  }) as CapturedFigureDescriptor

const report = (over: Partial<PreparationReport> = {}): PreparationReport => ({
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
  actions: [action('run'), action('run_when_ready'), action('recheck')],
  provider: { plan_id: 'prep-plan', attempt_id: null, attempts: 0, dependency: null },
  runtime_input: null,
  captured: [],
  result: null,
  ...over,
})

const running = (seq = 2, over: Partial<PreparationReport> = {}) =>
  report({
    phase: 'running',
    outcome: { kind: 'running' },
    observation_seq: seq,
    actions: [action('cancel')],
    provider: { plan_id: 'prep-plan', attempt_id: 'att-1', attempts: 1, dependency: null },
    ...over,
  })

const done = (stems: string[], attemptId = 'att-1', seq = 3, over: Partial<PreparationReport> = {}) =>
  report({
    phase: 'completed',
    outcome: { kind: 'succeeded' },
    facts: { execution_finished: true, figure_captured: true },
    observation_seq: seq,
    actions: [action('recheck')],
    captured: stems.map(fig),
    provider: { plan_id: 'prep-plan', attempt_id: attemptId, attempts: 1, dependency: null },
    ...over,
  })

const attempt = (over: Partial<PreparationAttempt>): PreparationAttempt => ({
  run: 1,
  seq: 1,
  attempt_id: 'att-1',
  profile: 'safe',
  interpreter: 'aaaaaaaaaaaa',
  launch: 'bbbbbbbbbbbb',
  cause: '',
  changed: [],
  outcome: 'succeeded',
  code: '',
  declined: '',
  ...over,
})

const panelFor = (fileId: string): PanelObject =>
  ({
    id: `panel-${fileId}`, type: 'panel', x: 0, y: 0, w: 100, h: 80, fileId, fileKind: 'png',
    nativeW: 100, nativeH: 80, script: 'plot.py', overrides: [],
  }) as unknown as PanelObject

const putPanel = (fileId: string) => {
  const p = panelFor(fileId)
  useDocumentStore.setState((st) => ({
    doc: { ...st.doc, objects: [...st.doc.objects.filter((o) => o.id !== p.id), p] },
  }))
}

const flush = async () => {
  for (let i = 0; i < 8; i++) await Promise.resolve()
  await new Promise((r) => setTimeout(r, 0))
}

const KEY = 'script:plot.py'
const store = () => useProjectPreparationStore.getState()
const entry = () => store().entries[KEY]
const open = (run = true) => store().open(scriptTarget('plot.py'), { run })
const claimed = (r: PreparationReport) => mockAct.mockResolvedValueOnce({ claimed: true, report: r })

beforeEach(() => {
  vi.useRealTimers()
  mockCreate.mockReset()
  mockGet.mockReset()
  mockAct.mockReset()
  vi.mocked(addRuntimePanelToCanvas).mockReset()
  vi.mocked(openFastEdit).mockReset()
  vi.mocked(openFastEdit).mockImplementation((id: string) => {
    putPanel(id)
    return 'editing'
  })
  vi.mocked(addRuntimePanelToCanvas).mockImplementation(((d: CapturedFigureDescriptor) => putPanel(d.asset_id)) as never)
  __setPreparationTimingForTests({ requestTimeoutMs: 15_000, pollMs: [10_000] })
  store().clear()
  useScriptArgvStore.getState().clear()
  useRuntimeAssetStore.setState({ assets: [], loadAssets: async () => {} })
  useDocumentStore.setState((st) => ({ doc: { ...st.doc, objects: [] } }))
  useUiStore.setState({ guideCard: 'closed' })
  setCurrentProjectId('pj-a')
})

afterEach(() => {
  store().clear()
  setCurrentProjectId(null)
})

describe('一次点击：检查 → 认领 run_when_ready（回显授权摘要）→ 自动进入编辑', () => {
  it('黄金路径：恰好一张图，不需要再点「运行」「进入编辑」；请求里永远没有 execution', async () => {
    mockCreate.mockResolvedValueOnce(report())
    claimed(running())
    await open()
    expect(mockAct).toHaveBeenCalledTimes(1)
    expect(mockAct.mock.calls[0][1]).toEqual({
      action_id: 'act-run_when_ready',
      expected_config_revision: 1,
      impact_digest: DIGEST,
    })
    expect(entry().armed).toBe(true)
    expect(openFastEdit).not.toHaveBeenCalled() // 还在跑
    // 后端跑完（SSE 提示后补拉）
    mockGet.mockResolvedValueOnce(done(['a']))
    await store().refresh(KEY)
    await vi.waitFor(() => expect(openFastEdit).toHaveBeenCalledTimes(1))
    expect(openFastEdit).toHaveBeenCalledWith('runtime:plot.py#a', expect.anything())
    expect(entry().editing).toEqual(['runtime:plot.py#a'])
    expect(entry().armed).toBe(false)
    expect(useUiStore.getState().guideCard).toBe('closed') // 进入编辑后卡片收起
    // 前端从不发 execution：创建与动作的请求体里都没有
    expect(JSON.stringify(mockCreate.mock.calls)).not.toContain('execution')
    expect(JSON.stringify(mockAct.mock.calls)).not.toContain('execution')
  })

  it('后端会话内静默回退（第二个 attempt）：同一个资产 id、只开一个面板，不再多开', async () => {
    mockCreate.mockResolvedValueOnce(report())
    claimed(running())
    await open()
    // safe 被拦、后端在同一会话里接上 native 回退：报告还是 running，attempt_id 换了（前端不发 execution）
    mockGet.mockResolvedValueOnce(
      running(3, {
        provider: { plan_id: 'prep-plan', attempt_id: 'att-2', attempts: 2, dependency: null },
        attempt_history: [
          attempt({ outcome: 'blocked', code: 'safe_file_operation_blocked' }),
          attempt({ seq: 2, attempt_id: 'att-2', profile: 'native', cause: 'safe_file_operation_blocked', changed: ['profile'], outcome: 'pending' }),
        ],
      }),
    )
    await store().refresh(KEY)
    expect(entry().armed).toBe(true) // 回退中途不撤销「打开并运行」
    expect(openFastEdit).not.toHaveBeenCalled()
    mockGet.mockResolvedValueOnce(done(['a'], 'att-2', 4))
    await store().refresh(KEY)
    await vi.waitFor(() => expect(openFastEdit).toHaveBeenCalledTimes(1))
    const panels = useDocumentStore.getState().doc.objects.filter((o) => o.type === 'panel')
    expect(panels).toHaveLength(1)
    expect(addRuntimePanelToCanvas).toHaveBeenCalledTimes(1)
  })

  it('这次运行替换掉了旧图名（unlinked_stems）：不自动进入——卡片详情里的恢复提示是用户必须看到的', async () => {
    mockCreate.mockResolvedValueOnce(report())
    claimed(running())
    await open()
    mockGet.mockResolvedValueOnce(done(['a'], 'att-1', 3, { unlinked_stems: ['Extra_map'] }))
    await store().refresh(KEY)
    await flush()
    await flush()
    expect(openFastEdit).not.toHaveBeenCalled()
    expect(entry().editing).toEqual([])
    expect(entry().armed).toBe(false)
  })

  it.each(['pill', 'closed'] as const)('用户把卡放到后台 / 收起（%s）之后才跑完：不抢他的工作区，角标 / 卡上仍有「进入编辑」', async (mode) => {
    mockCreate.mockResolvedValueOnce(report())
    claimed(running())
    await open()
    useUiStore.setState({ guideCard: mode })
    mockGet.mockResolvedValueOnce(done(['a']))
    await store().refresh(KEY)
    await flush()
    await flush()
    expect(openFastEdit).not.toHaveBeenCalled()
    expect(entry().editing).toEqual([])
    expect(entry().armed).toBe(false)
    expect(entry().report?.captured).toHaveLength(1) // 结果还在，用户随时点「进入编辑」
  })

  it.each(['pill', 'closed'] as const)('自动进入等素材清单期间用户把卡放到后台 / 收起（%s）：落地前复核，放弃进入，卡上仍可手动进入', async (mode) => {
    mockCreate.mockResolvedValueOnce(report())
    claimed(running())
    await open()
    // loadAssets 有两个调用方：onCompleted 的刷新（不 await）和 enterEdit 的 await——都挂住，等两个都到了再切走，最后一起放行
    const waiting: Array<() => void> = []
    const release = () => waiting.forEach((r) => r())
    const loadAssets = vi.fn(() => new Promise<void>((r) => waiting.push(r)))
    useRuntimeAssetStore.setState({ loadAssets })
    mockGet.mockResolvedValueOnce(done(['a']))
    await store().refresh(KEY)
    await vi.waitFor(() => expect(loadAssets).toHaveBeenCalledTimes(2)) // enterEdit 已挂在素材清单的 await 上
    useUiStore.setState({ guideCard: mode })
    release()
    await flush()
    await flush()
    expect(openFastEdit).not.toHaveBeenCalled()
    expect(addRuntimePanelToCanvas).not.toHaveBeenCalled()
    expect(entry().report?.captured).toHaveLength(1)
  })

  it('清单缓存里这张图的条目 descriptor 为 null（跑完前发出的清单请求被合并）：用报告里的描述符插入，仍能进入编辑', async () => {
    mockCreate.mockResolvedValueOnce(report())
    claimed(running())
    await open()
    useRuntimeAssetStore.setState({ assets: [{ id: 'runtime:plot.py#a', descriptor: null } as never] })
    mockGet.mockResolvedValueOnce(done(['a']))
    await store().refresh(KEY)
    await vi.waitFor(() => expect(openFastEdit).toHaveBeenCalledTimes(1))
    expect(addRuntimePanelToCanvas).toHaveBeenCalledTimes(1)
  })

  it('重跑已缓存的图、合并进来的旧清单带的是上一次的非空描述符：以这次报告的描述符插入（Codex #1044 r4285970263）', async () => {
    mockCreate.mockResolvedValueOnce(report())
    claimed(running())
    await open()
    useRuntimeAssetStore.setState({ assets: [{ id: 'runtime:plot.py#a', descriptor: { ...fig('a'), execution_profile: 'native' } } as never] })
    mockGet.mockResolvedValueOnce(done(['a']))
    await store().refresh(KEY)
    await vi.waitFor(() => expect(openFastEdit).toHaveBeenCalledTimes(1))
    expect(addRuntimePanelToCanvas).toHaveBeenCalledTimes(1)
    expect(vi.mocked(addRuntimePanelToCanvas).mock.calls[0][0]).toEqual(fig('a'))
  })

  it('清单里已有与这次一致的描述符：不重复插入', async () => {
    mockCreate.mockResolvedValueOnce(report())
    claimed(running())
    await open()
    useRuntimeAssetStore.setState({ assets: [{ id: 'runtime:plot.py#a', descriptor: fig('a') } as never] })
    mockGet.mockResolvedValueOnce(done(['a']))
    await store().refresh(KEY)
    await vi.waitFor(() => expect(openFastEdit).toHaveBeenCalledTimes(1))
    expect(addRuntimePanelToCanvas).not.toHaveBeenCalled()
  })

  it.each(['missing', 'layout_only'] as const)('openFastEdit 回 %s（没真的进入编辑）：不收卡、不记编辑，卡上保留手动「进入编辑」', async (outcome) => {
    mockCreate.mockResolvedValueOnce(report())
    claimed(running())
    await open()
    vi.mocked(openFastEdit).mockReset()
    vi.mocked(openFastEdit).mockReturnValue(outcome)
    mockGet.mockResolvedValueOnce(done(['a']))
    await store().refresh(KEY)
    await vi.waitFor(() => expect(openFastEdit).toHaveBeenCalledTimes(1))
    await flush()
    expect(useUiStore.getState().guideCard).toBe('card')
    expect(entry().editing).toEqual([])
    expect(entry().report?.captured).toHaveLength(1)
  })

  it('自动进入等素材清单期间换参数重开同一脚本（key / 焦点 / 展开都没变）：不打开旧配置跑出的图，也不记到新条目上', async () => {
    mockCreate.mockResolvedValueOnce(report())
    claimed(running())
    await open()
    const waiting: Array<() => void> = []
    const loadAssets = vi.fn(() => new Promise<void>((r) => waiting.push(r)))
    useRuntimeAssetStore.setState({ loadAssets })
    mockGet.mockResolvedValueOnce(done(['a']))
    await store().refresh(KEY)
    await vi.waitFor(() => expect(loadAssets).toHaveBeenCalledTimes(2))
    useScriptArgvStore.getState().setTokens('plot.py', ['--n', '2'])
    mockCreate.mockResolvedValueOnce(report({ session_id: 'psess-2' }))
    await open(false) // 同 key、同样聚焦展开，但目标（参数）换了
    expect(useUiStore.getState().guideCard).toBe('card')
    waiting.forEach((r) => r())
    await flush()
    await flush()
    expect(openFastEdit).not.toHaveBeenCalled()
    expect(addRuntimePanelToCanvas).not.toHaveBeenCalled()
    expect(entry().editing).toEqual([])
  })

  it('自动进入等素材清单期间用户切去别的卡（焦点换了）：同样放弃', async () => {
    mockCreate.mockResolvedValueOnce(report())
    claimed(running())
    await open()
    // loadAssets 有两个调用方：onCompleted 的刷新（不 await）和 enterEdit 的 await——都挂住，等两个都到了再切走，最后一起放行
    const waiting: Array<() => void> = []
    const release = () => waiting.forEach((r) => r())
    const loadAssets = vi.fn(() => new Promise<void>((r) => waiting.push(r)))
    useRuntimeAssetStore.setState({ loadAssets })
    mockGet.mockResolvedValueOnce(done(['a']))
    await store().refresh(KEY)
    await vi.waitFor(() => expect(loadAssets).toHaveBeenCalledTimes(2)) // enterEdit 已挂在素材清单的 await 上
    store().blur()
    release()
    await flush()
    await flush()
    expect(openFastEdit).not.toHaveBeenCalled()
  })

  it.each([
    ['仍在看这张卡', true],
    ['已切去别的卡', false],
  ] as const)('排版恢复中入口排队（deferred）：落地时 %s（仅仍在看才收卡片 / 记编辑）', async (_n, stays) => {
    mockCreate.mockResolvedValueOnce(report())
    claimed(running())
    await open()
    let finish: (r: 'ran') => void = () => {}
    // 排队的入口：恢复落定后才执行，执行前过传进来的守卫（真实现见 openRequestRestore.test）
    let entered = false
    let queued: () => void = () => {}
    vi.mocked(openFastEdit).mockImplementation(((id: string, opts?: { guard?: () => boolean }) => {
      queued = () => {
        if (opts?.guard && !opts.guard()) return
        entered = true
        putPanel(id)
      }
      return 'deferred'
    }) as never)
    vi.mocked(takeDeferredEntryOutcome).mockReturnValueOnce(new Promise((r) => (finish = r as never)) as never)
    mockGet.mockResolvedValueOnce(done(['a']))
    await store().refresh(KEY)
    await vi.waitFor(() => expect(openFastEdit).toHaveBeenCalledTimes(1))
    if (!stays) store().blur()
    queued()
    finish('ran')
    await flush()
    await flush()
    expect(entered).toBe(stays) // 切走之后排队的入口自己就不执行：不替换他此刻的工作区
    expect(useUiStore.getState().guideCard).toBe(stays ? 'closed' : 'card')
    expect(entry().editing.length).toBe(stays ? 1 : 0)
  })

  it('用户切去看了别的脚本的卡（聚焦不在这个条目）：同样不自动进入', async () => {
    mockCreate.mockResolvedValueOnce(report())
    claimed(running())
    await open()
    store().blur()
    mockGet.mockResolvedValueOnce(done(['a']))
    await store().refresh(KEY)
    await flush()
    await flush()
    expect(openFastEdit).not.toHaveBeenCalled()
  })

  it('多张图：不自动进入（要用户挑，绝不只取第一张）；之后卡片的进入编辑不再被抢', async () => {
    mockCreate.mockResolvedValueOnce(report())
    claimed(running())
    await open()
    mockGet.mockResolvedValueOnce(done(['a', 'b']))
    await store().refresh(KEY)
    await flush()
    expect(openFastEdit).not.toHaveBeenCalled()
    expect(addRuntimePanelToCanvas).not.toHaveBeenCalled()
    expect(entry().editing).toEqual([])
    expect(entry().armed).toBe(false)
  })

  it.each([
    ['失败', { phase: 'action_required', outcome: { kind: 'failed', code: 'script_error' } }],
    ['取消', { phase: 'cancelled', outcome: { kind: 'cancelled' } }],
    ['没出图', { phase: 'partial', outcome: { kind: 'execution_finished_no_figure' } }],
  ] as const)('%s：不进入编辑，也撤掉「打开并运行」的记号（之后手点的运行成功不会被自动抢走）', async (_n, over) => {
    mockCreate.mockResolvedValueOnce(report())
    claimed(running())
    await open()
    mockGet.mockResolvedValueOnce(running(3, over as Partial<PreparationReport>))
    await store().refresh(KEY)
    expect(entry().armed).toBe(false)
    mockGet.mockResolvedValueOnce(done(['a'], 'att-9', 5))
    await store().refresh(KEY)
    await flush()
    expect(openFastEdit).not.toHaveBeenCalled()
  })

  it('safe 被拦（blocked）而后端还没记下「为什么没回退」：可能紧接着就是回退，先不撤记号', async () => {
    mockCreate.mockResolvedValueOnce(report())
    claimed(running())
    await open()
    mockGet.mockResolvedValueOnce(
      running(3, {
        phase: 'action_required',
        outcome: { kind: 'failed', code: 'safe_file_operation_blocked' },
        attempt_history: [attempt({ outcome: 'blocked', code: 'safe_file_operation_blocked' })],
      }),
    )
    await store().refresh(KEY)
    expect(entry().armed).toBe(true)
  })
})

describe('已跑完的会话只给 run（没有 run_when_ready）：显式「打开并运行」认领 run 并武装这一次的新尝试（Codex #1044 r4280299198）', () => {
  const finished = () => done(['a'], 'att-1', 3, { actions: [action('run'), action('recheck')] })

  it('显式 run:true：认领 run，旧的 completed 结果不触发自动进入；新尝试跑完才进入', async () => {
    mockCreate.mockResolvedValueOnce(finished())
    mockAct.mockResolvedValueOnce({ claimed: true, report: running(4, { provider: { plan_id: 'prep-plan', attempt_id: 'att-2', attempts: 2, dependency: null } }) })
    await open()
    expect(mockAct).toHaveBeenCalledTimes(1)
    expect(mockAct.mock.calls[0][1]).toMatchObject({ action_id: 'act-run' })
    expect(entry().armed).toBe(true)
    await flush()
    expect(openFastEdit).not.toHaveBeenCalled() // 旧结果不进
    mockGet.mockResolvedValueOnce(done(['a'], 'att-2', 5, { actions: [action('recheck')] }))
    await store().refresh(KEY)
    await vi.waitFor(() => expect(openFastEdit).toHaveBeenCalledTimes(1))
  })

  it('上一轮已经自动进入过编辑（本地 editing 非空）：再点同一脚本的 ▶ 照样认领 run 重跑，不只是重开旧结果（Codex #1044 r4285899694）', async () => {
    mockCreate.mockResolvedValueOnce(finished())
    mockAct.mockResolvedValueOnce({ claimed: true, report: running(4, { provider: { plan_id: 'prep-plan', attempt_id: 'att-2', attempts: 2, dependency: null } }) })
    await open()
    mockGet.mockResolvedValueOnce(done(['a'], 'att-2', 5, { actions: [action('run'), action('recheck')] }))
    await store().refresh(KEY)
    await vi.waitFor(() => expect(openFastEdit).toHaveBeenCalledTimes(1))
    expect(entry().editing.length).toBeGreaterThan(0)

    mockCreate.mockResolvedValueOnce(done(['a'], 'att-2', 6, { actions: [action('run'), action('recheck')] }))
    mockAct.mockResolvedValueOnce({ claimed: true, report: running(7, { provider: { plan_id: 'prep-plan', attempt_id: 'att-3', attempts: 3, dependency: null } }) })
    await open()
    expect(mockAct).toHaveBeenCalledTimes(2)
    expect(mockAct.mock.calls[1][1]).toMatchObject({ action_id: 'act-run' })
  })

  it('卡上「继续」带运行意图重开（rerun:false）落到已跑完的同配置会话：复用结果，不认领 run 重跑', async () => {
    mockCreate.mockResolvedValueOnce(finished())
    await store().open(scriptTarget('plot.py'), { run: true, rerun: false })
    expect(mockAct).not.toHaveBeenCalled()
    expect(entry().wantsRun).toBe(true)
  })

  it('认领 run 的响应里报告还是旧的 completed：武装了也不进入旧结果，等新尝试', async () => {
    mockCreate.mockResolvedValueOnce(finished())
    mockAct.mockResolvedValueOnce({ claimed: true, report: finished() })
    await open()
    await flush()
    await flush()
    expect(openFastEdit).not.toHaveBeenCalled()
    expect(entry().armed).toBe(true)
  })

  // Codex #1044 r4280400832：武装标记不能是跨项目共享的模块级状态
  const defer = <T,>() => {
    let resolve!: (v: T) => void
    const promise = new Promise<T>((r) => (resolve = r))
    return { promise, resolve }
  }

  it('A 的 rerun 在途时切到 B（同相对脚本路径）：B 的普通 run 不武装', async () => {
    mockCreate.mockResolvedValueOnce(finished())
    const a = defer<{ claimed: boolean; report: PreparationReport }>()
    mockAct.mockReturnValueOnce(a.promise as never)
    const opening = open()
    await vi.waitFor(() => expect(mockAct).toHaveBeenCalledTimes(1))
    store().clear()
    setCurrentProjectId('pj-b')
    mockCreate.mockResolvedValueOnce(finished())
    await open(false)
    mockAct.mockResolvedValueOnce({ claimed: true, report: running(4, { provider: { plan_id: 'prep-plan', attempt_id: 'att-2', attempts: 2, dependency: null } }) })
    await store().act(KEY, 'run') // 普通运行，不是一键
    expect(entry().armed).toBe(false)
    a.resolve({ claimed: true, report: running(4) })
    await opening
    expect(entry().armed).toBe(false)
  })

  it('B 的 rerun 在途时 A 迟到收尾：不影响 B 的武装', async () => {
    mockCreate.mockResolvedValueOnce(finished())
    const a = defer<{ claimed: boolean; report: PreparationReport }>()
    mockAct.mockReturnValueOnce(a.promise as never)
    const openingA = open()
    await vi.waitFor(() => expect(mockAct).toHaveBeenCalledTimes(1))
    store().clear()
    setCurrentProjectId('pj-b')
    mockCreate.mockResolvedValueOnce(finished())
    const b = defer<{ claimed: boolean; report: PreparationReport }>()
    mockAct.mockReturnValueOnce(b.promise as never)
    const openingB = open()
    await vi.waitFor(() => expect(mockAct).toHaveBeenCalledTimes(2))
    a.resolve({ claimed: true, report: running(4) }) // A 迟到收尾
    await openingA
    b.resolve({ claimed: true, report: running(4, { provider: { plan_id: 'prep-plan', attempt_id: 'att-2', attempts: 2, dependency: null } }) })
    await openingB
    expect(entry().armed).toBe(true)
  })

  it('A 的迟到刷新收尾不删 B 的在途刷新（inflight 只删自己）：B 再刷新仍复用同一个请求', async () => {
    mockCreate.mockResolvedValueOnce(running())
    await open(false)
    const a = defer<PreparationReport>()
    mockGet.mockReturnValueOnce(a.promise as never)
    const refreshA = store().refresh(KEY)
    store().clear()
    setCurrentProjectId('pj-b')
    mockCreate.mockResolvedValueOnce(running())
    await open(false)
    const b = defer<PreparationReport>()
    mockGet.mockReturnValueOnce(b.promise as never)
    const refreshB = store().refresh(KEY)
    a.resolve(running(9))
    await refreshA
    void store().refresh(KEY) // 若 A 的收尾顶掉了 B 的登记，这里会再发一个请求
    expect(mockGet).toHaveBeenCalledTimes(2)
    b.resolve(running(9))
    await refreshB
  })

  const withRun = (r: PreparationReport) => ({ ...r, actions: [action('run'), action('recheck')] })

  it.each([
    ['partial（没出图）', () => withRun(report({ phase: 'partial', outcome: { kind: 'execution_finished_no_figure' }, observation_seq: 3 }))],
    ['action_required（上次失败）', () => withRun(report({ phase: 'action_required', outcome: { kind: 'failed', code: 'script_error' }, observation_seq: 3 }))],
  ])('上次 %s 后一键：认领 run；新一轮成功一张图 → 进入编辑', async (_n, make) => {
    mockCreate.mockResolvedValueOnce(make())
    mockAct.mockResolvedValueOnce({ claimed: true, report: running(4, { provider: { plan_id: 'prep-plan', attempt_id: 'att-2', attempts: 2, dependency: null } }) })
    await open()
    expect(mockAct).toHaveBeenCalledTimes(1)
    expect(mockAct.mock.calls[0][1]).toMatchObject({ action_id: 'act-run' })
    expect(entry().armed).toBe(true)
    mockGet.mockResolvedValueOnce(done(['a'], 'att-2', 5, { actions: [action('recheck')] }))
    await store().refresh(KEY)
    await vi.waitFor(() => expect(openFastEdit).toHaveBeenCalledTimes(1))
  })

  it('上次失败且需要用户先答（脚本要参数）：不代认领，停在卡上', async () => {
    mockCreate.mockResolvedValueOnce(
      withRun(report({ phase: 'action_required', outcome: { kind: 'failed', code: 'script_needs_arguments' }, observation_seq: 3 })),
    )
    await open()
    expect(mockAct).not.toHaveBeenCalled()
    expect(entry().armed).toBe(false)
  })

  it('非显式打开（只是查看）：不认领 run、不跑', async () => {
    mockCreate.mockResolvedValueOnce(finished())
    await open(false)
    expect(mockAct).not.toHaveBeenCalled()
    expect(entry().armed).toBe(false)
  })
})

describe('只有明确的入口才认领', () => {
  it('普通 open（只检查）：报告里有 run_when_ready 也不认领', async () => {
    mockCreate.mockResolvedValueOnce(report())
    await open(false)
    expect(mockAct).not.toHaveBeenCalled()
    expect(entry().armed).toBe(false)
  })

  it('老后端 / 杀开关：报告里没有 run_when_ready → 不认领（也不退而认领 run）', async () => {
    mockCreate.mockResolvedValueOnce(report({ actions: [action('run'), action('recheck')] }))
    await open()
    expect(mockAct).not.toHaveBeenCalled()
  })

  it('必填参数没填齐：不认领（卡片换成参数卡，填完用卡上的主按钮）', async () => {
    const schema = {
      status: 'complete',
      form_enabled: true,
      reasons: [],
      exclusive_groups: [],
      subcommands: null,
      arguments: [
        { id: 'scale', dest: 'scale', flags: ['--scale'], positional: false, required: true, arity: 1, action: 'store', type: 'float', editable: true, hidden: false, help: null, default: null, choices: null, metavar: null, role: null },
      ],
    }
    mockCreate.mockResolvedValueOnce(
      report({
        requirements: [
          { id: 'arguments', kind: 'script_arguments', code: 'script_arguments', blocking: false, payload: { schema: schema as never, argv_count: 0, run_config: null } },
        ],
      }),
    )
    await open()
    expect(mockAct).not.toHaveBeenCalled()
  })

  describe('只在卡片会说「可以运行了」时代认领：有要用户先看 / 先答的事就停在各自的卡上', () => {
    const arm = action('run_when_ready')
    const cases: [string, Partial<PreparationReport>][] = [
      [
        '要先选运行目录',
        {
          phase: 'awaiting_configuration',
          requirements: [
            { id: 'workdir', kind: 'workdir_choice', code: 'workdir_confirmation_required', payload: { recommended: 'project_root', options: [], conflicts: [] } as never },
          ],
          actions: [action('recheck'), arm],
        },
      ],
      [
        '要先装组件（用户该先看见装什么、装到哪）',
        {
          phase: 'awaiting_confirmation',
          requirements: [{ id: 'dependencies', kind: 'dependency_authorization', code: 'dependency_preparation_required', payload: {} }],
          actions: [action('prepare_dependencies'), action('recheck'), arm],
        },
      ],
      [
        '脚本会弹窗（提醒要先读）',
        {
          requirements: [
            { id: 'interaction', kind: 'gui_dialog', code: 'script_uses_gui_dialog', blocking: false, payload: { calls: [{ api: 'tkinter.filedialog.askopenfilename', kind: 'file', line: 7 }], truncated: false } },
          ],
          actions: [action('run'), action('recheck'), arm],
        },
      ],
      [
        '有 import 来源不明（提醒要先读）',
        { checks: [{ id: 'target', status: 'ok' }, { id: 'dependencies', status: 'ok', detail: { origins: { count: 2, by_reason: { module_origin_ambiguous: 2 } } } }], actions: [action('run'), action('recheck'), arm] },
      ],
    ]
    it.each(cases)('%s：不认领', async (_name, over) => {
      mockCreate.mockResolvedValueOnce(report(over))
      await open()
      expect(mockAct).not.toHaveBeenCalled()
      expect(entry().armed).toBe(false)
    })
  })

  it('检查失败（没能建会话）：不认领', async () => {
    mockCreate.mockRejectedValueOnce(new ApiError('x', 404, { code: 'script_not_found' }))
    await open()
    expect(mockAct).not.toHaveBeenCalled()
    expect(entry().failure?.code).toBe('script_not_found')
  })

  it('认领被拒（影响变了 409）：说一句、重读报告，不重发、不记号', async () => {
    mockCreate.mockResolvedValueOnce(report())
    mockAct.mockRejectedValueOnce(new ApiError('x', 409, { code: 'preparation_impact_changed' }))
    mockGet.mockResolvedValueOnce(report({ observation_seq: 2 }))
    await open()
    expect(mockAct).toHaveBeenCalledTimes(1)
    expect(entry().rejection?.code).toBe('preparation_impact_changed')
    expect(entry().armed).toBe(false)
  })

  it('重新打开（continue / 再试一次）沿用这份意图；同参数再 open 不带记号', async () => {
    mockCreate.mockResolvedValueOnce(report())
    claimed(running())
    await open()
    expect(entry().wantsRun).toBe(true)
    mockCreate.mockResolvedValueOnce(report({ observation_seq: 5 }))
    await store().open(scriptTarget('plot.py'))
    expect(entry().wantsRun).toBe(false)
    expect(entry().armed).toBe(false)
  })
})

describe('argv 换了：旧会话里在等的意图原子转移，不并存', () => {
  it('旧会话意图 active → 新会话认领带 transfer_from；成功后不再带', async () => {
    useScriptArgvStore.getState().setTokens('plot.py', ['--n', '1'])
    mockCreate.mockResolvedValueOnce(
      report({
        intent: { id: 'i1', status: 'active', reason: '', waiting_on: 'dependencies', steps: 0, max_steps: 2, expires_at: 1, attempt_id: null, transferred: false },
      }),
    )
    await open(false)
    useScriptArgvStore.getState().setTokens('plot.py', ['--n', '2'])
    mockCreate.mockResolvedValueOnce(report({ session_id: 'psess-2' }))
    await open(false)
    expect(entry().transferFrom).toBe('psess-1')
    claimed(running(2, { session_id: 'psess-2' }))
    await store().act(KEY, 'run_when_ready')
    expect(mockAct.mock.calls[0][0]).toBe('psess-2')
    expect(mockAct.mock.calls[0][1]).toMatchObject({ transfer_from: 'psess-1' })
    expect(entry().transferFrom).toBeNull()
  })

  it('旧会话没有在等的意图：不带 transfer_from（带了后端 409）', async () => {
    useScriptArgvStore.getState().setTokens('plot.py', ['--n', '1'])
    mockCreate.mockResolvedValueOnce(report())
    await open(false)
    useScriptArgvStore.getState().setTokens('plot.py', ['--n', '2'])
    mockCreate.mockResolvedValueOnce(report({ session_id: 'psess-2' }))
    await open(false)
    expect(entry().transferFrom).toBeNull()
    claimed(running(2, { session_id: 'psess-2' }))
    await store().act(KEY, 'run_when_ready')
    expect(mockAct.mock.calls[0][1]).not.toHaveProperty('transfer_from')
  })

  it('转移被拒（已执行 / 已取消）：下一次点击不再带它', async () => {
    useScriptArgvStore.getState().setTokens('plot.py', ['--n', '1'])
    mockCreate.mockResolvedValueOnce(
      report({
        intent: { id: 'i1', status: 'active', reason: '', waiting_on: '', steps: 0, max_steps: 2, expires_at: 1, attempt_id: null, transferred: false },
      }),
    )
    await open(false)
    useScriptArgvStore.getState().setTokens('plot.py', ['--n', '2'])
    mockCreate.mockResolvedValueOnce(report({ session_id: 'psess-2' }))
    await open(false)
    mockAct.mockRejectedValueOnce(new ApiError('x', 409, { code: 'preparation_run_intent_not_transferable' }))
    mockGet.mockResolvedValueOnce(report({ session_id: 'psess-2', observation_seq: 2 }))
    await store().act(KEY, 'run_when_ready')
    expect(entry().rejection?.code).toBe('preparation_run_intent_not_transferable')
    expect(entry().transferFrom).toBeNull()
  })
})

describe('状态丢失后以后端报告为准（Codex #1044 r4271063082 / r4271063100）', () => {
  const live = (over: Partial<PreparationReport> = {}) =>
    running(2, {
      intent: { id: 'i1', status: 'launched', reason: '', waiting_on: '', steps: 1, max_steps: 2, expires_at: 1, attempt_id: 'att-1', transferred: false },
      ...over,
    })

  it('认领响应丢了（连接失败）：之后补拉到的报告证实意图存在 → 自动进入编辑照样兑现', async () => {
    mockCreate.mockResolvedValueOnce(report())
    mockAct.mockRejectedValueOnce(new TypeError('network'))
    mockGet.mockResolvedValueOnce(live())
    await open() // 失败后的补拉读到 running + 意图：以报告为准恢复
    expect(entry().armed).toBe(true)
    mockGet.mockResolvedValueOnce(done(['a']))
    await store().refresh(KEY)
    await vi.waitFor(() => expect(openFastEdit).toHaveBeenCalledTimes(1))
  })

  it('卡上点「打开并运行」（条目不是从入口打开的）认领响应丢了：补拉到意图 → 武装；没收到 → 记号清掉，之后别处的意图不武装', async () => {
    mockCreate.mockResolvedValueOnce(report())
    await open(false)
    mockAct.mockRejectedValueOnce(new TypeError('network'))
    mockGet.mockResolvedValueOnce(live())
    await store().act(KEY, 'run_when_ready')
    expect(entry().armed).toBe(true)

    store().clear()
    mockCreate.mockResolvedValueOnce(report())
    await open(false)
    mockAct.mockRejectedValueOnce(new TypeError('network'))
    mockGet.mockResolvedValueOnce(report({ observation_seq: 2 })) // 没收到
    await store().act(KEY, 'run_when_ready')
    expect(entry().armMaybe).toBe(false)
    mockGet.mockResolvedValueOnce(live({ observation_seq: 3 })) // 别处武装的
    await store().refresh(KEY)
    expect(entry().armed).toBe(false)
  })

  it('认领响应丢了、而服务端其实没收到（报告里仍有 run_when_ready、没有意图）：不武装', async () => {
    mockCreate.mockResolvedValueOnce(report())
    mockAct.mockRejectedValueOnce(new TypeError('network'))
    mockGet.mockResolvedValueOnce(report({ observation_seq: 2 }))
    await open()
    await store().refresh(KEY)
    expect(entry().armed).toBe(false)
    mockGet.mockResolvedValueOnce(done(['a'], 'att-9', 5))
    await store().refresh(KEY)
    await flush()
    await flush()
    expect(openFastEdit).not.toHaveBeenCalled()
  })

  it('刷新页面后同一个「打开并运行」入口再打开：报告里意图仍在跑 → 恢复「自动进入编辑」', async () => {
    mockCreate.mockResolvedValueOnce(live())
    await open() // 新的前端内存：没有 armed 记号
    expect(entry().armed).toBe(true)
    mockGet.mockResolvedValueOnce(done(['a']))
    await store().refresh(KEY)
    await vi.waitFor(() => expect(openFastEdit).toHaveBeenCalledTimes(1))
  })

  it('认领响应丢了、首次补拉时快脚本已跑完（completed + 本次点击的意图）→ 仍自动进入编辑', async () => {
    mockCreate.mockResolvedValueOnce(report())
    await open(false)
    mockAct.mockRejectedValueOnce(new TypeError('network'))
    mockGet.mockResolvedValueOnce(done(['a'], 'att-1', 4, { intent: live().intent }))
    await store().act(KEY, 'run_when_ready')
    await vi.waitFor(() => expect(openFastEdit).toHaveBeenCalledTimes(1))
  })

  it('运行中再点一次同参数「打开并运行」、检查回来时脚本已跑完：记号不被重置，自动进入编辑照样兑现（Codex #1044 r4285653253）', async () => {
    mockCreate.mockResolvedValueOnce(report())
    claimed(live())
    await open()
    expect(entry().armed).toBe(true)
    mockCreate.mockResolvedValueOnce(done(['a'], 'att-1', 4, { intent: live().intent }))
    await open()
    await vi.waitFor(() => expect(openFastEdit).toHaveBeenCalledTimes(1))
  })

  it('认领响应丢了、再点一次同参数「打开并运行」：armMaybe 留着，本次点击的意图跑完照样进入', async () => {
    mockCreate.mockResolvedValueOnce(report())
    await open(false)
    mockAct.mockRejectedValueOnce(new TypeError('network'))
    mockGet.mockRejectedValueOnce(new TypeError('network'))
    await store().act(KEY, 'run_when_ready')
    expect(entry().armMaybe).toBe(true)
    mockCreate.mockResolvedValueOnce(done(['a'], 'att-1', 4, { intent: live().intent }))
    await open()
    await vi.waitFor(() => expect(openFastEdit).toHaveBeenCalledTimes(1))
  })

  it('认领响应丢了、再点一次认领成功：armMaybe 一组记号清掉，跑完后重复的已完成报告不再武装（Codex #1044 r4285832844）', async () => {
    mockCreate.mockResolvedValueOnce(report())
    await open(false)
    mockAct.mockRejectedValueOnce(new TypeError('network'))
    mockGet.mockRejectedValueOnce(new TypeError('network'))
    await store().act(KEY, 'run_when_ready')
    expect(entry().armMaybe).toBe(true)
    claimed(live())
    await store().act(KEY, 'run_when_ready')
    expect(entry().armed).toBe(true)
    expect(entry().armMaybe).toBe(false)
    expect(entry().armMaybeBaseline).toBe(null)
    expect(entry().armMaybeFrom).toBe(null)
  })

  it('「打开并运行」的首次检查超时、恢复后的重新检查成功：接着认领 run_when_ready，一键意图不丢（Codex #1044 r4290881812）', async () => {
    mockCreate.mockRejectedValueOnce(new TypeError('network'))
    await open()
    expect(mockAct).not.toHaveBeenCalled()
    expect(entry().wantsRun).toBe(true)
    mockCreate.mockResolvedValueOnce(report())
    claimed(running())
    await store().refresh(KEY)
    expect(mockAct).toHaveBeenCalledTimes(1)
    expect(entry().armed).toBe(true)
  })

  it('卡上「继续」（run:true, rerun:false）首次检查超时、恢复后落到已跑完的同配置会话：不认领 run 重跑（Codex #1152 补评 r4305863214）', async () => {
    mockCreate.mockRejectedValueOnce(new TypeError('network'))
    await store().open(scriptTarget('plot.py'), { run: true, rerun: false })
    expect(entry().wantsRun).toBe(true)
    expect(entry().allowRerun).toBe(false)
    mockCreate.mockResolvedValueOnce(done(['a'], 'att-1', 3, { actions: [action('run'), action('recheck')] }))
    await store().refresh(KEY)
    expect(mockAct).not.toHaveBeenCalled()
  })

  it('对照：明确「打开并运行」（rerun 未设）首次检查超时、恢复后落到已跑完的同配置会话：仍认领 run 重跑', async () => {
    mockCreate.mockRejectedValueOnce(new TypeError('network'))
    await open()
    expect(entry().allowRerun).toBe(true)
    mockCreate.mockResolvedValueOnce(done(['a'], 'att-1', 3, { actions: [action('run'), action('recheck')] }))
    mockAct.mockResolvedValueOnce({ claimed: true, report: running(4) })
    await store().refresh(KEY)
    expect(mockAct).toHaveBeenCalledTimes(1)
    expect(mockAct.mock.calls[0][1]).toMatchObject({ action_id: 'act-run' })
  })

  it('普通打开（不是一键入口）的首次检查超时后恢复：不代认领', async () => {
    mockCreate.mockRejectedValueOnce(new TypeError('network'))
    await open(false)
    mockCreate.mockResolvedValueOnce(report())
    await store().refresh(KEY)
    expect(mockAct).not.toHaveBeenCalled()
  })

  it('认领响应丢了、但已跑完的报告带的是点击前就有的旧意图：不算这次点击，不自动进入', async () => {
    mockCreate.mockResolvedValueOnce(report({ intent: live().intent }))
    await open(false)
    mockAct.mockRejectedValueOnce(new TypeError('network'))
    mockGet.mockResolvedValueOnce(done(['a'], 'att-1', 4, { intent: live().intent }))
    await store().act(KEY, 'run_when_ready')
    await flush()
    await flush()
    expect(openFastEdit).not.toHaveBeenCalled()
  })

  it('卡上点击、补拉赢了 POST 的提交竞态（报告还是点击前的样子）：保留 armMaybe，之后的 running / completed 照样武装', async () => {
    mockCreate.mockResolvedValueOnce(report())
    await open(false)
    mockAct.mockRejectedValueOnce(new TypeError('network'))
    mockGet.mockResolvedValueOnce(report()) // 观察序号没越过点击前
    await store().act(KEY, 'run_when_ready')
    expect(entry().armMaybe).toBe(true)
    mockGet.mockResolvedValueOnce(live({ observation_seq: 2 }))
    await store().refresh(KEY)
    expect(entry().armed).toBe(true)
    mockGet.mockResolvedValueOnce(done(['a'], 'att-1', 3))
    await store().refresh(KEY)
    await vi.waitFor(() => expect(openFastEdit).toHaveBeenCalledTimes(1))
  })

  it.each(['armed（同窗口认领）', 'wantsRun（刷新后从入口重开）'])('失败退役这次意图（%s）：之后普通「重试」跑成一张图，不再自动进入', async (mode) => {
    const failed = (seq: number) =>
      report({
        phase: 'action_required',
        outcome: { kind: 'failed', code: 'script_error' },
        observation_seq: seq,
        actions: [action('recheck')],
        intent: live().intent,
      })
    if (mode.startsWith('armed')) {
      mockCreate.mockResolvedValueOnce(report())
      claimed(running())
      await open()
    } else {
      mockCreate.mockResolvedValueOnce(live())
      await open()
    }
    expect(entry().armed).toBe(true)
    mockGet.mockResolvedValueOnce(failed(3))
    await store().refresh(KEY)
    expect(entry().armed).toBe(false)
    expect(entry().wantsRun).toBe(false)
    mockGet.mockResolvedValueOnce(live({ observation_seq: 4 })) // 普通重试：后端意图仍是 launched
    await store().refresh(KEY)
    expect(entry().armed).toBe(false)
    mockGet.mockResolvedValueOnce(done(['a'], 'att-2', 5, { intent: live().intent }))
    await store().refresh(KEY)
    await flush()
    await flush()
    expect(openFastEdit).not.toHaveBeenCalled()
  })

  it('普通打开（不是「打开并运行」入口）遇到别处武装的意图：不替它武装', async () => {
    mockCreate.mockResolvedValueOnce(live())
    await open(false)
    expect(entry().armed).toBe(false)
  })

  it('打开一个已经跑完的会话（意图 launched、phase completed）：不因重建记号而自动进入', async () => {
    mockCreate.mockResolvedValueOnce(done(['a'], 'att-1', 3, { intent: live().intent }))
    await open()
    await flush()
    await flush()
    expect(openFastEdit).not.toHaveBeenCalled()
  })

  it('另一份运行配置的意图还在（前端丢了旧会话 id）：服务端 409 带回旧会话 id → 自动带 transfer_from 重试一次', async () => {
    mockCreate.mockResolvedValueOnce(report({ session_id: 'psess-2' }))
    mockAct.mockRejectedValueOnce(new ApiError('x', 409, { code: 'preparation_run_intent_conflict', params: { session_id: 'psess-old' } }))
    claimed(running(2, { session_id: 'psess-2' }))
    await open()
    expect(mockAct).toHaveBeenCalledTimes(2)
    expect(mockAct.mock.calls[0][1]).not.toHaveProperty('transfer_from')
    expect(mockAct.mock.calls[1][1]).toMatchObject({ transfer_from: 'psess-old' })
    expect(entry().armed).toBe(true)
  })

  it('重试后又是冲突：只重试这一次，不无限循环', async () => {
    mockCreate.mockResolvedValueOnce(report())
    const conflict = () => new ApiError('x', 409, { code: 'preparation_run_intent_conflict', params: { session_id: 'psess-old' } })
    mockAct.mockRejectedValueOnce(conflict()).mockRejectedValueOnce(conflict())
    mockGet.mockResolvedValue(report({ observation_seq: 3 }))
    await open()
    expect(mockAct).toHaveBeenCalledTimes(2)
  })

  it('冲突带回的会话也转移不了：只重试一次，不循环', async () => {
    mockCreate.mockResolvedValueOnce(report())
    mockAct.mockRejectedValueOnce(new ApiError('x', 409, { code: 'preparation_run_intent_conflict', params: { session_id: 'psess-old' } }))
    mockAct.mockRejectedValueOnce(new ApiError('x', 409, { code: 'preparation_run_intent_not_transferable' }))
    mockGet.mockResolvedValue(report({ observation_seq: 3 }))
    await open()
    expect(mockAct).toHaveBeenCalledTimes(2)
  })
})

describe('沿用上次成功的参数（#982）：只从明确的打开动作发，值不回前端', () => {
  const reusedReport = (over: Partial<PreparationReport> = {}) =>
    report({ target: { kind: 'script', script: 'plot.py', entry: '__main__', asset_id: null, stem: null, argv_count: 2 }, ...over })

  it('scriptTarget 默认不带；明确 reuse 且草稿为空才带；草稿里有参数时用户给的赢', () => {
    expect(scriptTarget('plot.py')).toEqual({ script: 'plot.py' })
    expect(scriptTarget('plot.py', { reuse: true })).toEqual({ script: 'plot.py', reuse_last_success: true })
    useScriptArgvStore.getState().setTokens('plot.py', ['--n', '1'])
    expect(scriptTarget('plot.py', { reuse: true })).toEqual({ script: 'plot.py', argv: ['--n', '1'], argv_sensitive: false })
  })

  it('请求体只在明确要求时带 reuse_last_success；没有 argv 一起带', async () => {
    mockCreate.mockResolvedValueOnce(report())
    await store().open(scriptTarget('plot.py', { reuse: true }), { run: false })
    expect(mockCreate.mock.calls[0][0]).toEqual({ script: 'plot.py', reuse_last_success: true })
  })

  it('会话沿用了参数：报告只给个数，条目据此算出「沿用 N 个」；沿用不算「草稿与冻结的参数不同」', async () => {
    mockCreate.mockResolvedValueOnce(reusedReport())
    await store().open(scriptTarget('plot.py', { reuse: true }), { run: false })
    expect(reusedArgCount(entry())).toBe(2)
    expect(draftDiffers(entry().target)).toBe(false) // 否则卡片会误报「参数已填好」
  })

  it('没沿用（后端核验不成立 → 报告里 argv_count 为 0 / 缺）：个数是 0', async () => {
    mockCreate.mockResolvedValueOnce(report())
    await store().open(scriptTarget('plot.py', { reuse: true }), { run: false })
    expect(reusedArgCount(entry())).toBe(0)
  })

  it('「打开并运行」沿用了参数时不代认领：先让用户看到「沿用 N 个」（卡上有清除）', async () => {
    mockCreate.mockResolvedValueOnce(reusedReport())
    await store().open(scriptTarget('plot.py', { reuse: true }), { run: true })
    expect(mockAct).not.toHaveBeenCalled()
    expect(entry().wantsRun).toBe(true)
  })

  it('没沿用到任何参数时「打开并运行」照常一键认领', async () => {
    mockCreate.mockResolvedValueOnce(report())
    claimed(running())
    await store().open(scriptTarget('plot.py', { reuse: true }), { run: true })
    expect(mockAct).toHaveBeenCalledTimes(1)
  })

  it('清除：用不带标志的目标重新打开（另一个会话，旧报告不留），不替用户运行', async () => {
    mockCreate.mockResolvedValueOnce(reusedReport())
    await store().open(scriptTarget('plot.py', { reuse: true }), { run: true })
    mockCreate.mockResolvedValueOnce(report({ session_id: 'psess-2' }))
    await store().open(scriptTarget('plot.py'), { run: false })
    expect(mockCreate.mock.calls[1][0]).toEqual({ script: 'plot.py' })
    expect(entry().report?.session_id).toBe('psess-2')
    expect(reusedArgCount(entry())).toBe(0)
    expect(mockAct).not.toHaveBeenCalled()
  })

  it('清除的检查在途时，旧会话的报告不留在卡上（它属于另一份运行配置）', async () => {
    mockCreate.mockResolvedValueOnce(reusedReport())
    await store().open(scriptTarget('plot.py', { reuse: true }), { run: false })
    let release!: (r: PreparationReport) => void
    mockCreate.mockReturnValueOnce(new Promise<PreparationReport>((r) => (release = r)))
    const pending = store().open(scriptTarget('plot.py'), { run: false })
    expect(entry().report).toBeNull()
    release(report({ session_id: 'psess-2' }))
    await pending
    expect(entry().report?.session_id).toBe('psess-2')
  })
})
