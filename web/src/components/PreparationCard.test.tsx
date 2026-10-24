/**
 * 准备引导卡（T13b，取代 T09 的准备面板；ADR 0116）：
 *   * 「卡片一句话读懂」：每一种状态 × 每一种语种，默认可见区至多一句话、至多一个主按钮（`visibleBlocks` 同一把尺子），
 *     详情默认不展开；
 *   * 主按钮就是报告里后端给的那件事：没有 `run` 动作就没有「运行」；
 *   * 必填参数没填齐时任何卡都不说「可以运行」：换成参数卡、主按钮置灰；填齐了才是「继续」；
 *   * 运行目录在卡里直接选：推荐项预选，确认只回答目录（同一次 PATCH），不运行；
 *   * 执行结束 ≠ 首图成功：跑完没图说「跑完了，没有出图」，主按钮「知道了」；失败详情第一行是脚本的错误原文；
 *   * 进入编辑直接用这次捕获的图（加进画布），不发任何运行请求；之后卡片自动收起；
 *   * 运行中主按钮「放到后台」（缩成角标），「停止」是文字按钮；
 *   * 同一问只有一个展示面：卡片展示那一问时认领，原对话框让开；卡片收起就放手，对话框接着显示同一问；缩成角标时脚本一发问就自动展开。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  createPreparationSession: vi.fn(),
  fetchPreparationSession: vi.fn(),
  actOnPreparationSession: vi.fn(),
  probeScript: vi.fn(),
  fetchEngineEnvironment: vi.fn().mockResolvedValue({}),
  fetchDependencyOffer: vi.fn(),
  fetchScriptArguments: vi.fn().mockResolvedValue({ ok: true, script: 'plot.py', arguments: { status: 'none', arguments: [] } }),
  fetchPanels: vi.fn(),
  fetchReadiness: vi.fn().mockResolvedValue({
    project_id: 'project-a', fingerprint: 'empty-readiness', generated_at: 0,
    panels: [], conflicts: [], issues: [],
    summary: { total: 0, editable: 0, auto_linkable: 0, needs_probe: 0, conflict: 0, source_missing: 0, layout_only: 0 },
    project: { writable: true, registry_valid: true, scan_ok: true, can_rescan: true },
  }),
  refreshProject: vi.fn(),
  cancelNativeSession: vi.fn().mockResolvedValue({ confirmed: true, session: {} }),
  startConvert: vi.fn(),
  fetchAiSession: vi.fn(),
  fetchConvert: vi.fn(),
  adoptConvert: vi.fn(),
  discardConvert: vi.fn(),
  resolveConvertDependencies: vi.fn(),
  replanConvertDependencies: vi.fn(),
  aiCancel: vi.fn().mockResolvedValue({ ok: true }),
  requestAiFix: vi.fn(),
}))
vi.mock('@/store/liveSync', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/store/liveSync')>()),
  refreshProjectNow: vi.fn(async () => {}),
}))
vi.mock('@/store/workspace', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/store/workspace')>()),
  addRuntimePanelToCanvas: vi.fn(),
  openFastEdit: vi.fn(() => 'editing'),
}))

import {
  actOnPreparationSession,
  ApiError,
  cancelNativeSession,
  createPreparationSession,
  fetchDependencyOffer,
  fetchPreparationSession,
  fetchPanels,
  refreshProject,
  probeScript,
  type CapturedFigureDescriptor,
  type PreparationAction,
  type PreparationAttempt,
  type PreparationOriginFacts,
  type PreparationReport,
} from '@/lib/api'
import { i18n, resources, t as translate } from '@/i18n'
import { setCurrentProjectId } from '@/lib/session'
import { ForeignScriptCard, PreparationCard } from '@/components/PreparationCard'
import { ScriptInputDialog } from '@/components/ScriptInputDialog'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { useProjectPreparationStore } from '@/store/projectPreparationStore'
import { useNativeSessionStore } from '@/store/nativeSessionStore'
import { renderKeyOf, useRenderStore } from '@/store/renderStore'
import { beginRestore, settleRestore, resetRestoreGateForTests, useDocumentStore } from '@/store/documentStore'
import { deferUserEntry } from '@/store/documentRestore'
import { emptyProject } from '@/types/document'
import type { PanelObject } from '@/types/document'
import { seedExactRender } from '@/test/renderFixtures'
import { useScriptInputStore } from '@/store/scriptInputStore'
import { useScriptArgvStore } from '@/store/scriptArgvStore'
import { useEnvStore } from '@/store/envStore'
import { useUiStore } from '@/store/uiStore'
import { useDiagSendStore } from '@/store/diagSendStore'
import { addRuntimePanelToCanvas, openFastEdit } from '@/store/workspace'
import { refreshProjectNow } from '@/store/liveSync'
import { useAssetStore } from '@/store/assetStore'
import { useRuntimeAssetStore } from '@/store/runtimeAssetStore'
import { visibleBlocks, visiblePrimaryButtons, visibleSentenceCount } from '@/test/visibleBlocks'
import errorReports from '../../../tests/golden/preparation_error_reports.json'
import { ORIGIN_VIEWS } from '@/lib/importOrigin'
import { useDepRepairStore } from '@/store/depRepairStore'
import { useConvertStore, POLL_MS } from '@/store/convertStore'
import { useForeignScriptStore } from '@/store/foreignScriptStore'
import { useAiStore } from '@/store/aiStore'
import { requestAiFix } from '@/lib/api'
import { useAiFixStore } from '@/store/aiFixStore'
import { useOnboardingStore } from '@/store/onboardingStore'
import { adoptConvert, aiCancel, discardConvert, fetchAiSession, fetchConvert, replanConvertDependencies, resolveConvertDependencies, startConvert } from '@/lib/api'
import type { DependencyPreparationOffer } from '@/lib/api'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const mockCreate = vi.mocked(createPreparationSession)
const mockAct = vi.mocked(actOnPreparationSession)
const LOCALES = Object.keys(resources)

const action = (kind: PreparationAction['kind'], over: Partial<PreparationAction['impact']> = {}): PreparationAction => ({
  id: `act-${kind}`,
  kind,
  config_revision: 1,
  impact: {
    executes_user_script: kind === 'run',
    installs_packages: kind === 'prepare_dependencies',
    changes_environment: kind === 'prepare_dependencies',
    writes_to_project: kind === 'run' ? ['tavotto_registry.json'] : [],
    ...over,
  },
})

const fig = (stem: string): CapturedFigureDescriptor => ({
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
})

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
  checks: [
    { id: 'target', status: 'ok' },
    { id: 'environment', status: 'ok' },
  ],
  requirements: [],
  actions: [action('run'), action('recheck')],
  provider: { plan_id: 'prep-plan', attempt_id: null, attempts: 0, dependency: null },
  runtime_input: null,
  captured: [],
  result: null,
  ...over,
})

/**
 * 准备出错的真实报告片段（P05a）：`tests/golden/preparation_error_reports.json` 由后端测试 `test_preparation_error_detail.py`
 * 用真实 `WorkerError` 经准备会话序列化生成（漂了后端用例红），这里只把它套进基础报告——不手写理想 payload。
 */
type ErrorScenario = keyof typeof errorReports
const fromBackend = (name: ErrorScenario): Partial<PreparationReport> => {
  const g = errorReports[name]
  return {
    phase: g.phase as PreparationReport['phase'],
    outcome: g.outcome as PreparationReport['outcome'],
    requirements: g.requirements as unknown as PreparationReport['requirements'],
    actions: g.action_kinds.map((k) => action(k as PreparationAction['kind'])),
    result: g.result as unknown as PreparationReport['result'],
  }
}

/** `dependencies` 检查项带来源事实（`prepsession.origin_facts`）：状态仍是 ok，只有 detail 多了计数与原因码 */
const originChecks = (origins: PreparationOriginFacts): PreparationReport['checks'] => [
  { id: 'target', status: 'ok' },
  { id: 'environment', status: 'ok' },
  { id: 'dependencies', status: 'ok', detail: { origins } },
]

/** 账本里 safe 被拦下的那一条（`execpolicy.AttemptRecord.public`），按需覆盖字段 */
const HIST_BLOCKED = (over: Partial<PreparationAttempt>): PreparationAttempt => ({
  run: 1,
  seq: 1,
  attempt_id: 'prep-1',
  profile: 'safe',
  interpreter: 'aaaaaaaaaaaa',
  launch: 'bbbbbbbbbbbb',
  cause: '',
  changed: [],
  outcome: 'blocked',
  code: 'safe_file_operation_blocked',
  declined: '',
  ...over,
})

/** 面板要面对的每一种状态（报告形状来自后端合同 `prepsession.report`） */
const STATES: Record<string, Partial<PreparationReport>> = {
  ready: {},
  errPlain: fromBackend('plain_exception'),
  errInvalidArgv: fromBackend('invalid_argv'),
  errMissingArgv: fromBackend('missing_argv'),
  errUnansweredInput: fromBackend('unanswered_input'),
  errMissingInputFile: fromBackend('missing_input_file'),
  dialogFile: {
    requirements: [
      {
        id: 'interaction',
        kind: 'gui_dialog',
        code: 'script_uses_gui_dialog',
        blocking: false,
        payload: { calls: [{ api: 'tkinter.filedialog.askopenfilename', kind: 'file', line: 7 }], truncated: false },
      },
    ],
  },
  dialogAsk: {
    requirements: [
      {
        id: 'interaction',
        kind: 'gui_dialog',
        code: 'script_uses_gui_dialog',
        blocking: false,
        payload: { calls: [{ api: 'tkinter.simpledialog.askstring', kind: 'prompt', line: 3 }], truncated: false },
      },
    ],
  },
  readyArgs: { actions: [action('run', { script_arguments: 3 }), action('recheck')] },
  running: { phase: 'running', outcome: { kind: 'running' }, actions: [action('cancel')] },
  stopping: { phase: 'running', outcome: { kind: 'running', reason: 'cancel_requested' }, actions: [] },
  input: {
    phase: 'awaiting_runtime_input',
    outcome: { kind: 'running' },
    actions: [action('cancel')],
    runtime_input: { id: 'req-1', index: 1, input_kind: 'input', secret: false },
  },
  preparing: { phase: 'preparing_environment', outcome: { kind: 'running' }, actions: [action('cancel')] },
  pythonChange: {
    phase: 'preparing_environment',
    outcome: { kind: 'running', reason: 'python_change' },
    actions: [action('cancel')],
    python_change: { from: '3.14', to: '3.12', base: 'system', download_bytes: 0, state: 'running', stage: 'creating', done: 0, total: 0, code: '' },
  },
  pythonChangeDownload: {
    phase: 'preparing_environment',
    outcome: { kind: 'running', reason: 'python_change' },
    actions: [action('cancel')],
    python_change: { from: '3.9', to: '3.13', base: 'private', download_bytes: 25304407, state: 'running', stage: 'downloading', done: 5242880, total: 25304407, code: '' },
  },
  env: {
    phase: 'awaiting_confirmation',
    requirements: [
      {
        id: 'environment',
        kind: 'environment_choice',
        code: 'environment_choice_required',
        payload: {
          version: 1,
          decision: { consent: 'none', locked_by: null, needs_decision: true, current_id: null },
          recommended_id: 'env-1',
          candidates: [
            {
              id: 'env-1', label: 'project_env' as never, name: '.venv', sources: ['project_venv'], scope: 'project',
              python_relative: '.venv/bin/python', generation: 'g1', status: 'unchecked' as never, checked: false,
              health: null, current: false,
            },
          ],
          python_requirement: { supported: { min: '3.9', max_exclusive: '3.15' }, declared: null, status: 'unknown' },
          check: { executes_candidates: true, max_candidates: 4, deadline_s: 30, per_candidate_timeout_s: 10, scopes: ['project'] },
        },
      },
    ],
    actions: [action('recheck')],
  },
  workdir: {
    phase: 'awaiting_configuration',
    requirements: [
      {
        id: 'workdir',
        kind: 'workdir_choice',
        code: 'workdir_confirmation_required',
        payload: {
          kind: 'workdir',
          code: 'workdir_confirmation_required',
          script: 'plot.py',
          reason: 'project_root_evidence',
          recommended: 'project_root',
          options: [
            { mode: 'project_root', cwd_origin: 'project_root', write_mode: 'real', found: ['data/values.csv'], recommended: true },
            { mode: 'sandbox', cwd_origin: 'sandbox', write_mode: 'sandbox', found: [], recommended: false },
          ],
          conflicts: [],
          reads: ['data/values.csv'],
        } as never,
      },
    ],
    actions: [action('recheck')],
  },
  deps: {
    phase: 'awaiting_confirmation',
    requirements: [{ id: 'dependencies', kind: 'dependency_authorization', code: 'dependency_preparation_required', payload: {} }],
    actions: [action('prepare_dependencies', { installs: ['numpy', 'pandas'], impact_digest: 'imp-1' }), action('recheck')],
  },
  depsRuntime: {
    phase: 'awaiting_confirmation',
    outcome: { kind: 'needs_dependencies', code: 'missing_dependency', reason: 'rerun_required' },
    requirements: [
      { id: 'dependencies', kind: 'dependency_authorization', origin: 'runtime_missing', code: 'missing_dependency', payload: { module: 'scipy', installable: true } },
    ],
    actions: [action('prepare_dependencies', { installs: ['scipy'], impact_digest: 'imp-2' }), action('recheck')],
  },
  depsUnknown: {
    phase: 'awaiting_confirmation',
    outcome: { kind: 'needs_dependencies', code: 'missing_dependency' },
    requirements: [
      { id: 'dependencies', kind: 'dependency_authorization', origin: 'runtime_missing', code: 'missing_dependency', payload: { module: 'mylab', installable: false } },
    ],
    actions: [action('recheck')],
  },
  depsUnresolvable: {
    phase: 'awaiting_confirmation',
    outcome: { kind: 'needs_dependencies', code: 'missing_dependency' },
    requirements: [
      { id: 'dependencies', kind: 'dependency_authorization', origin: 'runtime_missing', code: 'missing_dependency', payload: { module: 'ROOT', installable: false, route: 'unresolvable' } },
    ],
    actions: [action('recheck')],
  },
  depsStdlib: {
    phase: 'awaiting_confirmation',
    outcome: { kind: 'needs_dependencies', code: 'missing_dependency' },
    environment: { mode: 'detect', kind: 'builtin', decided_by: 'default', switched: false, replaced: null },
    requirements: [
      { id: 'dependencies', kind: 'dependency_authorization', origin: 'runtime_missing', code: 'missing_dependency', payload: { module: 'tkinter', installable: false, route: 'stdlib_missing' } },
    ],
    actions: [action('recheck')],
  },
  depsPlatform: {
    phase: 'awaiting_confirmation',
    outcome: { kind: 'needs_dependencies', code: 'missing_dependency' },
    requirements: [
      { id: 'dependencies', kind: 'dependency_authorization', origin: 'runtime_missing', code: 'missing_dependency', payload: { module: 'winreg', installable: false, route: 'unsupported_platform', options: [] } },
    ],
    actions: [action('recheck')],
  },
  // Import Origin（PR6）：计划里有 import 没有可信的安装名——检查项不变状态，只在 detail 里带计数与闭集原因码
  originConfirm: {
    checks: originChecks({ count: 2, by_reason: { distribution_unresolved: 1, module_origin_ambiguous: 1 }, confirmable: 1 }),
  },
  originUnknown: {
    checks: originChecks({ count: 3, by_reason: { editable_dependency_not_reproducible: 1, module_origin_ambiguous: 2 } }),
  },
  needsArgs: {
    phase: 'action_required',
    outcome: { kind: 'failed', code: 'script_needs_arguments' },
    actions: [action('run'), action('recheck')],
    provider: { plan_id: 'p', attempt_id: 'prep-1', attempts: 1, dependency: null },
  },
  missingData: {
    phase: 'action_required',
    outcome: { kind: 'failed', code: 'missing_input' },
    requirements: [
      { id: 'data', kind: 'input_location', code: 'missing_input', origin: 'last_attempt', blocking: false, payload: { script: 'plot.py' } as never },
    ],
    actions: [action('run'), action('recheck')],
  },
  failed: {
    phase: 'action_required',
    outcome: { kind: 'failed', code: 'script_error' },
    actions: [action('run'), action('recheck')],
    provider: { plan_id: 'p', attempt_id: 'prep-1', attempts: 1, dependency: null },
    result: { status: 'error', error: { code: 'script_error', message: 'boom' } },
  },
  // P01a：safe 守卫拦下了脚本的改名/删除/写入、脚本随后又读了它——失败，不是「再试一次」能解决的
  fileOpsBlocked: {
    phase: 'action_required',
    outcome: { kind: 'failed', code: 'safe_file_operation_blocked' },
    actions: [action('run'), action('recheck')],
    provider: { plan_id: 'p', attempt_id: 'prep-1', attempts: 1, dependency: null },
    result: { status: 'error', error: { code: 'safe_file_operation_blocked', message: 'blocked' } },
  },
  // V2 PR4：后端后加的「打开并运行」动作取代主按钮；没有它（老后端 / 杀开关）就是上面原来的 run / 安装
  readyArm: { actions: [action('run'), action('run_when_ready', { impact_digest: 'grant-1' }), action('recheck')] },
  depsArm: {
    phase: 'awaiting_confirmation',
    requirements: [{ id: 'dependencies', kind: 'dependency_authorization', code: 'dependency_preparation_required', payload: {} }],
    actions: [
      action('prepare_dependencies', { installs: ['numpy'], impact_digest: 'imp-1' }),
      action('run_when_ready', { impact_digest: 'grant-1' }),
      action('recheck'),
    ],
  },
  // 要改用户自己环境的安装不在「打开并运行」的授权合同里：仍是逐次确认的「安装」
  depsArmUserEnv: {
    phase: 'awaiting_confirmation',
    requirements: [{ id: 'dependencies', kind: 'dependency_authorization', code: 'dependency_preparation_required', payload: {} }],
    actions: [
      action('prepare_dependencies', { installs: ['numpy'], impact_digest: 'imp-1', modifies_user_environment: true } as never),
      action('run_when_ready', { impact_digest: 'grant-1' }),
      action('recheck'),
    ],
  },
  fileOpsBlockedSafeOnly: {
    phase: 'action_required',
    outcome: { kind: 'failed', code: 'safe_file_operation_blocked' },
    actions: [action('run'), action('recheck')],
    provider: { plan_id: 'p', attempt_id: 'prep-1', attempts: 1, dependency: null },
    result: { status: 'error', error: { code: 'safe_file_operation_blocked', message: 'blocked' } },
    attempt_history: [HIST_BLOCKED({ declined: 'policy_safe_only' })],
  },
  fileOpsBlockedOff: {
    phase: 'action_required',
    outcome: { kind: 'failed', code: 'safe_file_operation_blocked' },
    actions: [action('run'), action('recheck')],
    provider: { plan_id: 'p', attempt_id: 'prep-1', attempts: 1, dependency: null },
    result: { status: 'error', error: { code: 'safe_file_operation_blocked', message: 'blocked' } },
    attempt_history: [HIST_BLOCKED({ declined: 'fallback_disabled' })],
  },
  // 换成原样运行之后又出错了：一句话 + 一个主按钮（再试一次），历史在详情里
  failedAfterFallback: {
    phase: 'action_required',
    outcome: { kind: 'failed', code: 'script_error' },
    actions: [action('run'), action('recheck')],
    provider: { plan_id: 'p', attempt_id: 'prep-2', attempts: 2, dependency: null },
    result: { status: 'error', error: { code: 'script_error', message: 'boom' } },
    attempt_history: [
      HIST_BLOCKED({}),
      HIST_BLOCKED({ seq: 2, attempt_id: 'prep-2', profile: 'native', cause: 'safe_file_operation_blocked', changed: ['profile'], outcome: 'failed', code: 'script_error' }),
    ],
  },
  stale: { phase: 'action_required', outcome: { kind: 'stale', code: 'preparation_plan_stale' }, actions: [action('recheck')] },
  expired: { phase: 'action_required', outcome: { kind: 'unknown', code: 'attempt_expired' }, actions: [action('recheck')] },
  noFigure: {
    phase: 'partial',
    outcome: { kind: 'execution_finished_no_figure' },
    facts: { execution_finished: true, figure_captured: false },
    actions: [action('run'), action('recheck')],
  },
  completed: {
    phase: 'completed',
    outcome: { kind: 'succeeded' },
    facts: { execution_finished: true, figure_captured: true },
    captured: [fig('a')],
    actions: [action('run'), action('recheck')],
  },
  // safe 被拦、自动回退 native 后成功（ADR 0127）：默认仍是同一句话；回退只在详情里说
  completedFallback: {
    phase: 'completed',
    outcome: { kind: 'succeeded' },
    facts: { execution_finished: true, figure_captured: true },
    captured: [{ ...fig('a'), execution_profile: 'native' }],
    attempt_history: [
      { run: 1, seq: 1, attempt_id: 'a1', profile: 'safe', interpreter: 'i', launch: 'l', cause: '', changed: [], outcome: 'blocked', code: 'safe_file_operation_blocked', declined: '' },
      { run: 1, seq: 2, attempt_id: 'a2', profile: 'native', interpreter: 'i', launch: 'l', cause: 'safe_file_operation_blocked', changed: ['profile'], outcome: 'succeeded', code: '', declined: '' },
    ],
    actions: [action('run'), action('recheck')],
  },
  completedMany: {
    phase: 'completed',
    outcome: { kind: 'succeeded' },
    facts: { execution_finished: true, figure_captured: true },
    captured: [fig('a'), fig('b'), fig('c')],
    actions: [action('run'), action('recheck')],
  },
  completedUnlinked: {
    phase: 'completed',
    outcome: { kind: 'succeeded' },
    facts: { execution_finished: true, figure_captured: true },
    captured: [fig('a')],
    unlinked_stems: ['a_scaled', 'b_scaled'],
    actions: [action('run'), action('recheck')],
  },
  completedPartial: {
    phase: 'partial',
    outcome: { kind: 'succeeded' },
    facts: { execution_finished: true, figure_captured: true },
    captured: [fig('good')],
    failed_figures: [{ stem: 'bad', code: 'preview_capture_failed' }],
    actions: [action('run'), action('recheck')],
  },
  cancelled: { phase: 'cancelled', outcome: { kind: 'cancelled' }, actions: [action('run'), action('recheck')] },
  // 智能修复（ADR 0148）：remedy 决定主按钮；「✦ 使用你的 AI 来修复」次按钮每张失败卡都有，不算主按钮也不算一句话
  errRemedyAi: { ...fromBackend('plain_exception'), remedy: { kind: 'ai_fix' } },
  errRemedyConnect: { ...fromBackend('plain_exception'), remedy: { kind: 'connect_ai' } },
  errRemedyFeedback: { ...fromBackend('plain_exception'), remedy: { kind: 'feedback', tried: ['switch_native'] } },
}

let host: HTMLElement
let root: Root

async function mount(withDialog = false) {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => {
    root.render(
      <TooltipProvider>
        <PreparationCard />
        {withDialog && <ScriptInputDialog />}
      </TooltipProvider>,
    )
  })
}

async function openWith(r: PreparationReport) {
  mockCreate.mockResolvedValueOnce(r)
  await act(async () => {
    await useProjectPreparationStore.getState().open({ script: 'plot.py' })
  })
}

const panel = () => host.querySelector('[data-prep-card]') as HTMLElement
const primary = () => panel()?.querySelector('[data-prep-primary]') as HTMLButtonElement | null
const secondary = () => panel()?.querySelector('[data-prep-alt]') as HTMLButtonElement | null

const panelFor = (fileId: string): PanelObject =>
  ({
    id: `panel-${fileId}`, type: 'panel', x: 0, y: 0, w: 100, h: 80, fileId, fileKind: 'png',
    nativeW: 100, nativeH: 80, script: 'plot.py', overrides: [],
  }) as unknown as PanelObject
function putPanel(fileId: string): PanelObject {
  const p = panelFor(fileId)
  useDocumentStore.setState((st) => ({
    doc: { ...st.doc, objects: [...st.doc.objects.filter((o) => o.id !== p.id), p] },
  }))
  return p
}

beforeEach(() => {
  mockCreate.mockReset()
  mockAct.mockReset()
  vi.mocked(addRuntimePanelToCanvas).mockReset()
  vi.mocked(openFastEdit).mockClear()
  vi.mocked(refreshProjectNow).mockClear()
  vi.mocked(fetchPanels).mockReset()
  vi.mocked(refreshProject).mockReset()
  useRuntimeAssetStore.setState({ assets: [], loadAssets: async () => {} })
  vi.mocked(probeScript).mockReset()
  setCurrentProjectId('pj-a')
  useProjectPreparationStore.getState().clear()
  useScriptInputStore.setState({ queue: [], presenters: [], busy: false, error: null })
  useRenderStore.setState({ byKey: {} })
  useDocumentStore.setState((st) => ({ doc: { ...st.doc, objects: [] } }))
  // 入口动作做完之后，画布上有这张图的面板（真实实现由 `addRuntimePanelToCanvas` / `openFastEdit` 完成）
  vi.mocked(openFastEdit).mockImplementation((id: string) => {
    putPanel(id)
    return 'editing'
  })
  vi.mocked(addRuntimePanelToCanvas).mockImplementation(((d: CapturedFigureDescriptor) => putPanel(d.asset_id)) as never)
  useScriptArgvStore.getState().clear()
  useUiStore.setState({ guideCard: 'closed' })
})

afterEach(async () => {
  await act(async () => root?.unmount())
  host?.remove()
  useProjectPreparationStore.getState().clear()
  await i18n.changeLanguage('zh-CN')
})

describe('一句话 + 至多一个主按钮（每一种状态 × 每一种语种）', () => {
  for (const lng of LOCALES) {
    for (const [name, over] of Object.entries(STATES)) {
      it(`${lng} · ${name}`, async () => {
        await i18n.changeLanguage(lng)
        await mount()
        await openWith(report(over))
        expect(panel()).not.toBeNull()
        expect(visibleSentenceCount(panel(), lng)).toBeLessThanOrEqual(1)
        expect(visiblePrimaryButtons(panel())).toBeLessThanOrEqual(1)
        // 详情默认不展开
        expect(panel().querySelector('[data-prep-details]')).toBeNull()
        // 任何卡都不说「可以运行」，除非真的可以运行（主按钮就是 run）
        if (panel().querySelector('[data-prep-line]')!.textContent === (lng === 'zh-CN' ? '可以运行了' : 'Ready to run')) {
          expect(['run', 'run_when_ready']).toContain(primary()?.dataset.prepPrimary)
        }
      })
    }
  }
})

const toggleDetails = () =>
  act(async () => (panel().querySelector('[data-prep-details-toggle]') as HTMLButtonElement).click())
const details = () => panel().querySelector('[data-prep-details]') as HTMLElement | null

describe('safe → native 回退成功：默认句子不变，详情里补一行', () => {
  const note = () => details()?.querySelector('[data-prep-native-fallback]') ?? null
  it('回退成功：默认没有这一句；展开详情才有，并说清改动不会写回', async () => {
    await mount()
    useUiStore.setState({ guideCard: 'card' })
    await openWith(report(STATES.completedFallback))
    const line = panel().querySelector('[data-prep-line]')!.textContent
    expect(panel().textContent).not.toContain('真实文件')
    await toggleDetails()
    expect(details()!.textContent).toContain('已自动换成按真实文件运行')
    expect(details()!.textContent).toContain('不会写回脚本')
    expect(panel().querySelector('[data-prep-line]')!.textContent).toBe(line) // 默认句子没动
    expect(note()).not.toBeNull()
  })

  it('部分捕获（partial + succeeded）也是回退成功：详情里同样补这一行（#1134 Codex r4310249719）', async () => {
    await mount()
    useUiStore.setState({ guideCard: 'card' })
    await openWith(
      report({ ...STATES.completedFallback, phase: 'partial', failed_figures: [{ stem: 'bad', code: 'preview_capture_failed' }] }),
    )
    await toggleDetails()
    expect(note()).not.toBeNull()
    expect(details()!.textContent).toContain('已自动换成按真实文件运行')
  })

  it('描述符说能写回就不说「不会写回」（事实读后端，不在前端推）', async () => {
    await mount()
    useUiStore.setState({ guideCard: 'card' })
    await openWith(report({ ...STATES.completedFallback, captured: [{ ...fig('a'), execution_profile: 'native', can_writeback_source: true }] }))
    await toggleDetails()
    expect(details()!.textContent).toContain('已自动换成按真实文件运行')
    expect(details()!.textContent).not.toContain('不会写回脚本')
  })

  it.each([
    ['没有回退（一直是 safe）', { ...STATES.completed }],
    ['老后端没有账本', { ...STATES.completedFallback, attempt_history: undefined }],
    ['用户一开始就选 native（没有换语义）', { ...STATES.completedFallback, attempt_history: [{ ...STATES.completedFallback.attempt_history![1], changed: [] }] }],
    ['回退了但这次的图不是 native', { ...STATES.completedFallback, captured: [fig('a')] }],
  ])('%s：详情里没有这一行', async (_n, over) => {
    await mount()
    useUiStore.setState({ guideCard: 'card' })
    await openWith(report(over as Partial<PreparationReport>))
    await toggleDetails()
    expect(details()!.textContent).not.toContain('真实文件')
  })
})

describe('进度条：唯一实现是 ui/ProgressBar，断线时不画假活动', () => {
  const bar = () => panel().querySelector('[data-prep-progress]') as HTMLElement | null
  it('运行中：不定态进度条是 ui/ProgressBar（role=progressbar、不报 valuenow）', async () => {
    await mount()
    await openWith(report(STATES.running))
    expect(bar()).not.toBeNull()
    expect(bar()!.getAttribute('role')).toBe('progressbar')
    expect(bar()!.hasAttribute('aria-valuenow')).toBe(false)
    expect(bar()!.dataset.progressIndeterminate).toBeDefined()
  })

  it('断线 / 引擎已停：只有那一句，没有在扫的条', async () => {
    await mount()
    await openWith(report(STATES.running))
    expect(bar()).not.toBeNull() // 对照：同一张卡连着时有条
    const key = useProjectPreparationStore.getState().focus!
    await act(async () => {
      useProjectPreparationStore.setState((st) => ({
        entries: { ...st.entries, [key]: { ...st.entries[key], connection: 'lost' } },
      }))
    })
    expect(panel().querySelector('[data-prep-line]')).not.toBeNull()
    expect(bar()).toBeNull()
  })
})

describe('safe 守卫拦下文件操作之后的失败（P01a）', () => {
  it('说清是失败、没有出图，且没有「再试一次」「运行」这类会得到同一结果的主按钮', async () => {
    await mount()
    await openWith(report(STATES.fileOpsBlocked))
    const line = panel().querySelector('[data-prep-line]')!.textContent ?? ''
    expect(line).toContain('没有出图')
    expect(line).not.toBe('可以运行了')
    expect(primary()?.dataset.prepPrimary).not.toBe('run')
    expect(primary()?.textContent).not.toBe('再试一次')
  })

  it('英文界面同样是一句话，且没有泄漏中文', async () => {
    await i18n.changeLanguage('en-US')
    await mount()
    await openWith(report(STATES.fileOpsBlocked))
    const line = panel().querySelector('[data-prep-line]')!.textContent ?? ''
    expect(line).toContain('no figure was made')
    expect(line).not.toMatch(/[一-鿿]/)
  })
})

describe('主按钮就是后端给的那件事', () => {
  it('ready：运行 = 认领报告里的 run 动作；旁边是「稍后」', async () => {
    await mount()
    await openWith(report())
    expect(primary()?.dataset.prepPrimary).toBe('run')
    expect(primary()?.textContent).toBe('运行')
    expect(panel().querySelector('[data-prep-later]')).not.toBeNull()
    mockAct.mockResolvedValueOnce({ claimed: true, report: report({ phase: 'running', observation_seq: 2, actions: [action('cancel')] }) })
    await act(async () => primary()!.click())
    expect(mockAct).toHaveBeenCalledWith(
      'psess-1',
      { action_id: 'act-run', expected_config_revision: 1 },
      'pj-a',
      expect.any(AbortSignal),
    )
  })

  describe('脚本会弹窗（后端静态识别，不阻塞）', () => {
    const dialogReport = (kind: 'file' | 'prompt', over: Partial<PreparationReport> = {}) =>
      report({
        requirements: [
          {
            id: 'interaction',
            kind: 'gui_dialog',
            code: 'script_uses_gui_dialog',
            blocking: false,
            payload: { calls: [{ api: 'tkinter.filedialog.askopenfilename', kind, line: 7 }], truncated: false },
          },
        ],
        ...over,
      })

    it('一句话说清 + 主按钮是「仍然运行」（认领报告里的 run）；怎么改与位置在默认收起的详情里', async () => {
      await mount()
      await openWith(dialogReport('file'))
      expect(panel().dataset.prepState).toBe('gui_dialog')
      expect(panel().querySelector('[data-prep-line]')?.textContent).toBe('脚本会弹窗选文件，这里弹不出来，请把文件路径写进脚本')
      expect(primary()?.dataset.prepPrimary).toBe('run')
      expect(primary()?.textContent).toBe('仍然运行')
      expect(panel().querySelector('[data-prep-dialog]')).toBeNull()
      await toggleDetails()
      expect(details()?.querySelector('[data-prep-dialog-how]')?.textContent).toContain('相对路径')
      expect(details()?.querySelector('[data-prep-row="dialog-call"]')?.textContent).toContain('第 7 行')
      expect(details()?.querySelector('[data-prep-row="dialog-call"]')?.textContent).toContain('tkinter.filedialog.askopenfilename')
      mockAct.mockResolvedValueOnce({ claimed: true, report: report({ phase: 'running', observation_seq: 2, actions: [action('cancel')] }) })
      await act(async () => primary()!.click())
      expect(mockAct).toHaveBeenCalledWith('psess-1', { action_id: 'act-run', expected_config_revision: 1 }, 'pj-a', expect.anything())
    })

    it('英文同样；询问类弹窗换一句话', async () => {
      await i18n.changeLanguage('en-US')
      await mount()
      await openWith(dialogReport('prompt'))
      expect(panel().querySelector('[data-prep-line]')?.textContent).toBe("This script opens a prompt that can't appear here, so put the answer in the script")
      expect(primary()?.textContent).toBe('Run anyway')
    })

    const manyReport = (n: number, truncated: boolean, kinds: Array<'file' | 'prompt'> = ['file']) =>
      report({
        requirements: [
          {
            id: 'interaction',
            kind: 'gui_dialog',
            code: 'script_uses_gui_dialog',
            blocking: false,
            payload: {
              calls: Array.from({ length: n }, (_, i) => ({
                api: 'tkinter.filedialog.askopenfilename',
                kind: kinds[i % kinds.length],
                line: i + 1,
              })),
              truncated,
            },
          },
        ],
      })

    it('4–8 处弹窗：详情里全部列出，不再只给前 3 处', async () => {
      await mount()
      await openWith(manyReport(8, false))
      await toggleDetails()
      expect(details()?.querySelectorAll('[data-prep-row="dialog-call"]').length).toBe(8)
      expect(details()?.querySelector('[data-prep-dialog-more]')).toBeNull()
    })

    it('后端说还有更多（truncated）：详情里明确写出，中英都有', async () => {
      await mount()
      await openWith(manyReport(8, true))
      await toggleDetails()
      expect(details()?.querySelector('[data-prep-dialog-more]')?.textContent).toContain('还有更多处未列出')
    })

    it('truncated 英文提示', async () => {
      await i18n.changeLanguage('en-US')
      await mount()
      await openWith(manyReport(8, true))
      await toggleDetails()
      expect(details()?.querySelector('[data-prep-dialog-more]')?.textContent).toContain('More places are not listed')
    })

    it('选文件与询问都有：标题一句话涵盖两者，详情里两种改法各一条', async () => {
      await mount()
      await openWith(manyReport(2, false, ['file', 'prompt']))
      expect(panel().dataset.prepState).toBe('gui_dialog')
      expect(panel().querySelector('[data-prep-line]')?.textContent).toBe('脚本会弹窗选文件和询问，这里弹不出来，请把路径和答案写进脚本')
      expect(primary()?.textContent).toBe('仍然运行')
      await toggleDetails()
      expect(details()?.querySelector('[data-prep-dialog-how="file"]')?.textContent).toContain('相对路径')
      expect(details()?.querySelector('[data-prep-dialog-how="prompt"]')?.textContent).toContain('别用弹窗问')
    })

    it('样本里只有选文件、但 kinds 说还有询问：标题与改法仍按两种来（读 kinds，不只看 calls）', async () => {
      await mount()
      const r = manyReport(8, true)
      const req = r.requirements.find((q) => q.kind === 'gui_dialog')
      if (req?.kind === 'gui_dialog') req.payload.kinds = ['file', 'prompt']
      await openWith(r)
      expect(panel().querySelector('[data-prep-line]')?.textContent).toBe('脚本会弹窗选文件和询问，这里弹不出来，请把路径和答案写进脚本')
      await toggleDetails()
      expect(details()?.querySelector('[data-prep-dialog-how="file"]')).not.toBeNull()
      expect(details()?.querySelector('[data-prep-dialog-how="prompt"]')).not.toBeNull()
    })

    it('没有 run 动作就没有按钮（不替用户造入口）', async () => {
      await mount()
      await openWith(dialogReport('file', { actions: [action('recheck')] }))
      expect(panel().dataset.prepState).toBe('gui_dialog')
      expect(primary()).toBeNull()
    })

    it('不带弹窗的报告仍是「可以运行了」', async () => {
      await mount()
      await openWith(report())
      expect(panel().dataset.prepState).toBe('ready')
      expect(panel().querySelector('[data-prep-line]')?.textContent).toBe('可以运行了')
    })
  })

  it('报告里没有 run 动作：就没有「运行」', async () => {
    await mount()
    await openWith(report({ actions: [action('recheck')] }))
    expect(primary()).toBeNull()
  })

  const envFact = (over: object): PreparationReport['environment'] => ({
    mode: 'detect',
    kind: 'builtin',
    decided_by: 'default',
    switched: false,
    replaced: null,
    ...over,
  })
  const SWITCH_COPY: Record<string, [string, string]> = {
    'zh-CN': ['Tavotto 自带的', '（原来那套不能用了，已自动换好）'],
    'en-US': ["Tavotto's own", '(the previous one stopped working and was replaced)'],
  }
  for (const lng of Object.keys(SWITCH_COPY)) {
    it(`${lng} · 记住的环境没了、回退到默认环境（switched + replaced）：详情里说换过；没换过就不说`, async () => {
      const [label, hint] = SWITCH_COPY[lng]
      await i18n.changeLanguage(lng)
      await mount()
      await openWith(report({ environment: envFact({ switched: true, replaced: { reason: 'missing' } }) }))
      await toggleDetails()
      const row = () => details()?.querySelector('[data-prep-row="environment"]')?.textContent ?? ''
      expect(row()).toContain(label)
      expect(row()).toContain(hint)
      await openWith(report({ environment: envFact({}), observation_seq: 2 }))
      expect(row()).toContain(label)
      expect(row()).not.toContain(hint)
    })
  }

  // 落选原因（ADR 0079 修订）：详情里每个没被采用的候选一句话 + 一个「选择渲染环境」入口；默认可见区不多一个字
  const NOT_USED_COPY: Record<string, Record<string, [Record<string, unknown>, string]>> = {
    'zh-CN': {
      root_needs_msvc: [{ label: 'Python 3.14 + ROOT 6.38', root_version: '6.38' }, 'ROOT 6.38 在 Windows 上要装 Visual Studio 的 C++ 工具才能运行'],
      root_python_mismatch: [{ label: 'ROOT 6.38', root_version: '6.38' }, '这份 ROOT 6.38 是为另一个 Python 版本编译的，和这个环境的 Python 版本不一致'],
      missing_imports: [{ label: 'Python 3.12', missing: ['ROOT', 'uproot'] }, 'Python 3.12 里没有这份脚本要用的 ROOT 和 uproot'],
    },
    'en-US': {
      root_needs_msvc: [{ label: 'Python 3.14 + ROOT 6.38', root_version: '6.38' }, 'ROOT 6.38 needs the Visual Studio C++ tools to run on Windows'],
      root_python_mismatch: [{ label: 'ROOT 6.38', root_version: '6.38' }, 'This ROOT 6.38 was built for a different Python version than this environment'],
      missing_imports: [{ label: 'Python 3.12', missing: ['ROOT', 'uproot'] }, 'Python 3.12 is missing ROOT and uproot, which this script needs'],
    },
  }
  const MISMATCH_VERSIONS: Record<string, string> = {
    'zh-CN': '这份 ROOT 6.38 是为 Python 3.14 编译的，这个环境是 Python 3.12',
    'en-US': 'This ROOT 6.38 was built for Python 3.14, but this environment uses Python 3.12',
  }
  for (const [lng, sentence] of Object.entries(MISMATCH_VERSIONS)) {
    it(`${lng} · 版本不匹配且两边版本都给出：点名两个 Python 版本`, async () => {
      await i18n.changeLanguage(lng)
      await mount()
      const notUsed = { source: 'system', reason: 'root_python_mismatch', label: 'ROOT 6.38', root_version: '6.38', root_python: '3.14', env_python: '3.12' }
      await openWith(report({ environment: envFact({ not_used: [notUsed] }) }))
      await toggleDetails()
      const row = details()?.querySelector('[data-prep-env-not-used-reason="root_python_mismatch"]')
      expect(row?.textContent).toContain(sentence)
    })
  }
  for (const lng of Object.keys(NOT_USED_COPY)) {
    for (const [reason, [extra, sentence]] of Object.entries(NOT_USED_COPY[lng])) {
      it(`${lng} · 没被采用的环境（${reason}）：详情里说一句为什么 + 「选择渲染环境」入口；默认区不多一句`, async () => {
        await i18n.changeLanguage(lng)
        await mount()
        const withNotUsed = envFact({ not_used: [{ source: 'system', reason, ...extra }] })
        await openWith(report({ environment: withNotUsed }))
        const lineBefore = panel().querySelector('[data-prep-line]')?.textContent
        expect(panel().querySelector('[data-prep-env-not-used]')).toBeNull() // 收起时不可见
        await toggleDetails()
        const row = details()?.querySelector(`[data-prep-env-not-used-reason="${reason}"]`)
        expect(row?.textContent).toContain(sentence)
        const open = details()?.querySelector<HTMLButtonElement>('[data-prep-open-environment]')
        expect(open).not.toBeNull()
        await act(async () => open!.click())
        expect(useUiStore.getState().engineEnvOpen).toBe(true)
        useUiStore.getState().setEngineEnvOpen(false)
        expect(panel().querySelector('[data-prep-line]')?.textContent).toBe(lineBefore)
      })
    }
  }

  it('落选原因里「能跑只是排后面」「运行前没量」不说；没有落选原因就没有这一块', async () => {
    await mount()
    await openWith(
      report({
        environment: envFact({
          not_used: [
            { source: 'conda', label: 'Python 3.11', reason: 'outranked' },
            { source: 'pyenv', label: 'Python 3.10', reason: 'not_checked' },
          ],
        }),
      }),
    )
    await toggleDetails()
    expect(details()?.querySelector('[data-prep-env-not-used]')).toBeNull()
  })

  it('已采用带 ROOT 叠加的环境：「用的是」一行只认报告里冻结的叠加，envStore 里别的叠加不串进来', async () => {
    await mount()
    useEnvStore.setState({
      env: { project: { open: true, env_overlay: { kind: 'root', version: '6.30.00', label: 'ROOT 6.30' } } },
    } as never)
    const row = () => details()?.querySelector('[data-prep-row="environment"]')?.textContent ?? ''
    await openWith(
      report({ environment: envFact({ kind: 'user', overlay: { kind: 'root', version: '6.38.02', label: 'ROOT 6.38' } }) }),
    )
    await toggleDetails()
    expect(row()).toContain('ROOT 6.38')
    expect(row()).not.toContain('ROOT 6.30')
    // 报告里没有叠加：即使项目当前记录有，也不说
    await openWith(report({ environment: envFact({ kind: 'user' }), observation_seq: 2 }))
    expect(row()).not.toContain('ROOT')
    useEnvStore.setState({ env: null } as never)
  })

  it('缺组件：主按钮「安装」认领 prepare_dependencies 并回显影响摘要；一行说装什么、装到哪，详情里列全', async () => {
    await mount()
    await openWith(report(STATES.deps))
    expect(panel().querySelector('[data-prep-line]')?.textContent).toBe('要先安装缺少的组件')
    expect(primary()?.dataset.prepPrimary).toBe('prepare_dependencies')
    expect(primary()?.textContent).toBe('安装')
    expect(panel().querySelector('[data-prep-install]')?.textContent).toBe('numpy, pandas · 装到 Tavotto 目录')
    await toggleDetails()
    expect(details()?.querySelector('[data-prep-row="installs"]')?.textContent).toContain('numpy, pandas')
    mockAct.mockResolvedValueOnce({ claimed: true, report: report({ phase: 'preparing_environment', observation_seq: 2 }) })
    await act(async () => primary()!.click())
    expect(mockAct.mock.calls[0][1]).toEqual({
      action_id: 'act-prepare_dependencies',
      expected_config_revision: 1,
      impact_digest: 'imp-1',
    })
  })

  it('运行中：主按钮「放到后台」只缩成角标（不取消）；「停止」是文字按钮、认领 cancel', async () => {
    await mount()
    useUiStore.setState({ guideCard: 'card' })
    await openWith(report(STATES.running))
    expect(primary()?.dataset.prepPrimary).toBe('background')
    expect(primary()?.textContent).toBe('放到后台')
    await act(async () => primary()!.click())
    expect(useUiStore.getState().guideCard).toBe('pill')
    expect(mockAct).not.toHaveBeenCalled()
    expect(host.querySelector('[data-prep-pill]')?.textContent).toContain('运行中')
    await act(async () => (host.querySelector('[data-prep-pill-open]') as HTMLButtonElement).click())
    mockAct.mockResolvedValueOnce({ claimed: true, report: report({ phase: 'cancelled', observation_seq: 2 }) })
    await act(async () => (panel().querySelector('[data-prep-stop]') as HTMLButtonElement).click())
    expect(mockAct.mock.calls[0][1]).toMatchObject({ action_id: 'act-cancel' })
  })
})

describe('缺依赖三类：可安装 / 装不了 / 标准库缺了——各一句话 + 一个真能走通的主按钮', () => {
  it('可安装：主按钮「安装」认领 prepare_dependencies', async () => {
    await mount()
    await openWith(report(STATES.depsRuntime))
    expect(panel().dataset.prepState).toBe('deps_runtime')
    expect(primary()?.dataset.prepPrimary).toBe('prepare_dependencies')
  })

  it('装不了（ROOT）：说「装不了」，主按钮直接打开选 Python 环境（不是只开设置）', async () => {
    await mount()
    await openWith(report(STATES.depsUnresolvable))
    expect(panel().dataset.prepState).toBe('deps_unknown')
    expect(panel().querySelector('[data-prep-line]')?.textContent).toBe('Tavotto 装不了 ROOT，请选一个装了它的 Python 环境')
    expect(primary()?.dataset.prepPrimary).toBe('pick_environment')
    expect(primary()?.textContent).toBe('换用我的 Python')
    await act(async () => primary()!.click())
    expect(useUiStore.getState().engineEnvOpen).toBe(true)
    expect(useUiStore.getState().engineEnvPick).toBe(true)
  })

  it('内置环境缺的标准库（tkinter）：说清自带环境里没有；主按钮是换用自己的 Python', async () => {
    await mount()
    await openWith(report(STATES.depsStdlib))
    expect(panel().dataset.prepState).toBe('deps_stdlib')
    expect(panel().querySelector('[data-prep-line]')?.textContent).toBe('Tavotto 自带的环境里没有 tkinter')
    expect(primary()?.dataset.prepPrimary).toBe('pick_environment')
  })

  it('系统专属标准库不可用：说明脚本不支持当前系统，不再给换 Python 的按钮', async () => {
    await mount()
    await openWith(report(STATES.depsPlatform))
    expect(panel().dataset.prepState).toBe('deps_platform')
    expect(panel().querySelector('[data-prep-line]')?.textContent).toBe('winreg 是其他系统专用模块，这个脚本无法在当前系统运行')
    expect(primary()).toBeNull()
    expect(panel().querySelector('[data-prep-primary="pick_environment"]')).toBeNull()
  })

  it('关上弹窗就清掉「直达选环境」的意图（下次从设置进来不会被预先展开）', () => {
    useUiStore.getState().openEngineEnvPicker()
    useUiStore.getState().setEngineEnvOpen(false)
    expect(useUiStore.getState().engineEnvPick).toBe(false)
  })
})

describe('必填参数没填齐时任何卡都不说「可以运行」', () => {
  const schema = {
    status: 'complete',
    form_enabled: true,
    reasons: [],
    exclusive_groups: [],
    subcommands: null,
    arguments: [
      { id: 'scale', dest: 'scale', flags: ['--scale'], positional: false, required: true, arity: 1, action: 'store', type: 'float', editable: true, hidden: false, help: '缩放', default: null, choices: null, metavar: null, role: null },
      { id: 'label', dest: 'label', flags: ['--label'], positional: false, required: true, arity: 1, action: 'store', type: 'str', editable: true, hidden: false, help: null, default: null, choices: null, metavar: null, role: null },
      { id: 'dpi', dest: 'dpi', flags: ['--dpi'], positional: false, required: false, arity: 1, action: 'store', type: 'int', editable: true, hidden: false, help: null, default: { kind: 'literal', display: '100' }, choices: null, metavar: null, role: null },
    ],
  }
  const withSchema = report({
    requirements: [
      { id: 'arguments', kind: 'script_arguments', code: 'script_arguments_available', blocking: false, payload: { schema: schema as never, argv_count: 0, run_config: null } },
    ],
  })

  it('还差 2 个：参数卡、只摆必填项、主按钮「继续」置灰；填齐了才能按，按了只按新参数重新检查', async () => {
    await mount()
    await openWith(withSchema)
    expect(panel().dataset.prepState).toBe('args')
    expect(panel().querySelector('[data-prep-line]')?.textContent).toBe('还差 2 个参数')
    expect(primary()?.textContent).toBe('继续')
    expect(primary()?.disabled).toBe(true)
    // 只摆必填项；可选的在「其他 1 个参数」折叠里
    const required = panel().querySelector('[data-testid="argv-form-required-plot.py"]')!
    expect(required.querySelector('[data-testid="argv-field-scale"]')).not.toBeNull()
    expect(required.querySelector('[data-testid="argv-field-dpi"]')).toBeNull()
    expect(panel().querySelector('[data-testid="argv-optional-plot.py"] > summary')?.textContent).toBe('其他 1 个参数')
    expect(panel().textContent).not.toContain('可以运行')
    await act(async () => useScriptArgvStore.getState().setTokens('plot.py', ['--scale', '1.5']))
    expect(panel().querySelector('[data-prep-line]')?.textContent).toBe('还差 1 个参数')
    expect(primary()?.disabled).toBe(true)
    await act(async () => useScriptArgvStore.getState().setTokens('plot.py', ['--scale', '1.5', '--label', '峰 值 A']))
    expect(panel().dataset.prepState).toBe('args_changed')
    expect(panel().querySelector('[data-prep-line]')?.textContent).toBe('参数已填好')
    expect(primary()?.disabled).toBe(false)
    mockCreate.mockResolvedValueOnce(report({ ...withSchema, config_revision: 2 }))
    await act(async () => primary()!.click())
    expect(mockCreate).toHaveBeenLastCalledWith(
      { script: 'plot.py', argv: ['--scale', '1.5', '--label', '峰 值 A'], argv_sensitive: false },
      'pj-a',
      expect.anything(),
    )
    expect(mockAct).not.toHaveBeenCalled()
    expect(panel().dataset.prepState).toBe('ready')
  })

  // Codex #1044 r4280135232：沿用上次成功的配置时必填项由那份配置满足，草稿为空不是缺参
  it('后端核验沿用了上次成功的配置（argv_count > 0、草稿空）：不出禁用的参数卡；用户草稿非空仍按草稿判；没沿用且草稿空照旧缺参', async () => {
    const reusedWith = { ...withSchema, target: { kind: 'script' as const, script: 'plot.py', entry: '__main__', asset_id: null, stem: null, argv_count: 2 }, actions: [action('run'), action('recheck')] }
    await mount()
    mockCreate.mockResolvedValueOnce(report(reusedWith as Partial<PreparationReport>))
    await act(async () => {
      await useProjectPreparationStore.getState().open({ script: 'plot.py', reuse_last_success: true })
    })
    expect(panel().dataset.prepState).not.toBe('args')
    expect(primary()?.disabled).toBe(false)
    // 用户在草稿里敲了一半：按草稿判
    await act(async () => useScriptArgvStore.getState().setTokens('plot.py', ['--scale', '1.5']))
    expect(panel().dataset.prepState).toBe('args')
  })

  it('没沿用（报告里 argv_count 为 0）且草稿空：仍是缺参的参数卡', async () => {
    await mount()
    mockCreate.mockResolvedValueOnce(report(withSchema))
    await act(async () => {
      await useProjectPreparationStore.getState().open({ script: 'plot.py', reuse_last_success: true })
    })
    expect(panel().dataset.prepState).toBe('args')
    expect(primary()?.disabled).toBe(true)
  })

  // Codex 安全 #820：项目自带的环境要运行时才体检——首查以「运行」为主，不推去安装
  it('依赖检查项标了 deferred=project_environment：主按钮是运行、句子说明项目环境运行时检查', async () => {
    const rep = report({
      checks: [
        { id: 'target', status: 'ok' },
        { id: 'environment', status: 'ok' },
        { id: 'dependencies', status: 'ok', detail: { deferred: 'project_environment' } },
      ],
    })
    await mount()
    await openWith(rep)
    expect(panel().dataset.prepState).toBe('ready')
    expect(panel().querySelector('[data-prep-line]')?.textContent).toBe('可以运行了，项目自带的环境会在运行时检查')
    expect(primary()?.textContent).toBe('运行')
    expect(primary()?.disabled).toBe(false)
  })

  // r4234219374：`@响应文件` 可以带外提供必填选项——说不清就不拦「运行」
  it('表单关着且有响应文件（fromfile）：必填选项不算缺，不拦运行', async () => {
    const subSchema = {
      ...schema,
      form_enabled: false,
      reasons: ['subcommands', 'fromfile'],
      arguments: [schema.arguments[0]],
      subcommands: { dest: 'cmd', required: true, choices: ['plot', 'stats'], dynamic: false },
    }
    const rep = report({
      requirements: [
        { id: 'arguments', kind: 'script_arguments', code: 'script_arguments_available', blocking: false, payload: { schema: subSchema as never, argv_count: 0, run_config: null } },
      ],
    })
    await mount()
    await openWith(rep)
    await act(async () => useScriptArgvStore.getState().setTokens('plot.py', ['@args.txt']))
    expect(panel().dataset.prepState).not.toBe('args')
    expect(primary()?.disabled).toBe(false)
  })

  // r4234436263：分支里声明的必填选项运行时可能不存在——不拦「运行」
  it('条件式声明的必填选项（表单开着）不拦运行', async () => {
    const condSchema = { ...schema, arguments: [{ ...schema.arguments[0], conditional: true }], exclusive_groups: [], subcommands: null }
    const rep = report({
      requirements: [
        { id: 'arguments', kind: 'script_arguments', code: 'script_arguments_available', blocking: false, payload: { schema: condSchema as never, argv_count: 0, run_config: null } },
      ],
    })
    await mount()
    await openWith(rep)
    expect(panel().dataset.prepState).not.toBe('args')
    expect(primary()?.disabled).toBe(false)
  })

  // r4234766738：辅助函数里的声明（后端标 conditional + conditional_argument）不拦「运行」，表单仍显示
  it('辅助函数里声明的必填选项（unproven -> conditional）不拦运行', async () => {
    const helperSchema = {
      ...schema,
      reasons: ['conditional_argument'],
      arguments: [{ ...schema.arguments[0], required: true, conditional: true }],
      exclusive_groups: [],
      subcommands: null,
    }
    const rep = report({
      requirements: [
        { id: 'arguments', kind: 'script_arguments', code: 'script_arguments_available', blocking: false, payload: { schema: helperSchema as never, argv_count: 0, run_config: null } },
      ],
    })
    await mount()
    await openWith(rep)
    expect(panel().dataset.prepState).not.toBe('args')
    expect(primary()?.disabled).toBe(false)
  })

  // r4232531822：必选互斥组里每个参数自己都 required=false，不能因此放行「运行」
  it('必选互斥组一个都没给：参数卡、主按钮置灰；给了其中一个才放行', async () => {
    const opt = (id: string) => ({ ...schema.arguments[2], id, dest: id, flags: [`--${id}`], default: null, group: 'g0' })
    const groupSchema = {
      ...schema,
      arguments: [opt('csv'), opt('json')],
      exclusive_groups: [{ id: 'g0', required: true, members: ['csv', 'json'] }],
    }
    const rep = report({
      requirements: [
        { id: 'arguments', kind: 'script_arguments', code: 'script_arguments_available', blocking: false, payload: { schema: groupSchema as never, argv_count: 0, run_config: null } },
      ],
    })
    await mount()
    await openWith(rep)
    expect(panel().dataset.prepState).toBe('args')
    expect(primary()?.disabled).toBe(true)
    expect(panel().textContent).not.toContain('可以运行')
    await act(async () => useScriptArgvStore.getState().setTokens('plot.py', ['--csv', 'a.csv']))
    expect(panel().dataset.prepState).not.toBe('args')
    expect(primary()?.disabled).toBe(false)
  })

  it('必选子命令没选（表单关着）：同样不放行；选了才放行', async () => {
    const subSchema = {
      ...schema,
      form_enabled: false,
      reasons: ['subcommands'],
      arguments: [],
      subcommands: { dest: 'cmd', required: true, choices: ['plot', 'stats'], dynamic: false },
    }
    const rep = report({
      requirements: [
        { id: 'arguments', kind: 'script_arguments', code: 'script_arguments_available', blocking: false, payload: { schema: subSchema as never, argv_count: 0, run_config: null } },
      ],
    })
    await mount()
    await openWith(rep)
    expect(panel().dataset.prepState).toBe('args')
    expect(primary()?.disabled).toBe(true)
    await act(async () => useScriptArgvStore.getState().setTokens('plot.py', ['plot', '--x']))
    expect(panel().dataset.prepState).not.toBe('args')
  })

  // r4232790899：表单关着时，读得准的必填选项（--scale）照拦，不能只看子命令
  it('表单关着：必填选项没给也不放行，补齐 + 选了子命令才放行', async () => {
    const subSchema = {
      ...schema,
      form_enabled: false,
      reasons: ['subcommands'],
      arguments: [schema.arguments[0]],
      subcommands: { dest: 'cmd', required: true, choices: ['plot', 'stats'], dynamic: false },
    }
    const rep = report({
      requirements: [
        { id: 'arguments', kind: 'script_arguments', code: 'script_arguments_available', blocking: false, payload: { schema: subSchema as never, argv_count: 0, run_config: null } },
      ],
    })
    await mount()
    await openWith(rep)
    await act(async () => useScriptArgvStore.getState().setTokens('plot.py', ['plot']))
    expect(panel().dataset.prepState).toBe('args')
    expect(primary()?.disabled).toBe(true)
    await act(async () => useScriptArgvStore.getState().setTokens('plot.py', ['--scale', '2', 'plot']))
    expect(panel().dataset.prepState).not.toBe('args')
  })

  it('来源待定的卡也一样（Import Origin）：必填参数没填齐时换成参数卡，不给「仍然运行」', async () => {
    await mount()
    await openWith({ ...withSchema, checks: originChecks({ count: 1, by_reason: { unverified: 1 } }) })
    expect(panel().dataset.prepState).toBe('args')
    expect(panel().querySelector('[data-prep-line]')?.textContent).toBe('还差 2 个参数')
    expect(primary()?.textContent).toBe('继续')
    expect(primary()?.disabled).toBe(true)
  })
})

describe('运行目录在卡里选', () => {
  const payload = (recommended: 'project_root' | null) => ({
    kind: 'workdir',
    code: 'workdir_confirmation_required',
    script: 'tools/plot.py',
    reason: recommended ? 'project_root_evidence' : 'ambiguous_data',
    recommended,
    options: [
      { mode: 'project_root', cwd_origin: 'project_root', write_mode: 'real', found: ['data/values.csv'], recommended: recommended === 'project_root' },
      { mode: 'project', cwd_origin: 'script_dir', write_mode: 'real', found: [], recommended: false },
      { mode: 'sandbox', cwd_origin: 'sandbox', write_mode: 'sandbox', found: [], recommended: false },
    ],
    conflicts: [],
    reads: ['data/values.csv'],
  })
  const workdirReport = (rec: 'project_root' | null) =>
    report({
      phase: 'awaiting_configuration',
      requirements: [{ id: 'workdir', kind: 'workdir_choice', code: 'workdir_confirmation_required', payload: payload(rec) as never }],
      actions: [action('recheck')],
    })

  it('推荐项预选，「换一个」才展开其余；确认 = 回答目录（同一次 PATCH），不运行、不认领动作', async () => {
    const setWorkdirMode = vi.fn().mockResolvedValue(null)
    useEnvStore.setState({ setWorkdirMode })
    await mount()
    await openWith(workdirReport('project_root'))
    expect(panel().querySelector('[data-prep-line]')?.textContent).toBe('在哪个目录运行？')
    expect(panel().querySelectorAll('[data-workdir-option]').length).toBe(1)
    expect(panel().querySelector('[data-workdir-option="project_root"]')?.textContent).toContain('找得到 data/values.csv')
    expect(primary()?.textContent).toBe('用项目根目录')
    expect(primary()?.disabled).toBe(false)
    await act(async () => (panel().querySelector('[data-workdir-more]') as HTMLButtonElement).click())
    expect(panel().querySelectorAll('[data-workdir-option]').length).toBe(3)
    await act(async () => primary()!.click())
    expect(setWorkdirMode).toHaveBeenCalledWith('project_root', { confirmed: true })
    expect(mockAct).not.toHaveBeenCalled()
    expect(document.querySelector('[data-dialog="workdir-confirm"]')).toBeNull()
  })

  it('两处数据不同（歧义）：不预选，选中之前按钮置灰', async () => {
    useEnvStore.setState({ setWorkdirMode: vi.fn().mockResolvedValue(null) })
    await mount()
    await openWith(workdirReport(null))
    expect(panel().querySelectorAll('[data-workdir-option]').length).toBe(3)
    expect(primary()?.disabled).toBe(true)
    await act(async () => (panel().querySelector('[data-workdir-option="project"] input') as HTMLInputElement).click())
    expect(primary()?.disabled).toBe(false)
    expect(primary()?.textContent).toBe('用脚本所在目录')
  })
})

describe('执行结束、捕获到图、首次编辑渲染是三件事', () => {
  it('跑完没图：「跑完了，没有出图」+「知道了」（收起卡片）；原因在详情', async () => {
    await mount()
    useUiStore.setState({ guideCard: 'card' })
    await openWith(report(STATES.noFigure))
    expect(panel().dataset.prepState).toBe('no_figure')
    expect(panel().querySelector('[data-prep-line]')?.textContent).toBe('跑完了，没有出图')
    expect(primary()?.textContent).toBe('知道了')
    await toggleDetails()
    expect(details()?.querySelector('[data-prep-nofigure-why]')).not.toBeNull()
    await act(async () => primary()!.click())
    expect(useUiStore.getState().guideCard).toBe('closed')
  })

  it('跑完没图、但脚本自己用位图库写了图片：一句话说原因 + 一个主按钮去素材库，其余折叠在详情', async () => {
    await mount()
    useUiStore.setState({ guideCard: 'card', leftTab: 'layers' })
    await openWith(report({ ...STATES.noFigure, no_figure_hint: { kind: 'raster_script', library: 'pillow', in_project: true } }))
    expect(panel().dataset.prepState).toBe('no_figure_raster')
    expect(panel().querySelector('[data-prep-line]')?.textContent).toBe('这张图是用 Pillow 画的，Tavotto 不能直接编辑它。')
    expect(primary()?.textContent).toBe('生成可编辑版本')
    expect(secondary()?.textContent).toBe('打开素材库')
    // 一句话 + 一个主按钮：详情没展开时说明不在页面上
    expect(panel().querySelector('[data-prep-nofigure-raster-why]')).toBeNull()
    await toggleDetails()
    expect(details()?.querySelector('[data-prep-nofigure-raster-why]')?.textContent).toContain('改成用 Matplotlib 画')
    expect(details()?.querySelector('[data-prep-nofigure-raster-why]')?.textContent).not.toContain('图片文件')
    expect(details()?.querySelector('[data-prep-nofigure-why]')).toBeNull()
    await act(async () => secondary()!.click())
    // 点按钮就主动刷新一次素材（刚写出的图不用等 watcher 轮询），再切标签
    expect(vi.mocked(refreshProjectNow)).toHaveBeenCalledTimes(1)
    expect(useUiStore.getState().leftTab).toBe('assets')
    expect(useUiStore.getState().guideCard).toBe('closed')
  })

  it.each([
    ['root', 'ROOT'],
    ['plotly', 'Plotly'],
  ] as const)('位图提示卡认得 %s 的库名，并带着输出文件路径', async (library, name) => {
    await mount()
    useUiStore.setState({ guideCard: 'card', leftTab: 'layers' })
    await openWith(report({ ...STATES.noFigure, no_figure_hint: { kind: 'raster_script', library, in_project: true, path: 'results/mass.pdf' } }))
    expect(panel().dataset.prepState).toBe('no_figure_raster')
    expect(panel().querySelector('[data-prep-line]')?.textContent).toBe(`这个脚本是用 ${name} 直接画成图片的，不是 Matplotlib 图`)
  })

  it('点「打开素材库」：刷新还没结束时 AssetBrowser 读的 loading 已为真，刷新结束后才取清单', async () => {
    await mount()
    useUiStore.setState({ guideCard: 'card', leftTab: 'layers' })
    let finish: () => void = () => {}
    const actual = await vi.importActual<typeof import('@/store/liveSync')>('@/store/liveSync')
    vi.mocked(refreshProjectNow).mockImplementationOnce(actual.refreshProjectNow)
    vi.mocked(refreshProject).mockImplementationOnce(() => new Promise((resolve) => {
      finish = () => resolve({} as Awaited<ReturnType<typeof refreshProject>>)
    }))
    vi.mocked(fetchPanels).mockResolvedValueOnce({ panels: [], figures_dir: '/figures' })
    const loadAssets = vi.fn(async () => {})
    useRuntimeAssetStore.setState({ assets: [], loadAssets })
    useAssetStore.getState().clear()
    await openWith(report({ ...STATES.noFigure, no_figure_hint: { kind: 'raster_script', library: 'pillow', in_project: true } }))
    await act(async () => secondary()!.click())
    expect(useUiStore.getState().leftTab).toBe('assets')
    expect(useAssetStore.getState().loading).toBe(true)
    expect(fetchPanels).not.toHaveBeenCalled()
    expect(loadAssets).not.toHaveBeenCalled()
    await act(async () => finish())
    expect(loadAssets).toHaveBeenCalledTimes(1)
    // 卡片委派真实统一刷新，强制清单只取一次，不再自己用 loading 猜是否补跑。
    expect(fetchPanels).toHaveBeenCalledTimes(1)
    expect(useAssetStore.getState().loading).toBe(false)
  })

  it.each([
    ['in_project 为假', { kind: 'raster_script' as const, library: 'pillow' as const, in_project: false }],
    ['老后端没有 in_project', { kind: 'raster_script' as const, library: 'pillow' as const }],
  ])('位图提示但后端没确认图落在素材库范围内（%s）：原因句照说，主按钮只有「知道了」', async (_name, hint) => {
    await mount()
    useUiStore.setState({ guideCard: 'card', leftTab: 'layers' })
    await openWith(report({ ...STATES.noFigure, no_figure_hint: hint }))
    expect(panel().dataset.prepState).toBe('no_figure_raster')
    expect(panel().querySelector('[data-prep-line]')?.textContent).toBe('这张图是用 Pillow 画的，Tavotto 不能直接编辑它。')
    expect(primary()?.textContent).toBe('生成可编辑版本')
    expect(secondary()?.textContent).toBe('知道了')
    await act(async () => secondary()!.click())
    expect(useUiStore.getState().leftTab).toBe('layers')
    expect(useUiStore.getState().guideCard).toBe('closed')
  })

  it('Pillow 画图脚本、有输出文件（path 非空）：沿用「画成图片」句子、详情说图片文件可排版，仍有「打开素材库」次按钮', async () => {
    await mount()
    useUiStore.setState({ guideCard: 'card', leftTab: 'layers' })
    await openWith(report({ ...STATES.noFigure, no_figure_hint: { kind: 'raster_script', library: 'pillow', in_project: true, path: 'out.png' } }))
    expect(panel().dataset.prepState).toBe('no_figure_raster')
    expect(panel().querySelector('[data-prep-line]')?.textContent).toBe('这个脚本是用 Pillow 直接画成图片的，不是 Matplotlib 图')
    expect(primary()?.textContent).toBe('生成可编辑版本')
    expect(secondary()?.textContent).toBe('打开素材库')
    await toggleDetails()
    expect(details()?.querySelector('[data-prep-nofigure-raster-why]')?.textContent).toContain('图片文件')
  })

  it.each([
    ['path 为 null', { kind: 'raster_script' as const, library: 'pillow' as const, in_project: false, path: null }],
    ['path 缺省', { kind: 'raster_script' as const, library: 'pillow' as const, in_project: false }],
  ])('Pillow 画图脚本、找不到输出文件（%s）：说「Pillow 画的、不能直接编辑」，主按钮「生成可编辑版本」，次按钮只有「知道了」', async (_name, hint) => {
    await mount()
    useUiStore.setState({ guideCard: 'card', leftTab: 'layers' })
    await openWith(report({ ...STATES.noFigure, no_figure_hint: hint }))
    expect(panel().dataset.prepState).toBe('no_figure_raster')
    expect(panel().querySelector('[data-prep-line]')?.textContent).toBe('这张图是用 Pillow 画的，Tavotto 不能直接编辑它。')
    expect(primary()?.textContent).toBe('生成可编辑版本')
    expect(secondary()?.textContent).toBe('知道了')
    await toggleDetails()
    expect(details()?.querySelector('[data-prep-nofigure-raster-why]')?.textContent).toContain('生成可编辑版本')
  })

  describe('R / MATLAB 入口卡（ADR 0143 修订）', () => {
    const readyR = {
      session: 'sid-r', state: 'ready' as const, script: 'fig.R', target_name: 'fig_matplotlib.py', library: null,
      source_language: 'r' as const,
      comparison: { original: null, candidate: { figures: 1, width_mm: 50, height_mm: 40 }, aspect_delta: null, pixel_diff: null, reference: null, verification: 'unknown' as const },
      has_preview: true,
    }
    const done = { status: 'done', final: true, task: { outcome: 'completed', unmet: [] } }
    const foreignCard = () => host.querySelector('[data-foreign-card]') as HTMLElement | null
    const startBtn = () => host.querySelector('[data-foreign-start]') as HTMLButtonElement | null
    const line = () => foreignCard()?.querySelector('[data-prep-line]')?.textContent
    const settle = () => act(async () => { await vi.advanceTimersByTimeAsync(POLL_MS + 10) })
    async function mountBoth() {
      host = document.createElement('div')
      document.body.appendChild(host)
      root = createRoot(host)
      await act(async () => {
        root.render(
          <TooltipProvider>
            <PreparationCard />
            <ForeignScriptCard />
          </TooltipProvider>,
        )
      })
    }
    const offer = (script: string, language: 'r' | 'matlab') =>
      act(async () => useForeignScriptStore.getState().setOffer({ script, language }))
    beforeEach(() => {
      vi.useFakeTimers({ toFake: ['setTimeout'] })
      useConvertStore.setState({ entries: {} })
      useForeignScriptStore.setState({ offer: null })
      vi.mocked(startConvert).mockReset().mockResolvedValue({ session: 'sid-r', script: 'fig.R' })
      vi.mocked(fetchAiSession).mockReset().mockResolvedValue(done as never)
      vi.mocked(fetchConvert).mockReset().mockResolvedValue(readyR)
      vi.mocked(adoptConvert).mockReset().mockResolvedValue({ script: 'fig_matplotlib.py', name: 'fig_matplotlib.py' })
      vi.mocked(discardConvert).mockReset().mockResolvedValue({ ok: true })
    })
    afterEach(() => vi.useRealTimers())

    it.each([
      ['r', 'fig.R', '这是 R 脚本，Tavotto 可以另写一份能直接编辑的 Matplotlib 版本，原脚本不动。'],
      ['matlab', 'fig.m', '这是 MATLAB 脚本，Tavotto 可以另写一份能直接编辑的 Matplotlib 版本，原脚本不动。'],
    ] as const)('%s 脚本：一句话 + 一个主按钮「生成可编辑版本」，没有横条', async (language, script, sentence) => {
      await mountBoth()
      expect(foreignCard()).toBeNull()
      await offer(script, language)
      expect(foreignCard()?.dataset.foreignCard).toBe(language)
      expect(line()).toBe(sentence)
      expect(startBtn()?.textContent).toBe('生成可编辑版本')
      expect(visibleSentenceCount(foreignCard()!, 'zh-CN')).toBeLessThanOrEqual(1)
      expect(visiblePrimaryButtons(foreignCard()!)).toBe(1)
      expect(host.querySelector('[role="banner"], [data-banner]')).toBeNull()
    })

    it('点主按钮：按来源语言发起（source_language，不带 library），进度 → 对照；没有原图时说“未对照验证”', async () => {
      await mountBoth()
      await offer('fig.R', 'r')
      await act(async () => startBtn()!.click())
      const body = vi.mocked(startConvert).mock.calls[0]![0]
      expect(body).toMatchObject({ script: 'fig.R', source_language: 'r', path: null })
      expect(body).not.toHaveProperty('library')
      expect(foreignCard()?.dataset.convertPhase).toBe('running')
      await settle()
      expect(foreignCard()?.dataset.convertPhase).toBe('ready')
      expect(foreignCard()?.querySelector('[data-convert-unverified]')?.textContent).toBe('Tavotto 没有运行 R / MATLAB，请并排看一眼再采用')
      expect(foreignCard()?.querySelector('[data-prep-primary]')?.textContent).toBe('采用新版本')
    })

    it('有原图（并排）时不说“未对照验证”；装好的包说一句', async () => {
      vi.mocked(fetchConvert).mockResolvedValue({
        ...readyR,
        installed: ['pandas', 'statsmodels'],
        comparison: { ...readyR.comparison, original: { path: 'fig.png', bytes: 9, sha256: 'x', width_px: 80, height_px: 40 }, reference: 'fig.png', verification: 'side_by_side' },
      })
      await mountBoth()
      await offer('fig.R', 'r')
      await act(async () => startBtn()!.click())
      await settle()
      expect(foreignCard()?.querySelector('[data-convert-unverified]')).toBeNull()
      expect(foreignCard()?.querySelector('[data-convert-installed]')?.textContent).toBe('已为这个项目装好 pandas 和 statsmodels')
    })

    it('出现时 PreparationCard 让开：一次只有一张一句话卡片', async () => {
      await mountBoth()
      useUiStore.setState({ guideCard: 'card' })
      await openWith(report(STATES.noFigure))
      expect(host.querySelector('[data-prep-state]:not([data-foreign-card])')).not.toBeNull()
      await offer('fig.R', 'r')
      expect(host.querySelectorAll('[data-prep-card]').length).toBe(1)
      expect(foreignCard()).not.toBeNull()
    })

    it('× 只收起这条提议：不影响别的状态；运行中点 × 会取消会话', async () => {
      await mountBoth()
      await offer('fig.R', 'r')
      await act(async () => startBtn()!.click())
      await act(async () => (host.querySelector('[data-prep-close]') as HTMLButtonElement).click())
      expect(useForeignScriptStore.getState().offer).toBeNull()
      expect(foreignCard()).toBeNull()
      expect(vi.mocked(aiCancel)).toHaveBeenCalledWith('sid-r', expect.anything())
    })

    it('Agent 停下说明（needs_input）：一句话带原因，主按钮是“知道了”而不是重试', async () => {
      vi.mocked(fetchAiSession).mockResolvedValue({
        status: 'done', final: true,
        task: { outcome: 'needs_input', unmet: [{ constraint_id: 'intent', reason_code: 'needs_input', detail: 'lme4::lmer 在 Python 里没有逐一对应' }] },
      } as never)
      await mountBoth()
      await offer('fig.R', 'r')
      await act(async () => startBtn()!.click())
      await settle()
      expect(foreignCard()?.dataset.convertPhase).toBe('failed')
      expect(line()).toBe('没有生成新版本：lme4::lmer 在 Python 里没有逐一对应')
      expect(foreignCard()?.querySelector('[data-convert-retry]')).toBeNull()
      const btn = foreignCard()?.querySelector('[data-convert-dismiss]') as HTMLButtonElement
      expect(btn.textContent).toBe('知道了')
      await act(async () => btn.click())
      expect(foreignCard()).toBeNull()
      expect(useConvertStore.getState().entries['fig.R']).toBeUndefined()
    })

    it.each([
      ['missing_dependency', '新版本要用的 Python 包没装上（自动安装已关闭，或这些包没法自动装），请手动装好后重试'],
      ['dependency_install_failed', '新版本要用的 Python 包没装成功，请稍后重试'],
    ])('失败原因 %s 有自己的一句话；可以重试', async (reason, sentence) => {
      vi.mocked(fetchAiSession).mockResolvedValue({
        status: 'done', final: true, task: { outcome: 'failed', unmet: [{ constraint_id: 'render.capture', reason_code: reason }] },
      } as never)
      await mountBoth()
      await offer('fig.R', 'r')
      await act(async () => startBtn()!.click())
      await settle()
      expect(line()).toBe(sentence)
      expect(foreignCard()?.querySelector('[data-convert-retry]')?.textContent).toBe('重试')
    })

    it('采用后提议结束（不会退回“生成可编辑版本”再提议一遍）', async () => {
      await mountBoth()
      mockCreate.mockResolvedValue(report({ ...STATES.noFigure, no_figure_hint: null }))
      await offer('fig.R', 'r')
      await act(async () => startBtn()!.click())
      await settle()
      await act(async () => (foreignCard()?.querySelector('[data-convert-adopt]') as HTMLButtonElement).click())
      expect(adoptConvert).toHaveBeenCalledWith('sid-r', expect.anything())
      expect(useForeignScriptStore.getState().offer).toBeNull()
      expect(foreignCard()).toBeNull()
    })
  })

  describe('R / MATLAB 缺依赖卡（ADR 0143 修订，Codex #1330 P1-2）', () => {
    const base = { packages: ['pandas', 'statsmodels'], outside_list: [] as string[], impact_digest: 'dg-1', target: 'tavotto_managed', creates_environment: true, downloads_python: false, modifies_user_environment: false }
    const askOnce = { ...base, kind: 'ask_once' as const, env: { kind: 'conda' as const, name: 'myenv' }, action: 'install_managed' as const }
    const uncommon = { ...base, kind: 'uncommon' as const, packages: ['obscure-pkg', 'pandas'], outside_list: ['obscure-pkg'], env: { kind: 'project_venv' as const, name: 'proj/.venv' }, action: 'install' as const }
    const system = { ...base, kind: 'system' as const, env: { kind: 'system' as const, name: 'python3' }, action: 'choose_env' as const }
    const heldSession = (card: object) =>
      ({ status: 'done', final: true, task: { outcome: 'needs_input', unmet: [{ constraint_id: 'intent', reason_code: 'dependency_decision', data: card }] } }) as never
    const readyR = {
      session: 'sid-r', state: 'ready' as const, script: 'fig.R', target_name: 'fig_matplotlib.py', library: null, source_language: 'r' as const,
      comparison: { original: null, candidate: { figures: 1, width_mm: 50, height_mm: 40 }, aspect_delta: null, pixel_diff: null, reference: null, verification: 'unknown' as const },
      has_preview: true,
    }
    const foreignCard = () => host.querySelector('[data-foreign-card]') as HTMLElement | null
    const line = () => foreignCard()?.querySelector('[data-prep-line]')?.textContent
    const settle = () => act(async () => { await vi.advanceTimersByTimeAsync(POLL_MS + 10) })
    async function begin(card: object) {
      host = document.createElement('div')
      document.body.appendChild(host)
      root = createRoot(host)
      await act(async () => { root.render(<TooltipProvider><PreparationCard /><ForeignScriptCard /></TooltipProvider>) })
      vi.mocked(fetchAiSession).mockResolvedValue(heldSession(card))
      await act(async () => useForeignScriptStore.getState().setOffer({ script: 'fig.R', language: 'r' }))
      await act(async () => (host.querySelector('[data-foreign-start]') as HTMLButtonElement).click())
      await settle()
    }
    beforeEach(() => {
      vi.useFakeTimers({ toFake: ['setTimeout'] })
      useConvertStore.setState({ entries: {} })
      useForeignScriptStore.setState({ offer: null })
      useUiStore.setState({ engineEnvOpen: false, engineEnvPick: false })
      vi.mocked(startConvert).mockReset().mockResolvedValue({ session: 'sid-r', script: 'fig.R' })
      vi.mocked(fetchConvert).mockReset().mockResolvedValue(readyR)
      vi.mocked(discardConvert).mockReset().mockResolvedValue({ ok: true })
      vi.mocked(resolveConvertDependencies).mockReset().mockResolvedValue({ session: 'sid-r', state: 'resuming' })
      vi.mocked(aiCancel).mockClear()
    })
    afterEach(() => {
      vi.useRealTimers()
      useForeignScriptStore.setState({ offer: null })
      useConvertStore.setState({ entries: {} })
      useUiStore.setState({ engineEnvOpen: false, engineEnvPick: false })
    })

    it('问一次：一句话点名环境 + 主按钮「装，以后这个环境都自动装」+ 次按钮「不装」；点主按钮回显卡片摘要并接着走同一个候选', async () => {
      await begin(askOnce)
      expect(foreignCard()?.dataset.convertPhase).toBe('deps')
      expect(line()).toBe('要给这个环境（conda: myenv）装缺的包吗（会装进 Tavotto 为本项目建的独立环境，不改动它）？')
      const btn = foreignCard()?.querySelector('[data-convert-deps-install]') as HTMLButtonElement
      expect(btn.textContent).toBe('装，以后这个环境都自动装')
      expect(foreignCard()?.querySelector('[data-convert-decline]')?.textContent).toBe('不装')
      expect(visibleSentenceCount(foreignCard()!, 'zh-CN')).toBeLessThanOrEqual(1)
      expect(visiblePrimaryButtons(foreignCard()!)).toBe(1)
      expect(startConvert).toHaveBeenCalledTimes(1)
      await act(async () => btn.click())
      expect(resolveConvertDependencies).toHaveBeenCalledWith('sid-r', 'dg-1', expect.anything())
      expect(foreignCard()?.dataset.convertPhase).toBe('resuming')
      await settle()
      expect(foreignCard()?.dataset.convertPhase).toBe('ready')
      expect(startConvert).toHaveBeenCalledTimes(1)
    })

    it('名单外：点名这个包「不常见，确认是你要的再装」；主按钮只装这一次（不是「以后都自动装」）', async () => {
      await begin(uncommon)
      expect(line()).toBe('obscure-pkg 这个包不常见，确认是你要的再装。')
      const btn = foreignCard()?.querySelector('[data-convert-deps-install]') as HTMLButtonElement
      expect(btn.dataset.convertDepsInstall).toBe('uncommon')
      expect(btn.textContent).toBe('确认，装上')
      expect(visibleSentenceCount(foreignCard()!, 'zh-CN')).toBeLessThanOrEqual(1)
      expect(visiblePrimaryButtons(foreignCard()!)).toBe(1)
    })

    it('系统 Python：不装，一句话说明 + 主按钮打开现有的「选择运行环境」入口；没有「装」动作', async () => {
      await begin(system)
      expect(line()).toBe('这个 Python 是系统自带的，往里面装包可能弄坏系统工具。请给项目换一个环境。')
      expect(foreignCard()?.querySelector('[data-convert-deps-install]')).toBeNull()
      const btn = foreignCard()?.querySelector('[data-convert-choose-env]') as HTMLButtonElement
      expect(btn.textContent).toBe('选择运行环境')
      expect(visiblePrimaryButtons(foreignCard()!)).toBe(1)
      await act(async () => btn.click())
      expect(useUiStore.getState().engineEnvOpen).toBe(true)
      expect(useUiStore.getState().engineEnvPick).toBe(true)
      expect(resolveConvertDependencies).not.toHaveBeenCalled()
    })

    it('系统 Python：选完环境且解释器变了 → 自动 replan，卡片说“正在按新环境重新检查…”，结果换成对应的新卡；没变不动', async () => {
      await begin(system)
      vi.mocked(replanConvertDependencies).mockReset().mockResolvedValue({ session: 'sid-r', state: 'resuming' })
      await act(async () => (foreignCard()?.querySelector('[data-convert-choose-env]') as HTMLButtonElement).click())
      // 选择器关闭但没换解释器（环境刷新、身份相同）：保持原卡
      await act(async () => useEnvStore.setState({ env: { ...useEnvStore.getState().env } as never }))
      expect(replanConvertDependencies).not.toHaveBeenCalled()
      expect(foreignCard()?.dataset.convertPhase).toBe('deps')
      vi.mocked(fetchConvert).mockResolvedValue({ state: 'awaiting_dependencies', dependency_card: askOnce } as never)
      await act(async () => useEnvStore.setState({ env: { ok: true, python: '/opt/other/bin/python' } as never }))
      expect(replanConvertDependencies).toHaveBeenCalledWith('sid-r', expect.anything())
      expect(foreignCard()?.dataset.convertPhase).toBe('resuming')
      expect(line()).toBe('正在按新环境重新检查…')
      await settle()
      expect(foreignCard()?.dataset.convertPhase).toBe('deps')
      expect(foreignCard()?.querySelector('[data-convert-deps-install]')).not.toBeNull()
    })

    it('停止来得太晚（依赖已装好）：一句话「依赖已经装好了…」+ 主按钮「继续」；点继续接着校验同一个候选', async () => {
      await begin(askOnce)
      vi.mocked(fetchConvert).mockResolvedValue({ ...readyR, state: 'deps_installed' as never, installed: ['pandas'] } as never)
      await act(async () => (foreignCard()?.querySelector('[data-convert-deps-install]') as HTMLButtonElement).click())
      await settle()
      expect(foreignCard()?.dataset.convertPhase).toBe('installed')
      expect(line()).toBe('依赖已经装好了（停止来得太晚，没能撤回）。')
      const btn = foreignCard()?.querySelector('[data-convert-continue]') as HTMLButtonElement
      expect(btn.textContent).toBe('继续')
      expect(visiblePrimaryButtons(foreignCard()!)).toBe(1)
      vi.mocked(fetchConvert).mockResolvedValue(readyR)
      await act(async () => btn.click())
      expect(replanConvertDependencies).toHaveBeenCalledWith('sid-r', expect.anything())
      await settle()
      expect(foreignCard()?.dataset.convertPhase).toBe('ready')
    })

    it('「不装」放弃候选并收起；装依赖期间点 × / 停止 → 放弃（后端据此取消正在进行的安装）', async () => {
      await begin(askOnce)
      await act(async () => (foreignCard()?.querySelector('[data-convert-decline]') as HTMLButtonElement).click())
      expect(discardConvert).toHaveBeenCalledWith('sid-r', expect.anything())
      expect(useConvertStore.getState().entries['fig.R']).toBeUndefined()

      vi.mocked(discardConvert).mockClear()
      useConvertStore.setState({ entries: {} })
      useForeignScriptStore.setState({ offer: null })
      await act(async () => useForeignScriptStore.getState().setOffer({ script: 'fig.R', language: 'r' }))
      await act(async () => (host.querySelector('[data-foreign-start]') as HTMLButtonElement).click())
      await settle()
      await act(async () => (foreignCard()?.querySelector('[data-convert-deps-install]') as HTMLButtonElement).click())
      expect(foreignCard()?.dataset.convertPhase).toBe('resuming')
      await act(async () => (foreignCard()?.querySelector('[data-convert-stop]') as HTMLButtonElement).click())
      expect(discardConvert).toHaveBeenCalledWith('sid-r', expect.anything())
      expect(aiCancel).not.toHaveBeenCalled()
      // 等后端回话才清卡：期间只有一句「正在停止…」，没有可点的停止按钮
      expect(foreignCard()?.dataset.convertPhase).toBe('resuming')
      expect(line()).toBe('正在停止…')
      expect(foreignCard()?.querySelector('[data-convert-stop]')).toBeNull()
      vi.mocked(fetchConvert).mockRejectedValue(new ApiError('convert_not_found', 404, {}))
      await settle()
      expect(useConvertStore.getState().entries['fig.R']).toBeUndefined()
    })

    it('续跑中停止来得太晚（后端回 deps_installed）：「正在停止…」之后出「依赖已经装好了」+「继续」，不被清掉', async () => {
      await begin(askOnce)
      await act(async () => (foreignCard()?.querySelector('[data-convert-deps-install]') as HTMLButtonElement).click())
      vi.mocked(fetchConvert).mockResolvedValue({ ...readyR, state: 'deps_installed' as never, installed: ['pandas'] } as never)
      await act(async () => (foreignCard()?.querySelector('[data-convert-stop]') as HTMLButtonElement).click())
      expect(line()).toBe('正在停止…')
      await settle()
      expect(foreignCard()?.dataset.convertPhase).toBe('installed')
      expect(line()).toBe('依赖已经装好了（停止来得太晚，没能撤回）。')
      expect(foreignCard()?.querySelector('[data-convert-continue]')).not.toBeNull()
    })

    describe('卡片 × 与对账（Codex #1330 P2）', () => {
      const close = () => foreignCard()?.querySelector('[data-prep-close]') as HTMLButtonElement
      const resume = async () => {
        await begin(askOnce)
        await act(async () => (foreignCard()?.querySelector('[data-convert-deps-install]') as HTMLButtonElement).click())
        expect(foreignCard()?.dataset.convertPhase).toBe('resuming')
      }

      it('续跑中点 ×：卡片不卸载、说「正在停止…」；后端回 deps_installed → 出「继续」，提议还在', async () => {
        await resume()
        vi.mocked(fetchConvert).mockResolvedValue({ ...readyR, state: 'deps_installed' as never, installed: ['pandas'] } as never)
        await act(async () => close().click())
        expect(foreignCard()).not.toBeNull()
        expect(line()).toBe('正在停止…')
        expect(useForeignScriptStore.getState().offer).not.toBeNull()
        await settle()
        expect(foreignCard()?.dataset.convertPhase).toBe('installed')
        expect(foreignCard()?.querySelector('[data-convert-continue]')).not.toBeNull()
        expect(useForeignScriptStore.getState().offer).not.toBeNull()
      })

      it('续跑中点 ×，后端回 discarded：条目与提议一起清掉，卡片消失', async () => {
        await resume()
        vi.mocked(fetchConvert).mockResolvedValue({ session: 'sid-r', state: 'discarded' } as never)
        await act(async () => close().click())
        expect(foreignCard()).not.toBeNull()
        expect(line()).toBe('正在停止…')
        await settle()
        expect(useConvertStore.getState().entries['fig.R']).toBeUndefined()
        expect(useForeignScriptStore.getState().offer).toBeNull()
        expect(foreignCard()).toBeNull()
      })

      describe('「暂时连不上」（failed + lost）点 ×', () => {
        const lostCard = async () => {
          await begin(askOnce)
          vi.mocked(resolveConvertDependencies).mockRejectedValue(new Error('net'))
          vi.mocked(fetchConvert).mockRejectedValue(new Error('net'))
          await act(async () => (foreignCard()?.querySelector('[data-convert-deps-install]') as HTMLButtonElement).click())
          await settle()
          expect(foreignCard()?.dataset.convertPhase).toBe('failed')
          expect(useConvertStore.getState().entries['fig.R']?.lost).not.toBeNull()
          vi.mocked(discardConvert).mockClear()
        }

        // discard 先挂住：此刻对账还没开始，才看得见「正在停止…」
        const gateDiscard = () => {
          let release!: () => void
          vi.mocked(discardConvert).mockImplementation(() => new Promise<never>((r) => (release = () => r({ ok: true } as never))))
          return () => release()
        }

        it('发 discard、卡片说「正在停止…」，对账回 discarded → 条目与提议一起清掉', async () => {
          await lostCard()
          const release = gateDiscard()
          vi.mocked(fetchConvert).mockReset().mockResolvedValue({ session: 'sid-r', state: 'discarded' } as never)
          await act(async () => close().click())
          expect(discardConvert).toHaveBeenCalledWith('sid-r', expect.anything())
          expect(foreignCard()).not.toBeNull()
          expect(line()).toBe('正在停止…')
          expect(useForeignScriptStore.getState().offer).not.toBeNull()
          await act(async () => release())
          await settle()
          expect(useConvertStore.getState().entries['fig.R']).toBeUndefined()
          expect(useForeignScriptStore.getState().offer).toBeNull()
        })

        it('对账回 deps_installed → 出「继续」，提议还在', async () => {
          await lostCard()
          const release = gateDiscard()
          vi.mocked(fetchConvert).mockReset().mockResolvedValue({ ...readyR, state: 'deps_installed' as never, installed: ['pandas'] } as never)
          await act(async () => close().click())
          expect(discardConvert).toHaveBeenCalled()
          expect(line()).toBe('正在停止…')
          await act(async () => release())
          await settle()
          expect(foreignCard()?.dataset.convertPhase).toBe('installed')
          expect(foreignCard()?.querySelector('[data-convert-continue]')).not.toBeNull()
          expect(useForeignScriptStore.getState().offer).not.toBeNull()
        })

        it('对账回 ready → 出可采用的结果', async () => {
          await lostCard()
          vi.mocked(fetchConvert).mockReset().mockResolvedValue(readyR)
          await act(async () => close().click())
          await settle()
          expect(foreignCard()?.dataset.convertPhase).toBe('ready')
          expect(useForeignScriptStore.getState().offer).not.toBeNull()
        })
      })

      it('非续跑阶段（依赖卡）点 ×：立即消失（原行为）', async () => {
        await begin(askOnce)
        expect(foreignCard()?.dataset.convertPhase).toBe('deps')
        await act(async () => close().click())
        expect(foreignCard()).toBeNull()
        expect(useForeignScriptStore.getState().offer).toBeNull()
      })
    })

    it('摘要对不上时后端回新的卡片：卡片换成新的，不当失败', async () => {
      await begin(askOnce)
      vi.mocked(fetchConvert).mockResolvedValue({ ...readyR, state: 'awaiting_dependencies', dependency_card: { ...askOnce, impact_digest: 'dg-2', notice: 'dependency_impact_changed' } } as never)
      await act(async () => (foreignCard()?.querySelector('[data-convert-deps-install]') as HTMLButtonElement).click())
      await settle()
      expect(foreignCard()?.dataset.convertPhase).toBe('deps')
      expect(useConvertStore.getState().entries['fig.R']?.card?.impact_digest).toBe('dg-2')
    })

    it('续跑失败：落到失败卡（一句话 + 重试）', async () => {
      await begin(askOnce)
      vi.mocked(fetchConvert).mockResolvedValue({ ...readyR, state: 'failed', failure: { reason: 'dependency_install_failed' } } as never)
      await act(async () => (foreignCard()?.querySelector('[data-convert-deps-install]') as HTMLButtonElement).click())
      await settle()
      expect(foreignCard()?.dataset.convertPhase).toBe('failed')
      expect(line()).toBe('新版本要用的 Python 包没装成功，请稍后重试')
    })
  })

  describe('生成可编辑版本（ADR 0143）', () => {
    const hint = { kind: 'raster_script' as const, library: 'pillow' as const, in_project: true, path: 'out.png' }
    const ready = {
      session: 'sid1', state: 'ready' as const, script: 'plot.py', target_name: 'plot_matplotlib.py', library: 'pillow',
      comparison: { original: { path: 'out.png', bytes: 10, sha256: 'x', width_px: 80, height_px: 40 }, candidate: { figures: 1, width_mm: 50, height_mm: 40 }, aspect_delta: 0.3, pixel_diff: null },
      has_preview: true,
    }
    const done = { status: 'done', final: true, task: { outcome: 'completed', unmet: [] } }
    beforeEach(() => {
      vi.useFakeTimers({ toFake: ['setTimeout'] })
      useConvertStore.setState({ entries: {} })
      useAiFixStore.setState({ handoffs: {} })
      vi.mocked(startConvert).mockReset().mockResolvedValue({ session: 'sid1', script: 'plot.py' })
      vi.mocked(fetchAiSession).mockReset().mockResolvedValue(done as never)
      vi.mocked(fetchConvert).mockReset().mockResolvedValue(ready)
      vi.mocked(adoptConvert).mockReset().mockResolvedValue({ script: 'plot_matplotlib.py', name: 'plot_matplotlib.py' })
      vi.mocked(discardConvert).mockReset().mockResolvedValue({ ok: true })
    })
    afterEach(() => vi.useRealTimers())
    const settle = () => act(async () => { await vi.advanceTimersByTimeAsync(POLL_MS + 10) })

    it('主按钮发起任务（带库与输出路径）→ 进度 → 对照视图（左原图右新版）', async () => {
      await mount()
      useUiStore.setState({ guideCard: 'card' })
      await openWith(report({ ...STATES.noFigure, no_figure_hint: hint }))
      await act(async () => primary()!.click())
      expect(startConvert).toHaveBeenCalledWith(
        expect.objectContaining({ script: 'plot.py', library: 'pillow', path: 'out.png', client_request_id: expect.any(String) }),
        expect.anything(),
      )
      expect(panel().dataset.convertPhase).toBe('running')
      expect(panel().querySelector('[data-prep-progress]')).not.toBeNull()
      expect(primary()).toBeNull()
      await settle()
      expect(panel().dataset.convertPhase).toBe('ready')
      expect(panel().querySelector('[data-convert-original]')).not.toBeNull()
      expect(panel().querySelector<HTMLImageElement>('[data-convert-new]')!.src).toContain('/api/ai/convert/sid1/preview.svg')
      expect(panel().querySelector('[data-convert-aspect]')?.textContent).toContain('30%')
      expect(primary()?.textContent).toBe('采用新版本')
      expect(panel().querySelector('[data-convert-discard]')?.textContent).toBe('放弃')
    })

    it('采用：后端新建脚本，随后用「打开并运行」接进画布；原脚本的位图面板不被替换', async () => {
      await mount()
      useUiStore.setState({ guideCard: 'card' })
      await openWith(report({ ...STATES.noFigure, no_figure_hint: hint }))
      mockCreate.mockResolvedValueOnce(report({ ...STATES.noFigure, no_figure_hint: null }))
      await act(async () => primary()!.click())
      await settle()
      await act(async () => (panel().querySelector('[data-convert-adopt]') as HTMLButtonElement).click())
      expect(adoptConvert).toHaveBeenCalledWith('sid1', expect.anything())
      expect(mockCreate).toHaveBeenLastCalledWith(expect.objectContaining({ script: 'plot_matplotlib.py' }), expect.anything(), expect.anything())
      expect(useConvertStore.getState().entries['plot.py']).toBeUndefined()
    })

    it('放弃：删候选、回到原来的卡片（主按钮仍是「生成可编辑版本」）', async () => {
      await mount()
      useUiStore.setState({ guideCard: 'card' })
      await openWith(report({ ...STATES.noFigure, no_figure_hint: hint }))
      await act(async () => primary()!.click())
      await settle()
      await act(async () => (panel().querySelector('[data-convert-discard]') as HTMLButtonElement).click())
      expect(discardConvert).toHaveBeenCalledWith('sid1', expect.anything())
      expect(primary()?.textContent).toBe('生成可编辑版本')
    })

    it('AI 修复交接过来（没有 no_figure_hint 的失败卡）：同一张卡接上进度 → 对照；放弃后交接记录清掉、回到原卡', async () => {
      await mount()
      useUiStore.setState({ guideCard: 'card' })
      await openWith(report(STATES.errPlain))
      const key = useProjectPreparationStore.getState().focus!
      expect(panel().dataset.convertPhase).toBeUndefined()
      await act(async () => {
        useAiFixStore.setState({ handoffs: { [key]: { script: 'plot.py', library: 'pillow', path: null } } })
        void useConvertStore.getState().start('plot.py', 'pillow', null, 'python')
      })
      expect(startConvert).toHaveBeenCalledWith(expect.objectContaining({ script: 'plot.py', library: 'pillow' }), expect.anything())
      expect(panel().dataset.convertPhase).toBe('running')
      await settle()
      expect(panel().dataset.convertPhase).toBe('ready')
      expect(primary()?.textContent).toBe('采用新版本')
      await act(async () => (panel().querySelector('[data-convert-discard]') as HTMLButtonElement).click())
      expect(panel().dataset.convertPhase).toBeUndefined()
      expect(useAiFixStore.getState().handoffs[key]).toBeUndefined()
    })

    it('卡片本来有 convert 目标（plot.py）、AI 交接到另一个脚本：以交接为准，渲染交接脚本的转换条目', async () => {
      await mount()
      useUiStore.setState({ guideCard: 'card' })
      await openWith(report({ ...STATES.noFigure, no_figure_hint: hint }))
      const key = useProjectPreparationStore.getState().focus!
      vi.mocked(startConvert).mockResolvedValue({ session: 'sidB', script: 'other.py' })
      await act(async () => {
        useAiFixStore.setState({ handoffs: { [key]: { script: 'other.py', library: 'opencv', path: 'o.png' } } })
        void useConvertStore.getState().start('other.py', 'opencv', 'o.png', 'python')
      })
      expect(useConvertStore.getState().entries['plot.py']).toBeUndefined()
      expect(panel().dataset.convertPhase).toBe('running')
    })

    it('失败：一句话原因 + 「重试」；重试再发起一次', async () => {
      await mount()
      useUiStore.setState({ guideCard: 'card' })
      vi.mocked(fetchAiSession).mockResolvedValue({ status: 'failed', final: true, task: { outcome: 'failed', unmet: [{ constraint_id: 'render.capture', reason_code: 'no_matplotlib_figure' }] } } as never)
      await openWith(report({ ...STATES.noFigure, no_figure_hint: hint }))
      await act(async () => primary()!.click())
      await settle()
      expect(panel().dataset.convertPhase).toBe('failed')
      expect(panel().querySelector('[data-prep-line]')?.textContent).toBe('生成的脚本跑完没有画出 Matplotlib 图')
      expect(primary()?.textContent).toBe('重试')
      await act(async () => primary()!.click())
      expect(startConvert).toHaveBeenCalledTimes(2)
      expect(adoptConvert).not.toHaveBeenCalled()
    })

    it('后端拒绝（总开关关了）：落成失败卡，不进入采用', async () => {
      await mount()
      useUiStore.setState({ guideCard: 'card' })
      const { ApiError } = await import('@/lib/api')
      vi.mocked(startConvert).mockRejectedValueOnce(new ApiError('x', 404, { code: 'convert_disabled' }))
      await openWith(report({ ...STATES.noFigure, no_figure_hint: hint }))
      await act(async () => primary()!.click())
      expect(panel().dataset.convertPhase).toBe('failed')
      expect(panel().querySelector('[data-prep-line]')?.textContent).toContain('已被关闭')
    })

    it('用既有 AI 流程算出的实际 Agent（首选 Claude / Codex 不可用时不默认 codex）', async () => {
      await mount()
      useUiStore.setState({ guideCard: 'card' })
      useAiStore.setState({
        agent: null,
        caps: {
          agents: [
            { id: 'codex', usable: false, models: [], efforts: [], default_model: null, default_effort: null },
            { id: 'claude', usable: true, models: [], efforts: [], default_model: null, default_effort: null },
          ],
        } as never,
      })
      vi.mocked(fetchAiSession).mockResolvedValue({ status: 'running', final: false } as never)
      await openWith(report({ ...STATES.noFigure, no_figure_hint: hint }))
      await act(async () => primary()!.click())
      expect(vi.mocked(startConvert).mock.calls.at(-1)![0]).toMatchObject({ agent: 'claude' })
      useAiStore.setState({ caps: null })
    })

    it('不确定是否送达的失败重试复用同一个 client_request_id；明确拒绝后换新的', async () => {
      await mount()
      useUiStore.setState({ guideCard: 'card' })
      await openWith(report({ ...STATES.noFigure, no_figure_hint: hint }))
      vi.mocked(startConvert).mockRejectedValueOnce(new Error('network down'))
      await act(async () => primary()!.click())
      expect(panel().dataset.convertPhase).toBe('failed')
      vi.mocked(fetchAiSession).mockResolvedValue({ status: 'running', final: false } as never)
      await act(async () => primary()!.click())
      const ids = vi.mocked(startConvert).mock.calls.slice(-2).map((c) => c[0].client_request_id)
      expect(ids[0]).toBeTruthy()
      expect(ids[1]).toBe(ids[0])
      useConvertStore.getState().clear('plot.py')
    })

    it('POST 还没回来时点停止：回来后取消刚起的会话，不重建条目', async () => {
      await mount()
      useUiStore.setState({ guideCard: 'card' })
      let resolve!: (v: { session: string; script: string }) => void
      vi.mocked(startConvert).mockReturnValueOnce(new Promise((r) => (resolve = r)))
      vi.mocked(aiCancel).mockClear()
      await openWith(report({ ...STATES.noFigure, no_figure_hint: hint }))
      await act(async () => primary()!.click())
      await act(async () => { await useConvertStore.getState().stop('plot.py') })
      expect(aiCancel).not.toHaveBeenCalled()
      await act(async () => { resolve({ session: 'late1', script: 'plot.py' }) })
      expect(aiCancel).toHaveBeenCalledWith('late1', expect.anything())
      expect(useConvertStore.getState().entries['plot.py']).toBeUndefined()
    })

    it('停止：取消后端会话并回到原卡片', async () => {
      await mount()
      useUiStore.setState({ guideCard: 'card' })
      vi.mocked(fetchAiSession).mockResolvedValue({ status: 'running', final: false } as never)
      await openWith(report({ ...STATES.noFigure, no_figure_hint: hint }))
      await act(async () => primary()!.click())
      await settle()
      await act(async () => (panel().querySelector('[data-convert-stop]') as HTMLButtonElement).click())
      expect(useConvertStore.getState().entries['plot.py']).toBeUndefined()
      expect(primary()?.textContent).toBe('生成可编辑版本')
    })
  })

  it('没有提示（老后端 / 判不出）的跑完没图照旧', async () => {
    await mount()
    await openWith(report({ ...STATES.noFigure, no_figure_hint: null }))
    expect(panel().dataset.prepState).toBe('no_figure')
    expect(primary()?.textContent).toBe('知道了')
  })

  it('运行出错：详情第一行是脚本的错误原文（不是字面量占位符），完整 traceback 折叠可复制，下面是这一次的诊断', async () => {
    await mount()
    await openWith(report({ ...STATES.failed, ...fromBackend('plain_exception') }))
    expect(panel().querySelector('[data-prep-line]')?.textContent).toBe('运行出错了')
    expect(primary()?.textContent).toBe('再试一次')
    await toggleDetails()
    const first = details()!.firstElementChild as HTMLElement
    expect(first.dataset.prepError).toBe('script_error')
    expect(first.textContent).toContain('ZeroDivisionError: division by zero')
    expect(first.textContent).toContain('File "/work/proj/fig.py", line 7')
    // 完整 traceback 默认折叠、展开可见、能复制
    const fold = details()!.querySelector('[data-prep-traceback]') as HTMLDetailsElement
    expect(fold).not.toBeNull()
    expect(fold.open).toBe(false)
    expect(fold.querySelector('pre')?.textContent).toBe(errorReports.plain_exception.result.error.traceback)
    expect(fold.textContent).toContain('File "/work/proj/fig.py", line 12')
    expect(fold.querySelector('button')).not.toBeNull()
    expect(details()!.textContent).not.toContain('{{')
    expect(details()!.querySelector('[data-task-diagnostic], button')).not.toBeNull()
  })

  it('敏感运行 / 老后端没有 traceback：只露后端主文案，没有"完整错误"折叠', async () => {
    await mount()
    const g = fromBackend('plain_exception')
    await openWith(
      report({ ...STATES.failed, ...g, result: { status: 'error', error: { code: 'script_error', message: 'boom' } } }),
    )
    await toggleDetails()
    expect((details()!.firstElementChild as HTMLElement).textContent).toBe('boom')
    expect(details()!.querySelector('[data-prep-traceback]')).toBeNull()
  })

  it('发送问题反馈（ADR 0118）：开关关着不出现；有「再试一次」主按钮的失败卡把它放进折叠详情；没有任何修复动作的失败卡它就是主按钮', async () => {
    const label = '发送问题反馈'
    // 关着：两种失败卡都没有
    useDiagSendStore.setState({ capability: null })
    await mount()
    await openWith(report({ ...STATES.failed }))
    expect(panel().querySelector('[data-send-report]')).toBeNull()
    await act(async () => root.unmount())
    host.remove()
    // 开着 + 有主按钮：主按钮仍是「再试一次」，发送反馈只在折叠详情里
    useDiagSendStore.setState({ capability: { enabled: true } })
    await mount()
    await openWith(report({ ...STATES.failed }))
    expect(primary()?.textContent).toBe('再试一次')
    expect(panel().querySelector('[data-send-report]')?.closest('[data-prep-details]')).not.toBeNull()
    await act(async () => root.unmount())
    host.remove()
    // 开着 + 没有任何可执行动作：它就是主按钮（不在折叠详情里）
    await mount()
    await openWith(report({ ...STATES.failed, actions: [] }))
    const direct = panel().querySelector('[data-send-report]')
    expect(primary()).toBeNull()
    expect(direct?.textContent).toBe(label)
    expect(direct?.closest('[data-prep-details]')).toBeNull()
    useDiagSendStore.setState({ capability: null })
  })

  it('无参数运行替换掉了此前带参数产出的图名（T09b）：标题照旧「画好了」，详情里说清哪些、怎么恢复', async () => {
    await mount()
    await openWith(report(STATES.completedUnlinked))
    expect(panel().dataset.prepState).toBe('completed_unlinked')
    expect(panel().querySelector('[data-prep-line]')?.textContent).toBe('画好了 1 张图')
    expect(primary()?.dataset.prepPrimary).toBe('enter_edit')
    await toggleDetails()
    expect(details()?.querySelector('[data-prep-unlinked]')?.textContent).toBe(
      '此前带其他参数生成的 a_scaled 和 b_scaled 已不再关联，用原参数再运行一次即可恢复。',
    )
  })

  it('部分捕获同时替换掉了带参数产出的图名：详情里照样说清哪些已不再关联（#1134 Codex r4310249719）', async () => {
    await mount()
    await openWith(report({ ...STATES.completedPartial, unlinked_stems: ['a_scaled'] }))
    await toggleDetails()
    expect(details()?.querySelector('[data-prep-unlinked]')).not.toBeNull()
  })

  it('部分捕获（#1134）：不叫画好了——标题说 N/M 张并点名失败的图，主按钮仍是进入编辑，原因码折叠在详情里', async () => {
    await mount()
    await openWith(report(STATES.completedPartial))
    expect(panel().dataset.prepState).toBe('completed_partial')
    expect(panel().querySelector('[data-prep-line]')?.textContent).toBe('只画好了 1/2 张图，bad 没画出来')
    expect(primary()?.dataset.prepPrimary).toBe('enter_edit')
    await toggleDetails()
    expect(details()?.querySelector('[data-prep-failed-figures]')?.textContent).toBe(
      '没画出来的图：bad（preview_capture_failed）。其余的图可以照常编辑。',
    )
  })

  it('进入编辑：用这次捕获的图进入图内编辑（稳定动作），不发任何运行请求；卡片随即收起、不留角标', async () => {
    await mount()
    await openWith(report(STATES.completed))
    expect(primary()?.dataset.prepPrimary).toBe('enter_edit')
    await act(async () => primary()!.click())
    // 素材清单里还没有这张新图：把这次捕获的描述符交给 openFastEdit（它按「打开 = 换看那一张」新建画布放它），
    // 不先加进当前画布
    expect(vi.mocked(addRuntimePanelToCanvas)).not.toHaveBeenCalled()
    expect(vi.mocked(openFastEdit)).toHaveBeenCalledWith('runtime:plot.py#a', expect.objectContaining({ descriptor: fig('a') }))
    expect(mockAct).not.toHaveBeenCalled()
    expect(vi.mocked(probeScript)).not.toHaveBeenCalled()
    expect(mockCreate).toHaveBeenCalledTimes(1) // 只有打开时那一次只读检查
    expect(useUiStore.getState().guideCard).toBe('closed')
    expect(panel()).toBeNull()
    expect(host.querySelector('[data-prep-pill]')).toBeNull()
    // 再从素材库打开：说的是编辑的进度（编辑渲染可用之后才说「已进入编辑」）
    await act(async () => useUiStore.getState().setGuideCard('card'))
    expect(panel().dataset.prepState).toBe('edit_opening')
    await act(async () => seedExactRender(panelFor('runtime:plot.py#a'), { elements: [] } as never))
    expect(panel().dataset.prepState).toBe('edit_ready')
  })

  it('「已进入编辑」只认入口动作创建的面板上的精确新渲染：markStale() 留下的旧渲染、同文件别的变体都不算', async () => {
    await mount()
    await openWith(report(STATES.completed))
    const target = panelFor('runtime:plot.py#a')
    useRenderStore.getState().patch(renderKeyOf(target), {
      fileId: 'runtime:plot.py#a', status: 'ready', manifest: { elements: [] } as never, stale: true,
      lastPatches: '[]', wantPatches: '[]',
    })
    useRenderStore.getState().patch('runtime:plot.py#a|other-variant', {
      fileId: 'runtime:plot.py#a', status: 'ready', manifest: { elements: [] } as never, stale: false,
      lastPatches: '[{"x":1}]', wantPatches: '[{"x":1}]',
    })
    await act(async () => primary()!.click())
    await act(async () => useUiStore.getState().setGuideCard('card'))
    expect(panel().dataset.prepState).toBe('edit_opening')
    await act(async () => seedExactRender(target, { elements: [] } as never))
    expect(panel().dataset.prepState).toBe('edit_ready')
  })
})

describe('进入编辑遇到上次的排版还没恢复好', () => {
  afterEach(() => resetRestoreGateForTests())

  it('blocked（需要用户先处理恢复）：什么都没放进去，卡片留着，不说已进入编辑', async () => {
    await mount()
    await openWith(report(STATES.completed))
    vi.mocked(addRuntimePanelToCanvas).mockImplementation((() => null) as never)
    vi.mocked(openFastEdit).mockImplementation((() => 'blocked') as never)
    await act(async () => primary()!.click())
    expect(useUiStore.getState().guideCard).not.toBe('closed')
    expect(panel()).not.toBeNull()
    expect(useDocumentStore.getState().doc.objects).toEqual([])
  })

  /** 真实的排队：openFastEdit 的替身走真 `deferUserEntry`，落地时放面板 */
  const realDeferral = () => {
    vi.mocked(addRuntimePanelToCanvas).mockImplementation((() => null) as never)
    vi.mocked(openFastEdit).mockImplementation(((id: string) => {
      const entry = deferUserEntry(() => void putPanel(id))
      if (entry === 'run') {
        putPanel(id)
        return 'editing'
      }
      return entry === 'deferred' ? 'deferred' : 'blocked'
    }) as never)
  }

  it('deferred（恢复在路上）：入口排队期间卡片留着；入口真的落地之后才收', async () => {
    await mount()
    await openWith(report(STATES.completed))
    const token = beginRestore('pj-a')
    realDeferral()
    await act(async () => primary()!.click())
    expect(useUiStore.getState().guideCard).not.toBe('closed')
    await act(async () => {
      settleRestore(token, { state: 'ready', reason: 'empty' })
      await Promise.resolve()
      await Promise.resolve()
    })
    expect(useUiStore.getState().guideCard).toBe('closed')
    expect(useDocumentStore.getState().doc.objects).toHaveLength(1)
  })

  it('deferred 后恢复被同项目里显式打开的别的排版取代，那份排版恰好也有这张图：入口被丢掉，卡片不收、不记编辑', async () => {
    await mount()
    await openWith(report(STATES.completed))
    beginRestore('pj-a')
    realDeferral()
    await act(async () => primary()!.click())
    const other = { ...emptyProject(), canvases: [{ ...emptyProject().canvases[0], objects: [panelFor('runtime:plot.py#a')] }] }
    await act(async () => {
      await useDocumentStore.getState().switchDocument(other, 'd_other') // 显式取代（同一个项目）
      await Promise.resolve()
      await Promise.resolve()
    })
    expect(useDocumentStore.getState().doc.objects.map((o) => o.id)).toEqual([panelFor('runtime:plot.py#a').id])
    expect(useUiStore.getState().guideCard).not.toBe('closed')
  })
})

describe('多张图的结果对话框', () => {
  it('从对话框里加进画布的图也记入编辑记录（卡片才会往前走）', async () => {
    await mount()
    await openWith(report(STATES.completedMany))
    await act(async () => primary()!.click()) // 多张：打开结果对话框
    const add = Array.from(document.body.querySelectorAll('[role="dialog"] ul button')) as HTMLButtonElement[]
    expect(add.length).toBe(3)
    await act(async () => add[1].click())
    expect(vi.mocked(addRuntimePanelToCanvas)).toHaveBeenCalledWith(fig('b'))
    expect(useProjectPreparationStore.getState().entries['script:plot.py'].editing).toEqual(['runtime:plot.py#b'])
  })
})

describe('切换聚焦条目：卡体的条目局部状态不带到另一个脚本', () => {
  const otherReport = () =>
    report({
      session_id: 'psess-2',
      target: { kind: 'script', script: 'other.py', entry: '__main__', asset_id: null, stem: null },
    })
  const openOther = async () => {
    mockCreate.mockResolvedValueOnce(otherReport())
    await act(async () => {
      await useProjectPreparationStore.getState().open({ script: 'other.py' })
    })
  }
  const focusKey = async (key: string) => {
    await act(async () => useProjectPreparationStore.setState({ focus: key }))
  }

  it('A 的本地失败文案不出现在 B 上；切回 A 也是干净的', async () => {
    const workdirReport = report({
      phase: 'awaiting_configuration',
      requirements: [
        {
          id: 'workdir',
          kind: 'workdir_choice',
          code: 'workdir_confirmation_required',
          payload: {
            kind: 'workdir', code: 'workdir_confirmation_required', script: 'plot.py', reason: 'project_root_evidence',
            recommended: 'project_root',
            options: [{ mode: 'project_root', cwd_origin: 'project_root', write_mode: 'real', found: ['d.csv'], recommended: true }],
            conflicts: [], reads: ['d.csv'],
          } as never,
        },
      ],
      actions: [action('recheck')],
    })
    useEnvStore.setState({ setWorkdirMode: vi.fn().mockResolvedValue('设置失败了') })
    await mount()
    await openWith(workdirReport)
    // 先把 B 开好（报告修订相同：不靠「报告换了就收起」的 effect 蒙混），再回到 A 制造失败，最后切到 B
    await openOther()
    await focusKey('script:plot.py')
    await act(async () => primary()!.click())
    expect(panel().dataset.prepState).toBe('action_failed')
    await focusKey('script:other.py')
    expect(panel().dataset.prepSession).toBe('psess-2')
    expect(panel().dataset.prepState).not.toBe('action_failed')
    await focusKey('script:plot.py')
    expect(panel().dataset.prepState).not.toBe('action_failed')
  })

  it('A 的多图结果对话框开着，切到 B：对话框不跟过去（切回 A 也是关着的）', async () => {
    await mount()
    await openWith(report(STATES.completedMany))
    await act(async () => primary()!.click())
    expect(document.body.querySelectorAll('[role="dialog"] ul button').length).toBe(3)
    await openOther()
    expect(document.body.querySelector('[role="dialog"]')).toBeNull()
    await focusKey('script:plot.py')
    expect(document.body.querySelector('[role="dialog"]')).toBeNull()
  })

  it('采用环境挂起期间切了项目：A 的失败文案不落到卡片上', async () => {
    let release: (v: string) => void = () => {}
    useEnvStore.setState({ adoptCandidate: vi.fn(() => new Promise<string>((r) => (release = r))) as never })
    await mount()
    await openWith(report(STATES.env))
    await act(async () => primary()!.click())
    await act(async () => setCurrentProjectId('pj-b'))
    await act(async () => release('采用失败了'))
    expect(panel()?.dataset.prepState).not.toBe('action_failed')
  })

  it('结果对话框开着时切项目（组件还没卸载）：点「加入画布」不改文档', async () => {
    await mount()
    await openWith(report(STATES.completedMany))
    await act(async () => primary()!.click())
    const add = Array.from(document.body.querySelectorAll('[role="dialog"] ul button')) as HTMLButtonElement[]
    await act(async () => setCurrentProjectId('pj-b'))
    await act(async () => add[1].click())
    expect(vi.mocked(addRuntimePanelToCanvas)).not.toHaveBeenCalled()
  })
})

describe('结果对话框记录的是它刚加进画布的那个面板', () => {
  it('同一素材文档里已有一个面板（旧的就绪渲染）：新加的还在渲染时不报就绪', async () => {
    await mount()
    await openWith(report(STATES.completedMany))
    // 文档里已有 b 的旧实例（另一个渲染键），且有一份就绪的精确渲染：按素材 id 回找会选中它
    const old = { ...panelFor('runtime:plot.py#b'), id: 'panel-old-b', overrides: [{ op: 'x' }] } as unknown as PanelObject
    useDocumentStore.setState((st) => ({ doc: { ...st.doc, objects: [...st.doc.objects, old] } }))
    seedExactRender(old, { elements: [] } as never)
    const fresh = { ...panelFor('runtime:plot.py#b'), id: 'panel-new-b' } as PanelObject
    vi.mocked(addRuntimePanelToCanvas).mockImplementation((() => {
      useDocumentStore.setState((st) => ({ doc: { ...st.doc, objects: [...st.doc.objects, fresh] } }))
      return fresh
    }) as never)
    await act(async () => primary()!.click())
    const add = Array.from(document.body.querySelectorAll('[role="dialog"] ul button')) as HTMLButtonElement[]
    await act(async () => add[1].click())
    const rec = useProjectPreparationStore.getState().entries['script:plot.py'].editRenders
    expect(rec['runtime:plot.py#b']?.panelId).toBe('panel-new-b')
    await act(async () => useUiStore.getState().setGuideCard('card'))
    expect(panel().dataset.prepState).toBe('edit_opening') // 旧实例的就绪渲染不算
    await act(async () => seedExactRender(fresh, { elements: [] } as never))
    expect(panel().dataset.prepState).toBe('edit_ready')
  })
})

describe('进入编辑：清单请求在途时连点', () => {
  it('两次点击各自等到清单之后，同一张图只加一个面板（补审 #914 r4236701619）', async () => {
    await mount()
    await openWith(report(STATES.completed))
    let release!: () => void
    const loading = new Promise<void>((r) => (release = r)) // 两次点击等同一个在途请求
    useRuntimeAssetStore.setState({ assets: [], loadAssets: () => loading })
    const button = primary()!
    await act(async () => {
      button.click()
      button.click()
    })
    await act(async () => release())
    expect(vi.mocked(addRuntimePanelToCanvas)).not.toHaveBeenCalled()
    // 第一次点击带描述符放进一个面板；第二次已经在文档里找到它，不再带描述符
    const calls = vi.mocked(openFastEdit).mock.calls
    expect(calls[0][1]?.descriptor).toEqual(fig('a'))
    expect(calls[1][1]?.descriptor).toBeUndefined()
  })
})

describe('进入编辑：挂起期间换了项目，续延整个丢弃', () => {
  it('loadAssets 之后项目已换：不把 A 的面板加进 B 的版面，也不打开编辑、不记录', async () => {
    await mount()
    await openWith(report(STATES.completed))
    let release!: () => void
    useRuntimeAssetStore.setState({ assets: [], loadAssets: () => new Promise<void>((r) => (release = r)) })
    const click = act(async () => primary()!.click())
    setCurrentProjectId('pj-b') // 切项目（与 `clear()` 同一时刻的后果）
    useProjectPreparationStore.getState().clear()
    await act(async () => release())
    await click
    expect(vi.mocked(addRuntimePanelToCanvas)).not.toHaveBeenCalled()
    expect(vi.mocked(openFastEdit)).not.toHaveBeenCalled()
  })
})

describe('运行时 input：同一请求只有一个展示面', () => {
  const req = { id: 'req-1', script: 'plot.py', index: 1, input_kind: 'input' as const, prompt: 'mode?', stdout_tail: 'a\nb\nc\n0) raw\n1) smooth' }

  it('卡片展示那一问时认领，原对话框让开；只留最近 4 行输出、提示加粗；收起卡片就放手，对话框接着显示同一问', async () => {
    await mount(true)
    useScriptInputStore.getState().onRequested(req)
    await openWith(report(STATES.input))
    expect(panel().querySelector('[data-prep-line]')?.textContent).toBe('脚本在等你回答')
    expect(panel().querySelector('[data-prep-input]')).not.toBeNull()
    expect(useScriptInputStore.getState().presenters).toEqual(['prep-card'])
    const out = panel().querySelector('[data-script-input-output]')!
    expect(out.textContent).toBe('b\nc\n0) raw\n1) smooth\nmode?')
    expect(out.querySelector('b')?.textContent).toBe('mode?')
    expect(primary()?.textContent).toBe('回答')
    // 只有一个答题框（卡片里那个），对话框没出来
    expect(document.querySelectorAll('[data-script-input-answer]').length).toBe(1)
    await act(async () => (panel().querySelector('[data-prep-close]') as HTMLButtonElement).click())
    expect(useScriptInputStore.getState().presenters).toEqual([])
    expect(useScriptInputStore.getState().queue[0]?.id).toBe('req-1') // 收起不取消那一问
    expect(document.querySelectorAll('[data-script-input-answer]').length).toBe(1) // 现在是对话框里那个
    expect(panel()).toBeNull()
  })

  it('缩成角标时脚本发问：卡片自动展开', async () => {
    await mount(true)
    useUiStore.setState({ guideCard: 'card' })
    await openWith(report(STATES.running))
    await act(async () => primary()!.click()) // 放到后台
    expect(useUiStore.getState().guideCard).toBe('pill')
    useScriptInputStore.getState().onRequested(req)
    vi.mocked(fetchPreparationSession).mockResolvedValueOnce(report({ ...STATES.input, observation_seq: 5 }))
    await act(async () => {
      await useProjectPreparationStore.getState().refresh('script:plot.py')
    })
    expect(useUiStore.getState().guideCard).toBe('card')
    expect(panel().querySelector('[data-prep-input]')).not.toBeNull()
  })

  it('输出片段以提示结尾时提示只出现一次（加粗那一行）', async () => {
    await mount(true)
    useScriptInputStore.getState().onRequested({ ...req, stdout_tail: '0) raw\n1) smooth\nmode? ' })
    await openWith(report(STATES.input))
    expect(panel().querySelector('[data-script-input-output]')!.textContent).toBe('0) raw\n1) smooth\nmode?')
  })

  it('在答题框里填的就是交出去的那一个（「回答」与框共用同一份）', async () => {
    await mount(true)
    useScriptInputStore.getState().onRequested(req)
    const submit = vi.fn().mockResolvedValue(undefined)
    useScriptInputStore.setState({ submit })
    await openWith(report(STATES.input))
    const box = panel().querySelector('[data-script-input-answer]') as HTMLInputElement
    await act(async () => {
      const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
      set.call(box, '1')
      box.dispatchEvent(new Event('input', { bubbles: true }))
    })
    await act(async () => primary()!.click())
    expect(submit).toHaveBeenCalledWith('1')
  })

  it('队首不是报告里的那一问（别的脚本在问）：卡片不认领，原对话框照旧', async () => {
    await mount(true)
    useScriptInputStore.getState().onRequested({ ...req, id: 'other', script: 'other.py' })
    await openWith(report(STATES.input))
    expect(panel().querySelector('[data-prep-input]')).toBeNull()
    expect(useScriptInputStore.getState().presenters).toEqual([])
  })

  it('队首是别的脚本的问：本卡不渲染「回答」与 ⋯（点了会答到别人头上），也不露别人的输出', async () => {
    await mount(true)
    useScriptInputStore.getState().onRequested({ ...req, id: 'other', script: 'other.py', stdout_tail: 'OTHER-OUTPUT' })
    await openWith(report(STATES.input))
    expect(panel().querySelector('[data-prep-more]')).toBeNull()
    expect(primary()?.textContent).not.toBe('回答')
    expect(panel().textContent).not.toContain('OTHER-OUTPUT')
  })
})

/* ------------------------------------------------------------------ Import Origin（PR6） */

describe('Import Origin：五种展示态，每种 × 每个语种都是「一句话 + 至多一个主按钮」', () => {
  // 状态名 → 展示态的名字；卡片的状态由后端报告选（`prepView`），`data-origin-view` 只是它的名字
  const FIVE: [string, keyof typeof STATES, (typeof ORIGIN_VIEWS)[number]][] = [
    ['已就绪', 'ready', 'ready'],
    ['发现更合适的环境', 'env', 'better_env'],
    ['可以自动修复', 'deps', 'auto_fix'],
    ['需要确认', 'originConfirm', 'needs_confirm'],
    ['尚无法确定', 'originUnknown', 'undetermined'],
  ]
  it('五种名字一个不少', () => {
    expect(new Set(FIVE.map((f) => f[2]))).toEqual(new Set(ORIGIN_VIEWS))
  })
  for (const lng of LOCALES) {
    for (const [name, key, view] of FIVE) {
      it(`${lng} · ${name}`, async () => {
        await i18n.changeLanguage(lng)
        await mount()
        await openWith(report(STATES[key]))
        expect(panel().dataset.originView).toBe(view)
        // 逐块钉死：看得见的文字块只有「标题那一句」「详情」「稍后 / 主按钮」，多一块说明就红
        const blocks = visibleBlocks(panel()).map((b) => b.text)
        const line = panel().querySelector('[data-prep-line]')!.textContent!
        expect(blocks.filter((b) => b === line)).toHaveLength(1)
        expect(visibleSentenceCount(panel(), lng)).toBeLessThanOrEqual(1)
        expect(visiblePrimaryButtons(panel())).toBeLessThanOrEqual(1)
        expect(panel().querySelector('[data-prep-details]')).toBeNull()
        // 解释、计数、原因全收在详情里：默认可见区里没有原因句 / 计数行
        expect(panel().querySelector('[data-origin-reasons]')).toBeNull()
        expect(panel().querySelector('[data-origin-reason]')).toBeNull()
      })
    }
  }
})

describe('Import Origin：来源事实决定准备卡说什么', () => {
  it('需要确认：不说「可以运行了」；一句话数的是带预填的个数；主按钮「确认包名」，没有运行按钮', async () => {
    await mount()
    await openWith(report(STATES.originConfirm))
    expect(panel().dataset.prepState).toBe('origin_confirm')
    expect(panel().querySelector('[data-prep-line]')?.textContent).toBe('有 1 个库要你确认包名')
    expect(primary()?.dataset.prepPrimary).toBe('confirm_origins')
    expect(primary()?.textContent).toBe('确认包名')
    expect(panel().querySelector('[data-prep-primary="run"]')).toBeNull()
  })

  it('尚无法确定：不说「可以运行了」；主按钮是报告里的运行（改口「仍然运行」）；没有 run 动作就没有主按钮', async () => {
    await mount()
    await openWith(report(STATES.originUnknown))
    expect(panel().dataset.prepState).toBe('origin_unknown')
    expect(panel().querySelector('[data-prep-line]')?.textContent).toBe('有 3 个库的来源还没法确定')
    expect(primary()?.dataset.prepPrimary).toBe('run')
    expect(primary()?.textContent).toBe('仍然运行')
    await openWith(report({ ...STATES.originUnknown, actions: [action('recheck')], observation_seq: 2 }))
    expect(primary()).toBeNull()
  })

  it('弹窗提示与可确认的来源并存：主按钮是「确认包名」（弹窗提示收进详情）；只有弹窗提示时照旧', async () => {
    await mount()
    await openWith(report({ ...STATES.dialogFile, ...STATES.originConfirm }))
    expect(panel().dataset.prepState).toBe('origin_confirm')
    expect(primary()?.dataset.prepPrimary).toBe('confirm_origins')
    expect(visiblePrimaryButtons(panel())).toBe(1)
    await toggleDetails()
    expect(details()?.querySelector('[data-prep-dialog]')).not.toBeNull()
    await openWith(report({ ...STATES.dialogFile, observation_seq: 2 }))
    expect(panel().dataset.prepState).toBe('gui_dialog')
    expect(primary()?.textContent).toBe('仍然运行')
  })

  it('英文：复数跟着个数走', async () => {
    await i18n.changeLanguage('en-US')
    await mount()
    await openWith(report(STATES.originConfirm))
    expect(panel().querySelector('[data-prep-line]')?.textContent).toBe('1 library needs a package name from you')
    await openWith(
      report({
        checks: originChecks({ count: 4, by_reason: { unverified: 4 }, confirmable: 3 }),
        observation_seq: 2,
      }),
    )
    expect(panel().querySelector('[data-prep-line]')?.textContent).toBe('3 libraries need a package name from you')
  })

  it('没有来源事实 / 计数为 0 / 形状不对：和以前一字不差（可以运行了）', async () => {
    await mount()
    for (const [i, checks] of [
      [{ id: 'target', status: 'ok' }, { id: 'dependencies', status: 'ok' }],
      originChecks({ count: 0, by_reason: {} }),
      [{ id: 'dependencies', status: 'ok', detail: { origins: 'oops' } }],
    ].entries()) {
      await openWith(report({ checks: checks as never, observation_seq: i + 1 }))
      expect(panel().dataset.prepState).toBe('ready')
      expect(panel().querySelector('[data-prep-line]')?.textContent).toBe('可以运行了')
    }
  })

  it('来源事实不改变别的状态：缺组件的卡仍是「安装」，来源只多一行详情', async () => {
    await mount()
    await openWith(
      report({
        ...STATES.deps,
        checks: originChecks({ count: 1, by_reason: { module_origin_ambiguous: 1 } }),
      }),
    )
    expect(panel().dataset.prepState).toBe('deps')
    expect(primary()?.dataset.prepPrimary).toBe('prepare_dependencies')
    await toggleDetails()
    expect(details()?.querySelector('[data-origin-reason="module_origin_ambiguous"]')).not.toBeNull()
  })

  it('详情：每个原因码一句人话 + 个数；不出 import 名、不出术语；codes 不原样甩给用户', async () => {
    await mount()
    await openWith(report(STATES.originUnknown))
    await toggleDetails()
    const reasons = [...details()!.querySelectorAll('[data-origin-reason]')].map((el) => el.getAttribute('data-origin-reason'))
    expect(reasons).toEqual(['editable_dependency_not_reproducible', 'module_origin_ambiguous'])
    const text = details()!.querySelector('[data-origin-reasons]')!.textContent!
    expect(text).toContain('2')
    expect(text).toContain('好几个包都能提供它')
    expect(text).not.toMatch(/editable|site-packages|dist-info|隔离环境|module_origin_ambiguous/)
  })

  it('准备卡的角标（素材库行 / 缩成角标）也不说「待运行」', async () => {
    await mount()
    useUiStore.setState({ guideCard: 'card' })
    await openWith(report(STATES.originConfirm))
    await act(async () => (panel().querySelector('[data-prep-later]') as HTMLButtonElement).click())
    expect(host.querySelector('[data-prep-pill]')?.textContent).toContain('待确认包名')
  })

  it('准备卡的角标：来源待定', async () => {
    await mount()
    useUiStore.setState({ guideCard: 'card' })
    await openWith(report(STATES.originUnknown))
    await act(async () => (panel().querySelector('[data-prep-later]') as HTMLButtonElement).click())
    expect(host.querySelector('[data-prep-pill]')?.textContent).toContain('来源待定')
  })
})

describe('Import Origin：「确认包名」打开框', () => {
  const planOffer = (): DependencyPreparationOffer =>
    ({
      code: 'dependency_preparation_required',
      script: 'plot.py',
      plan: {
        status: 'nothing_needed',
        script: 'plot.py',
        requirements: [],
        unknown: ['lab_tools'],
        origins: [
          { import_name: 'lab_tools', bucket: 'unknown', distribution: '', reason: 'distribution_unresolved', resolution_status: 'unresolved', distribution_status: '', origin_kind: 'unresolved', provenance: '', candidates: [], coverage: '', suggested_distribution: 'lab-tools' },
        ],
      },
      target_kind: 'tavotto_managed',
      targets: [],
      rounds_remaining: 3,
      skipped: false,
    }) as never

  beforeEach(() => {
    vi.mocked(fetchDependencyOffer).mockReset()
    useDepRepairStore.setState({ originDraft: null, originPlan: null, busy: false })
    useEnvStore.setState({ originConfirmation: null })
  })

  it('点主按钮：取一份计划载荷（名字在载荷里），设草稿 = 后端预填，把载荷交给框；卡片本身没有任何输入框', async () => {
    await mount()
    await openWith(report(STATES.originConfirm))
    vi.mocked(fetchDependencyOffer).mockResolvedValueOnce({ offer: planOffer(), groups: [], rounds_remaining: 3 })
    await act(async () => primary()!.click())
    expect(fetchDependencyOffer).toHaveBeenCalledWith('plot.py')
    expect(useEnvStore.getState().originConfirmation?.script).toBe('plot.py')
    expect(useDepRepairStore.getState().originDraft).toEqual({
      script: 'plot.py',
      names: ['lab_tools'],
      values: { lab_tools: 'lab-tools' },
    })
    expect(panel().querySelector('[data-origin-input], input[type="text"]')).toBeNull()
  })

  it('取不到载荷：卡片上说一句人话，不开框', async () => {
    await mount()
    await openWith(report(STATES.originConfirm))
    vi.mocked(fetchDependencyOffer).mockResolvedValueOnce({ offer: null, groups: [], rounds_remaining: 3 })
    await act(async () => primary()!.click())
    expect(panel().querySelector('[data-prep-line]')?.textContent).toBe('暂时读不到包名信息，请稍后再试。')
    expect(useEnvStore.getState().originConfirmation).toBeNull()
  })

  it('取载荷挂起期间换了项目：整个丢弃，不把 A 的预填落进 B', async () => {
    await mount()
    await openWith(report(STATES.originConfirm))
    let resolve!: (v: Awaited<ReturnType<typeof fetchDependencyOffer>>) => void
    vi.mocked(fetchDependencyOffer).mockReturnValueOnce(new Promise((r) => (resolve = r)))
    const click = act(async () => primary()!.click())
    setCurrentProjectId('pj-b')
    await act(async () => resolve({ offer: planOffer(), groups: [], rounds_remaining: 3 }))
    await click
    expect(useEnvStore.getState().originConfirmation).toBeNull()
    expect(useDepRepairStore.getState().originDraft).toBeNull()
  })
})

describe('准备出错：后端本地详情接到已有恢复动作（P05a，AC-26）', () => {
  it('参数被脚本自己的解析器拒绝：说"不认你填的 N 个参数"，参数编辑器直接展开，详情有参数问题类别', async () => {
    await mount()
    await openWith(report(fromBackend('invalid_argv')))
    expect(panel().dataset.prepState).toBe('needs_args')
    expect(panel().querySelector('[data-prep-line]')?.textContent).toBe('脚本不认你填的 2 个参数')
    expect(primary()?.dataset.prepPrimary).toBe('reopen')
    await toggleDetails()
    expect(details()!.querySelector('[data-prep-row="parse-kind"]')?.textContent).toContain('有参数的值不合法')
  })

  it('没给参数脚本就要参数：仍是"脚本要运行参数"，类别是缺必填项', async () => {
    await mount()
    await openWith(report(fromBackend('missing_argv')))
    expect(panel().dataset.prepState).toBe('needs_args')
    expect(panel().querySelector('[data-prep-line]')?.textContent).toBe('脚本要运行参数')
    await toggleDetails()
    expect(details()!.querySelector('[data-prep-row="parse-kind"]')?.textContent).toContain('缺少必填参数')
  })

  it('无人应答的 input：一句话 + 再运行一次，脚本的提问在详情里', async () => {
    await mount()
    await openWith(report(fromBackend('unanswered_input')))
    expect(panel().dataset.prepState).toBe('input_unanswered')
    expect(primary()?.textContent).toBe('再试一次')
    expect(primary()?.dataset.prepPrimary).toBe('run')
    await toggleDetails()
    expect(details()!.querySelector('[data-prep-row="input-prompt"]')?.textContent).toContain('Enter output directory:')
  })

  it('口令类的无人应答：说明口令不会被记住', async () => {
    await mount()
    const g = fromBackend('unanswered_input')
    const err = { ...errorReports.unanswered_input.result.error, params: { prompt: 'Password: ', reason: 'secret_required' } }
    await openWith(report({ ...g, result: { status: 'error', error: err } as never }))
    expect(panel().querySelector('[data-prep-line]')?.textContent).toContain('口令不会被记住')
  })

  it('getpass 首问无人应答（理由不是 secret_required）：仍按口令说明不会被记住', async () => {
    await mount()
    const g = fromBackend('unanswered_input')
    const err = {
      ...errorReports.unanswered_input.result.error,
      params: { prompt: 'Password: ', reason: 'no_interactive_client', input_kind: 'getpass' },
    }
    await openWith(report({ ...g, result: { status: 'error', error: err } as never }))
    expect(panel().querySelector('[data-prep-line]')?.textContent).toContain('口令不会被记住')
  })

  it('getpass 等到超时：仍按口令说明不会被记住', async () => {
    await mount()
    const g = fromBackend('unanswered_input')
    const err = {
      ...errorReports.unanswered_input.result.error,
      code: 'script_input_timeout',
      params: { prompt: 'Password: ', input_kind: 'getpass' },
    }
    await openWith(report({ ...g, result: { status: 'error', error: err } as never }))
    expect(panel().querySelector('[data-prep-line]')?.textContent).toContain('口令不会被记住')
  })

  it('读不到数据：主按钮仍是已有的"指认数据"（回答走原路径选择），原文 traceback 在折叠里', async () => {
    await mount()
    await openWith(report(fromBackend('missing_input_file')))
    expect(panel().dataset.prepState).toBe('missing_data')
    expect(primary()?.dataset.prepPrimary).toBe('missing_input')
    await toggleDetails()
    expect(details()!.querySelector('[data-prep-traceback]')).not.toBeNull()
  })
})

describe('沙盒里落空、脚本目录里找得到的引导（#1254）', () => {
  const hint = {
    kind: 'workdir',
    code: 'workdir_confirmation_required',
    script: 'plot.py',
    reason: 'script_dir_evidence',
    recommended: 'project',
    options: [],
    conflicts: [],
    reads: [],
    probes: ['data/run_*.txt'],
    message: '',
    source: 'runtime',
  }
  const withHint = (extra: object = {}) =>
    report({
      ...STATES.noFigure,
      requirements: [
        { id: 'data', kind: 'workdir_hint', code: 'workdir_hint', origin: 'last_attempt', blocking: false, payload: hint as never },
      ],
      ...extra,
    })
  const chooseText = () => translate('engine.workdirChooseButton', { ns: 'errors' })

  it('跑完没出图且报告带 workdir_hint：卡片多一个「选择运行目录…」入口（主按钮与一句话不变）', async () => {
    await mount()
    await openWith(withHint())
    expect(panel().textContent).toContain(chooseText())
    expect(panel().querySelectorAll('[data-prep-primary]').length).toBeLessThanOrEqual(1)
  })

  it('报告里没有 workdir_hint：不出现', async () => {
    await mount()
    await openWith(report(STATES.noFigure))
    expect(panel().textContent).not.toContain(chooseText())
  })

  it('点入口打开的是首开同一个确认框（载荷原样）', async () => {
    await mount()
    const request = vi.fn()
    useEnvStore.setState({ requestWorkdirConfirmation: request } as never)
    await openWith(withHint())
    const button = [...panel().querySelectorAll('button')].find((b) => b.textContent === chooseText())!
    await act(async () => button.click())
    expect(request.mock.calls[0][0]).toEqual(hint)
  })
})

describe('打开并运行 + 后端会话内回退的呈现（V2 PR4）', () => {
  it('ready 且报告里有 run_when_ready：主按钮是「打开并运行」，认领它并回显授权摘要；永远不发 execution', async () => {
    await mount()
    await openWith(report(STATES.readyArm))
    expect(primary()?.dataset.prepPrimary).toBe('run_when_ready')
    expect(primary()?.textContent).toBe('打开并运行')
    mockAct.mockResolvedValueOnce({ claimed: true, report: report({ phase: 'running', observation_seq: 2, actions: [action('cancel')] }) })
    await act(async () => primary()!.click())
    expect(mockAct).toHaveBeenCalledWith(
      'psess-1',
      { action_id: 'act-run_when_ready', expected_config_revision: 1, impact_digest: 'grant-1' },
      'pj-a',
      expect.any(AbortSignal),
    )
    expect(JSON.stringify(mockAct.mock.calls)).not.toContain('execution')
    expect(JSON.stringify(mockCreate.mock.calls)).not.toContain('execution')
  })

  it('老后端 / 杀开关（报告里没有 run_when_ready）：回到「运行」', async () => {
    await mount()
    await openWith(report())
    expect(primary()?.dataset.prepPrimary).toBe('run')
    expect(primary()?.textContent).toBe('运行')
  })

  it('缺组件：打开并运行取代「安装」（装完自动接着运行）；要改用户自己环境的安装不在授权合同里，仍是「安装」', async () => {
    await mount()
    await openWith(report(STATES.depsArm))
    expect(panel().dataset.prepState).toBe('deps')
    expect(primary()?.dataset.prepPrimary).toBe('run_when_ready')
    expect(primary()?.textContent).toBe('打开并运行')
    await act(async () => root.unmount())
    host.remove()
    useProjectPreparationStore.getState().clear()
    await mount()
    await openWith(report(STATES.depsArmUserEnv))
    expect(primary()?.dataset.prepPrimary).toBe('prepare_dependencies')
    expect(primary()?.textContent).toBe('安装')
  })

  it('运行时才发现缺包（已执行过）：没有 run_when_ready，仍是逐次确认的「安装」', async () => {
    await mount()
    await openWith(report({ ...STATES.depsRuntime, actions: [...(STATES.depsRuntime.actions ?? []), action('run_when_ready')] }))
    expect(primary()?.dataset.prepPrimary).toBe('prepare_dependencies')
  })

  describe('safe 被自己的守卫拦下、后端没有回退（file_ops_blocked 的死胡同只剩这一种成因）', () => {
    it.each([
      ['fileOpsBlockedSafeOnly', '只用安全运行'],
      ['fileOpsBlockedOff', '原样运行被关掉了'],
    ] as const)('%s：按账本里的闭集理由说一句具体的话，没有主按钮', async (name, snippet) => {
      await mount()
      await openWith(report(STATES[name]))
      expect(panel().dataset.prepState).toBe('file_ops_blocked')
      expect(panel().querySelector('[data-prep-line]')?.textContent).toContain(snippet)
      expect(primary()).toBeNull()
    })

    it('没有账本（老后端）：保持原来的一句话', async () => {
      await mount()
      await openWith(report(STATES.fileOpsBlocked))
      expect(panel().querySelector('[data-prep-line]')?.textContent).toContain('没有出图')
      expect(primary()).toBeNull()
    })

    it('运行记录在默认折叠的详情里：理由是闭集码翻成的人话', async () => {
      await mount()
      await openWith(report(STATES.fileOpsBlockedSafeOnly))
      expect(panel().querySelector('[data-prep-history]')).toBeNull()
      await toggleDetails()
      const rows = [...(details()?.querySelectorAll('[data-prep-row="history"]') ?? [])]
      expect(rows).toHaveLength(1)
      expect(rows[0].textContent).toContain('安全运行')
      expect(rows[0].textContent).toContain('项目设为只用安全运行，没有换')
    })

    it('英文界面同样是一句话、没有泄漏中文', async () => {
      await i18n.changeLanguage('en-US')
      await mount()
      await openWith(report(STATES.fileOpsBlockedSafeOnly))
      const line = panel().querySelector('[data-prep-line]')!.textContent ?? ''
      expect(line).toContain('safe mode only')
      expect(line).not.toMatch(/[一-鿿]/)
    })
  })

  describe('回退也失败了', () => {
    it('一句话说明已经换过 + 一个主按钮（再试一次）；运行记录两条在详情里，原因码不进可见区', async () => {
      await mount()
      await openWith(report(STATES.failedAfterFallback))
      expect(panel().dataset.prepState).toBe('failed')
      expect(panel().querySelector('[data-prep-line]')?.textContent).toBe('安全运行不能照常执行，改用原样运行后又出错了')
      expect(visiblePrimaryButtons(panel())).toBe(1)
      expect(primary()?.textContent).toBe('再试一次')
      expect(panel().textContent).not.toContain('safe_file_operation_blocked')
      await toggleDetails()
      const rows = [...(details()?.querySelectorAll('[data-prep-row="history"]') ?? [])].map((r) => r.textContent)
      expect(rows).toHaveLength(2)
      expect(rows[0]).toContain('第 1 次')
      expect(rows[0]).toContain('被拦下')
      expect(rows[1]).toContain('第 2 次')
      expect(rows[1]).toContain('原样运行')
      expect(rows[1]).toContain('出错')
    })

    it('只看本次运行：历史里更早一次运行回退过，这次是普通 safe 失败 → 不说「换过」（Codex #1044 r4271063109）', async () => {
      await mount()
      await openWith(
        report({
          ...STATES.failed,
          provider: { plan_id: 'p', attempt_id: 'prep-3', attempts: 3, dependency: null },
          attempt_history: [
            HIST_BLOCKED({}),
            HIST_BLOCKED({ seq: 2, attempt_id: 'prep-2', profile: 'native', changed: ['profile'], outcome: 'succeeded', code: '' }),
            HIST_BLOCKED({ run: 2, seq: 1, attempt_id: 'prep-3', outcome: 'failed', code: 'script_error' }),
          ],
        }),
      )
      expect(panel().querySelector('[data-prep-line]')?.textContent).toBe('运行出错了')
    })

    it('没换过的普通失败不说「换过」，也不显示运行记录（只有一次且没有理由，没有可说的）', async () => {
      await mount()
      await openWith(report({ ...STATES.failed, attempt_history: [HIST_BLOCKED({ outcome: 'failed', code: 'script_error' })] }))
      expect(panel().querySelector('[data-prep-line]')?.textContent).toBe('运行出错了')
      await toggleDetails()
      expect(details()?.querySelector('[data-prep-history]')).toBeNull()
    })

    it('后端以后新增的原因码（旧前端不认识）原样显示，不丢也不猜', async () => {
      await mount()
      await openWith(
        report({ ...STATES.fileOpsBlockedSafeOnly, attempt_history: [HIST_BLOCKED({ declined: 'brand_new_reason' })] }),
      )
      await toggleDetails()
      expect(details()?.querySelector('[data-prep-row="history"]')?.textContent).toContain('brand_new_reason')
    })
  })

  it('取消之后「再运行」也走 run_when_ready（报告里有就用），说的字不变', async () => {
    await mount()
    await openWith(
      report({
        phase: 'cancelled',
        outcome: { kind: 'cancelled' },
        actions: [action('run'), action('run_when_ready', { impact_digest: 'grant-1' }), action('recheck')],
      }),
    )
    expect(primary()?.dataset.prepPrimary).toBe('run_when_ready')
    expect(primary()?.textContent).toBe('再运行')
  })
})

describe('沿用上次成功的参数的呈现（#982）', () => {
  const reused = (over: Partial<PreparationReport> = {}) =>
    report({ target: { kind: 'script', script: 'plot.py', entry: '__main__', asset_id: null, stem: null, argv_count: 2 }, ...over })
  const openReuse = async (r: PreparationReport) => {
    mockCreate.mockResolvedValueOnce(r)
    await act(async () => {
      await useProjectPreparationStore.getState().open({ script: 'plot.py', reuse_last_success: true })
    })
  }

  it('ready 卡的参数行说「沿用上次成功的 2 个参数」（只有个数，值不显示），旁边一个文字钮「不沿用」，主按钮仍只有一个', async () => {
    await mount()
    await openReuse(reused(STATES.readyArm as Partial<PreparationReport>))
    expect(panel().dataset.prepState).toBe('ready')
    expect(panel().querySelector('[data-prep-args-summary]')?.textContent).toBe('沿用上次成功的 2 个参数')
    expect(panel().querySelector('[data-prep-reuse-clear]')?.textContent).toBe('不沿用')
    expect(visiblePrimaryButtons(panel())).toBe(1)
    expect(visibleSentenceCount(panel(), 'zh-CN')).toBeLessThanOrEqual(1)
    expect(panel().textContent).not.toContain('参数已填好') // 沿用不是用户改了草稿
  })

  it('没沿用（报告里没有个数）：不显示清除钮', async () => {
    await mount()
    await openReuse(report(STATES.readyArm as Partial<PreparationReport>))
    expect(panel().querySelector('[data-prep-reuse-clear]')).toBeNull()
    expect(panel().querySelector('[data-prep-args-summary]')?.textContent).toBe('不带参数')
  })

  it('点「不沿用」：按无参数重新检查（请求里没有 reuse_last_success），不运行', async () => {
    await mount()
    await openReuse(reused())
    mockCreate.mockResolvedValueOnce(report({ session_id: 'psess-2' }))
    await act(async () => (panel().querySelector('[data-prep-reuse-clear]') as HTMLButtonElement).click())
    expect(mockCreate).toHaveBeenLastCalledWith({ script: 'plot.py' }, 'pj-a', expect.anything())
    expect(mockAct).not.toHaveBeenCalled()
    expect(panel().querySelector('[data-prep-reuse-clear]')).toBeNull()
  })

  it('没能建会话时点「再试一次」：重新打开仍沿用（目标构造点只有 scriptTarget，不翻转成无参数）', async () => {
    await mount()
    mockCreate.mockRejectedValueOnce(new ApiError('x', 404, { code: 'script_not_found' }))
    await act(async () => {
      await useProjectPreparationStore.getState().open({ script: 'plot.py', reuse_last_success: true })
    })
    expect(panel().dataset.prepState).toBe('check_failed')
    mockCreate.mockResolvedValueOnce(reused())
    await act(async () => primary()!.click())
    expect(mockCreate).toHaveBeenLastCalledWith({ script: 'plot.py', reuse_last_success: true }, 'pj-a', expect.anything())
  })

  it('英文界面同样一句话、没有泄漏中文', async () => {
    await i18n.changeLanguage('en-US')
    await mount()
    await openReuse(reused())
    expect(panel().querySelector('[data-prep-args-summary]')?.textContent).toBe('Reusing the last 2 successful arguments')
    expect(panel().querySelector('[data-prep-reuse-clear]')?.textContent).toBe("Don't reuse")
    expect(panel().textContent).not.toMatch(/[一-鿿]/)
  })

  it('详情里的「参数」行同样说沿用的个数，不说「不带参数」', async () => {
    await mount()
    await openReuse(reused())
    await toggleDetails()
    expect(details()?.querySelector('[data-prep-row="args"]')?.textContent).toContain('沿用上次成功的 2 个参数')
  })
})

describe('停止运行（V2 PR4 第二轮）：用户不感知运行层，只有「停止运行」', () => {
  const runningNative = (over: Partial<PreparationReport> = {}) =>
    report({
      phase: 'running',
      outcome: { kind: 'running' },
      actions: [action('cancel')],
      provider: { plan_id: 'p', attempt_id: 'plan-2', attempts: 2, dependency: null },
      ...over,
    })
  const putNative = () =>
    useNativeSessionStore.setState({
      sessions: {
        'native-owned-1': {
          session_id: 'native-owned-1', owner: 'desktop', launch_id: 'plan-2', state: 'running_script',
        } as never,
      },
    })
  beforeEach(() => vi.mocked(cancelNativeSession).mockClear())

  it('卡上「停止运行」：这次运行有桌面拥有的会话（launch_id 对得上计划）→ 调 native 取消端点，不再另发准备动作', async () => {
    await mount()
    putNative()
    await openWith(runningNative())
    const stop = panel().querySelector('[data-prep-stop]') as HTMLButtonElement
    expect(stop.textContent).toBe('停止运行')
    await act(async () => stop.click())
    expect(cancelNativeSession).toHaveBeenCalledWith('native-owned-1')
    expect(mockAct).not.toHaveBeenCalled()
  })

  it('没有对得上的桌面会话（safe 阶段 / 别的计划的会话）：走准备会话的 cancel 动作', async () => {
    await mount()
    putNative()
    await openWith(runningNative({ provider: { plan_id: 'p', attempt_id: 'plan-OTHER', attempts: 1, dependency: null } }))
    mockAct.mockResolvedValueOnce({ claimed: true, report: runningNative() })
    await act(async () => (panel().querySelector('[data-prep-stop]') as HTMLButtonElement).click())
    expect(cancelNativeSession).not.toHaveBeenCalled()
    expect(mockAct).toHaveBeenCalledTimes(1)
  })

  // Codex #1044 r4280206157：取消请求失败不许被吞——按钮置忙、被拒说一句、连不上走离线态
  it('停止请求被后端拒绝：请求中按钮置忙，失败后卡上有后端稳定码对应的一句话，按钮恢复可再点', async () => {
    await mount()
    putNative()
    await openWith(runningNative())
    let reject: (e: unknown) => void = () => {}
    vi.mocked(cancelNativeSession).mockImplementationOnce(() => new Promise((_, r) => (reject = r)))
    const stop = () => panel().querySelector('[data-prep-stop]') as HTMLButtonElement
    await act(async () => stop().click())
    expect(stop().disabled).toBe(true)
    vi.mocked(fetchPreparationSession).mockResolvedValueOnce(runningNative({ observation_seq: 9 })) // 被拒之后重读报告
    await act(async () => reject(new ApiError('x', 409, { code: 'script_not_found' })))
    expect(panel().dataset.prepState).toBe('rejected') // 一句话：这次没成（详情里是稳定码对应的原因）
    expect(stop().disabled).toBe(false)
  })

  it('停止请求连接失败：卡片转为连不上的状态（继续补拉），不再无反应', async () => {
    await mount()
    putNative()
    await openWith(runningNative())
    vi.mocked(cancelNativeSession).mockRejectedValueOnce(new TypeError('network'))
    await act(async () => (panel().querySelector('[data-prep-stop]') as HTMLButtonElement).click())
    expect(panel().dataset.prepConnection).toBe('lost')
  })

  it('角标上运行中也有「停止运行」（同一条路径）；文案里没有 native / tavotto run', async () => {
    await mount()
    putNative()
    await openWith(runningNative())
    useUiStore.setState({ guideCard: 'pill' })
    await act(async () => {})
    const pillStop = host.querySelector('[data-prep-pill] [data-prep-pill-stop]') as HTMLButtonElement
    expect(pillStop.textContent).toBe('停止运行')
    expect(host.querySelector('[data-prep-pill]')!.textContent).not.toMatch(/native|tavotto run/i)
    await act(async () => pillStop.click())
    expect(cancelNativeSession).toHaveBeenCalledWith('native-owned-1')
  })

  it('已结束的桌面会话不拿来取消', async () => {
    await mount()
    useNativeSessionStore.setState({
      sessions: { 'native-owned-1': { session_id: 'native-owned-1', owner: 'desktop', launch_id: 'plan-2', state: 'ended' } as never },
    })
    await openWith(runningNative())
    mockAct.mockResolvedValueOnce({ claimed: true, report: runningNative() })
    await act(async () => (panel().querySelector('[data-prep-stop]') as HTMLButtonElement).click())
    expect(cancelNativeSession).not.toHaveBeenCalled()
  })
})

describe('智能修复（ADR 0148）：每张失败卡都有「✦ 使用你的 AI 来修复」', () => {
  const aiBtn = () => panel()?.querySelector('[data-prep-ai-fix]') as HTMLButtonElement | null
  const mockFix = vi.mocked(requestAiFix)
  const usable = (on: boolean) =>
    ({ agents: [{ id: 'claude', usable: on }], endpoints: [], presets: [], checked_at_ms: 0 }) as never

  beforeEach(() => {
    mockFix.mockReset()
    useAiFixStore.getState().reset()
    useAiStore.setState({ caps: usable(false), loadCaps: vi.fn(async () => {}) })
    useUiStore.setState({ settingsOpen: false, settingsSection: null })
    useDiagSendStore.setState({ capability: { enabled: true } as never })
    useOnboardingStore.setState({ status: 'idle' } as never)
  })
  afterEach(() => {
    useDiagSendStore.setState({ capability: null })
    useAiFixStore.getState().reset()
    useAiStore.setState({ caps: null })
  })

  it('失败卡都有这颗按钮（蓝色描边用语义 token，不是主按钮）；ready / 运行中 / 成功没有', async () => {
    const withBtn = ['errPlain', 'errRemedyAi', 'errRemedyConnect', 'errRemedyFeedback', 'depsRuntime', 'depsUnknown', 'depsUnresolvable', 'depsStdlib', 'fileOpsBlocked']
    const without = ['ready', 'running', 'completedMany', 'cancelled', 'workdir']
    for (const name of [...withBtn, ...without]) {
      await mount()
      await openWith(report(STATES[name]))
      if (withBtn.includes(name)) {
        // 主按钮就是 AI 修复时不重复摆次按钮（errRemedyAi / errRemedyConnect）
        const primaryIsAi = primary()?.dataset.prepPrimary === 'ai_fix'
        expect(aiBtn() !== null || primaryIsAi, name).toBe(true)
        if (aiBtn()) {
          expect(aiBtn()!.textContent).toBe('使用你的 AI 来修复')
          expect(aiBtn()!.className).toMatch(/inset-ring-accent/)
          expect(aiBtn()!.className).not.toMatch(/#[0-9a-f]{3,6}/i)
          expect(aiBtn()!.dataset.variant).not.toBe('primary')
        }
      } else {
        expect(aiBtn(), name).toBeNull()
        expect(primary()?.dataset.prepPrimary, name).not.toBe('ai_fix')
      }
      await act(async () => root.unmount())
      host.remove()
    }
  })

  it('remedy 为 ai_fix / connect_ai：主按钮就是它；feedback：一句话 +「发送」', async () => {
    await mount()
    await openWith(report(STATES.errRemedyAi))
    expect(primary()?.dataset.prepPrimary).toBe('ai_fix')
    expect(primary()?.textContent).toBe('使用你的 AI 来修复')
    expect(aiBtn()).toBeNull()
    await act(async () => root.unmount())
    host.remove()
    await mount()
    await openWith(report(STATES.errRemedyFeedback))
    expect(panel().querySelector('[data-prep-line]')?.textContent).toBe('这个脚本暂时用不了，发给我们看看？')
    expect(panel().querySelector('[data-send-report]')?.textContent).toBe('发送')
    expect(primary()).toBeNull() // 反馈卡的主按钮是 SendReportButton（打开发送对话框，源码默认不勾）
    expect(aiBtn()).not.toBeNull()
  })

  it('没连 AI：点它打开设置的 AI 页；连好（caps 里出现可用的 AI）后自动接着进入修复，不用再点', async () => {
    mockFix.mockResolvedValue({ ok: false, reason: 'ai_fix_unavailable' })
    await mount()
    await openWith(report(STATES.errPlain))
    await act(async () => aiBtn()!.click())
    expect(useUiStore.getState().settingsOpen).toBe(true)
    expect(useUiStore.getState().settingsSection).toBe('ai')
    expect(mockFix).not.toHaveBeenCalled()
    expect(useAiFixStore.getState().entries[useProjectPreparationStore.getState().focus!]?.phase).toBe('awaiting_connect')
    // 用户在设置里连好了
    await act(async () => {
      useAiStore.setState({ caps: usable(true) })
    })
    expect(mockFix).toHaveBeenCalledTimes(1)
    expect(mockFix).toHaveBeenCalledWith({ session_id: 'psess-1', script: 'plot.py' })
  })

  it('设置关了还没连上：放弃等待，之后偶然连上 AI 不会冷不丁开始修', async () => {
    await mount()
    await openWith(report(STATES.errPlain))
    await act(async () => aiBtn()!.click())
    await act(async () => useUiStore.getState().setSettingsOpen(false))
    await act(async () => {
      useAiStore.setState({ caps: usable(true) })
    })
    expect(mockFix).not.toHaveBeenCalled()
  })

  it('已连 AI：调 POST /api/ai/fix；遇到 ai_fix_unavailable 落成「AI 暂时还不会修」+ 发送', async () => {
    useAiStore.setState({ caps: usable(true) })
    mockFix.mockResolvedValue({ ok: false, reason: 'ai_fix_unavailable' })
    await mount()
    await openWith(report(STATES.errPlain))
    await act(async () => aiBtn()!.click())
    expect(mockFix).toHaveBeenCalledWith({ session_id: 'psess-1', script: 'plot.py' })
    expect(useUiStore.getState().settingsOpen).toBe(false)
    expect(panel().querySelector('[data-prep-line]')?.textContent).toBe('这个问题 AI 暂时还不会修')
    expect(panel().dataset.prepState).toBe('ai_unavailable')
    expect(panel().querySelector('[data-send-report]')?.textContent).toBe('发送')
    expect(aiBtn()).toBeNull()
    expect(visiblePrimaryButtons(panel())).toBeLessThanOrEqual(1)
  })

  it('capabilities 说 ai_fix_available === false：按钮照常出现，点击直接走降级，不发请求', async () => {
    useAiStore.setState({ caps: { ...(usable(true) as object), ai_fix_available: false } as never })
    await mount()
    await openWith(report(STATES.errPlain))
    expect(aiBtn()).not.toBeNull()
    await act(async () => aiBtn()!.click())
    expect(mockFix).not.toHaveBeenCalled()
    expect(panel().dataset.prepState).toBe('ai_unavailable')
  })

  it('降级只对点的那次尝试有效：重新运行出新尝试后卡片换回原样', async () => {
    useAiStore.setState({ caps: { ...(usable(true) as object), ai_fix_available: false } as never })
    await mount()
    await openWith(report(STATES.errPlain))
    await act(async () => aiBtn()!.click())
    expect(panel().dataset.prepState).toBe('ai_unavailable')
    const next = report({
      ...STATES.errPlain,
      observation_seq: 9,
      provider: { plan_id: 'p', attempt_id: 'prep-NEW', attempts: 2, dependency: null },
    })
    await act(async () => {
      useProjectPreparationStore.setState((st) => {
        const key = st.focus!
        return { entries: { ...st.entries, [key]: { ...st.entries[key], report: next } } }
      })
    })
    expect(panel().dataset.prepState).toBe('failed')
    expect(aiBtn()).not.toBeNull()
  })

  it('教程层激活时：整张准备卡让位，不出现任何「连接 AI」相关按钮', async () => {
    await mount()
    await openWith(report(STATES.errPlain))
    expect(aiBtn()).not.toBeNull()
    await act(async () => {
      useOnboardingStore.setState({ status: 'active' } as never)
    })
    expect(host.querySelector('[data-prep-card]')).toBeNull()
    expect(host.querySelector('[data-prep-ai-fix]')).toBeNull()
  })

  it('英文界面：按钮文案是英文，没有「执行器 / CLI / agent」字样', async () => {
    await i18n.changeLanguage('en-US')
    await mount()
    await openWith(report(STATES.errPlain))
    expect(aiBtn()?.textContent).toBe('Fix it with your AI')
    for (const lng of ['zh-CN', 'en-US']) {
      const ws = resources[lng as keyof typeof resources].workspace.prep
      const text = JSON.stringify([ws.btn.aiFix, ws.line.feedback, ws.line.aiUnavailable])
      expect(text).not.toMatch(/执行器|CLI|agent/i)
    }
  })
})

describe('版本不合自动换 Python（ADR 0114 修订 2026-10-23）：一句话 + 进度 + 停止，不问用户', () => {
  const change = (over: Partial<NonNullable<PreparationReport['python_change']>> = {}, extra: Partial<PreparationReport> = {}) =>
    report({ ...STATES.pythonChange, python_change: { ...STATES.pythonChange.python_change!, ...over }, ...extra })

  it('系统 Python：「正在换成 Python 3.12…」，阶段一句 + 不定态进度条 + 文字「停止」；没有主按钮之外的第二件事', async () => {
    await mount()
    await openWith(change())
    expect(panel().dataset.prepState).toBe('python_change')
    expect(panel().querySelector('[data-prep-line]')?.textContent).toBe('正在换成 Python 3.12…')
    expect(panel().querySelector('[data-python-change-line]')?.textContent).toBe('正在创建新的运行环境…')
    expect(panel().querySelector('[data-prep-progress]')).not.toBeNull()
    expect(panel().querySelector('[data-prep-stop]')).not.toBeNull()
    expect(visibleSentenceCount(panel())).toBeLessThanOrEqual(1)
    // 只讲「连接/换成」，不出现「执行器」
    expect(panel().textContent).not.toContain('执行器')
  })

  it('私有 Python 要下载：标题如实说约多少 MB；下载那一段说已下载字节并画确定的进度条（百分比）', async () => {
    await mount()
    await openWith(report(STATES.pythonChangeDownload))
    expect(panel().querySelector('[data-prep-line]')?.textContent).toBe('正在换成 Python 3.13（下载约 24 MB）…')
    expect(panel().querySelector('[data-python-change-line]')?.textContent).toBe('已下载 5 / 24 MB')
    const bar = panel().querySelector('[role="progressbar"]') as HTMLElement | null
    expect(bar?.getAttribute('aria-valuenow')).toBe('21')
  })

  it('已就位 / 本地缓存的私有 Python（下载字节为 0）不说"下载约…"', async () => {
    await mount()
    await openWith(change({ base: 'private', stage: 'launching' }))
    expect(panel().querySelector('[data-prep-line]')?.textContent).toBe('正在换成 Python 3.12…')
    expect(panel().querySelector('[data-python-change-line]')?.textContent).toBe('正在启动 Python…')
  })

  it('「停止」认领报告里的 cancel 动作（会话里的取消，后端把它转给换环境的事务）', async () => {
    await mount()
    await openWith(change())
    mockAct.mockResolvedValueOnce({ claimed: true, report: report({ phase: 'cancelled', observation_seq: 2 }) })
    await act(async () => (panel().querySelector('[data-prep-stop]') as HTMLButtonElement).click())
    expect(mockAct.mock.calls[0][1]).toMatchObject({ action_id: 'act-cancel' })
  })

  it('完成后照常出图：卡片回到出图的句子，详情里留一行「换成了 Python 3.12」，运行记录不把它当一次执行', async () => {
    await mount()
    await openWith(
      report({
        ...STATES.completed,
        python_change: { from: '3.14', to: '3.12', base: 'system', download_bytes: 0, state: 'done', stage: 'installing', done: 0, total: 0, code: '' },
        attempt_history: [
          { run: 1, seq: 1, attempt_id: 'a1', profile: 'safe', interpreter: 'i', launch: 'l', cause: '', changed: [], outcome: 'failed', code: 'project_env_unsupported_python', declined: '' },
          { kind: 'python_changed', run: 1, from: '3.14', to: '3.12', base: 'system', download_bytes: 0, outcome: 'succeeded', code: '' },
          { run: 1, seq: 2, attempt_id: 'a2', profile: 'safe', interpreter: 'j', launch: 'l', cause: 'python_changed', changed: ['interpreter'], outcome: 'succeeded', code: '', declined: '' },
        ],
      }),
    )
    expect(panel().dataset.prepState).not.toBe('python_change')
    expect(panel().querySelector('[data-python-change-line]')).toBeNull()
    await toggleDetails()
    expect(details()?.querySelector('[data-prep-row="python-changed"]')?.textContent).toBe('换成了Python 3.12')
    // 运行记录只数执行：两次执行，不是三条
    expect(details()?.querySelectorAll('[data-prep-row="history"]').length).toBe(2)
  })

  it('en-US：同一套状态用英文说，且同样没有"executor"', async () => {
    await i18n.changeLanguage('en-US')
    try {
      await mount()
      await openWith(report(STATES.pythonChangeDownload))
      expect(panel().querySelector('[data-prep-line]')?.textContent).toBe('Switching to Python 3.13 (about 24 MB to download)…')
      expect(panel().querySelector('[data-python-change-line]')?.textContent).toBe('5 / 24 MB downloaded')
      expect(panel().textContent?.toLowerCase()).not.toContain('executor')
    } finally {
      await i18n.changeLanguage('zh-CN')
    }
  })

  it('老后端没有 python_change / 非换版本的准备中状态：仍是原来的"正在安装…"', async () => {
    await mount()
    await openWith(report(STATES.preparing))
    expect(panel().dataset.prepState).toBe('preparing')
    expect(panel().querySelector('[data-prep-python-change]')).toBeNull()
  })
})
