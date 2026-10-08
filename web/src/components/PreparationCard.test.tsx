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
  fetchScriptArguments: vi.fn().mockResolvedValue({ ok: true, script: 'plot.py', arguments: { status: 'none', arguments: [] } }),
}))
vi.mock('@/store/workspace', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/store/workspace')>()),
  addRuntimePanelToCanvas: vi.fn(),
  openFastEdit: vi.fn(() => 'editing'),
}))

import {
  actOnPreparationSession,
  createPreparationSession,
  fetchPreparationSession,
  probeScript,
  type CapturedFigureDescriptor,
  type PreparationAction,
  type PreparationReport,
} from '@/lib/api'
import { i18n, resources } from '@/i18n'
import { setCurrentProjectId } from '@/lib/session'
import { PreparationCard } from '@/components/PreparationCard'
import { ScriptInputDialog } from '@/components/ScriptInputDialog'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { useProjectPreparationStore } from '@/store/projectPreparationStore'
import { useRenderStore } from '@/store/renderStore'
import { useScriptInputStore } from '@/store/scriptInputStore'
import { useScriptArgvStore } from '@/store/scriptArgvStore'
import { useEnvStore } from '@/store/envStore'
import { useUiStore } from '@/store/uiStore'
import { addRuntimePanelToCanvas, openFastEdit } from '@/store/workspace'
import { useRuntimeAssetStore } from '@/store/runtimeAssetStore'
import { visiblePrimaryButtons, visibleSentenceCount } from '@/test/visibleBlocks'

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

/** 面板要面对的每一种状态（报告形状来自后端合同 `prepsession.report`） */
const STATES: Record<string, Partial<PreparationReport>> = {
  ready: {},
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
  cancelled: { phase: 'cancelled', outcome: { kind: 'cancelled' }, actions: [action('run'), action('recheck')] },
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

beforeEach(() => {
  mockCreate.mockReset()
  mockAct.mockReset()
  vi.mocked(addRuntimePanelToCanvas).mockReset()
  vi.mocked(openFastEdit).mockClear()
  useRuntimeAssetStore.setState({ assets: [], loadAssets: async () => {} })
  vi.mocked(probeScript).mockReset()
  setCurrentProjectId('pj-a')
  useProjectPreparationStore.getState().clear()
  useScriptInputStore.setState({ queue: [], presenters: [], busy: false, error: null })
  useRenderStore.setState({ byKey: {} })
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
          expect(primary()?.dataset.prepPrimary).toBe('run')
        }
      })
    }
  }
})

const toggleDetails = () =>
  act(async () => (panel().querySelector('[data-prep-details-toggle]') as HTMLButtonElement).click())
const details = () => panel().querySelector('[data-prep-details]') as HTMLElement | null

describe('主按钮就是后端给的那件事', () => {
  it('ready：运行 = 认领报告里的 run 动作；旁边是「稍后」', async () => {
    await mount()
    await openWith(report())
    expect(primary()?.dataset.prepPrimary).toBe('run')
    expect(primary()?.textContent).toBe('运行')
    expect(panel().querySelector('[data-prep-later]')).not.toBeNull()
    mockAct.mockResolvedValueOnce({ claimed: true, report: report({ phase: 'running', observation_seq: 2, actions: [action('cancel')] }) })
    await act(async () => primary()!.click())
    expect(mockAct).toHaveBeenCalledWith('psess-1', { action_id: 'act-run', expected_config_revision: 1 }, 'pj-a')
  })

  it('报告里没有 run 动作：就没有「运行」', async () => {
    await mount()
    await openWith(report({ actions: [action('recheck')] }))
    expect(primary()).toBeNull()
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

  it('运行出错：详情第一行是脚本的错误原文（不是字面量占位符），下面是这一次的诊断', async () => {
    await mount()
    await openWith(
      report({
        ...STATES.failed,
        result: {
          status: 'error',
          error: {
            code: 'script_probe_failed',
            message: '脚本执行失败',
            params: { error: "ValueError: could not convert string to float: 'abc'" },
            traceback: 'Traceback (most recent call last):\n  File "tools/plot.py", line 41, in main\nValueError: x',
          } as never,
        },
      }),
    )
    expect(panel().querySelector('[data-prep-line]')?.textContent).toBe('运行出错了')
    expect(primary()?.textContent).toBe('再试一次')
    await toggleDetails()
    const first = details()!.firstElementChild as HTMLElement
    expect(first.dataset.prepError).toBe('script_probe_failed')
    expect(first.textContent).toContain("ValueError: could not convert string to float: 'abc'")
    expect(first.textContent).toContain('File "tools/plot.py", line 41')
    expect(details()!.textContent).not.toContain('{{')
    expect(details()!.querySelector('[data-task-diagnostic], button')).not.toBeNull()
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

  it('进入编辑：用这次捕获的图进入图内编辑（稳定动作），不发任何运行请求；卡片随即收起、不留角标', async () => {
    await mount()
    await openWith(report(STATES.completed))
    expect(primary()?.dataset.prepPrimary).toBe('enter_edit')
    await act(async () => primary()!.click())
    // 素材清单里还没有这张新图：先用这次捕获的描述符加进画布，再进入编辑
    expect(vi.mocked(addRuntimePanelToCanvas)).toHaveBeenCalledWith(fig('a'))
    expect(vi.mocked(openFastEdit)).toHaveBeenCalledWith('runtime:plot.py#a')
    expect(mockAct).not.toHaveBeenCalled()
    expect(vi.mocked(probeScript)).not.toHaveBeenCalled()
    expect(mockCreate).toHaveBeenCalledTimes(1) // 只有打开时那一次只读检查
    expect(useUiStore.getState().guideCard).toBe('closed')
    expect(panel()).toBeNull()
    expect(host.querySelector('[data-prep-pill]')).toBeNull()
    // 再从素材库打开：说的是编辑的进度（编辑渲染可用之后才说「已进入编辑」）
    await act(async () => useUiStore.getState().setGuideCard('card'))
    expect(panel().dataset.prepState).toBe('edit_opening')
    await act(async () => {
      useRenderStore.setState({
        byKey: {
          [`runtime:plot.py#a`]: {
            fileId: 'runtime:plot.py#a',
            status: 'ready',
            manifest: { elements: [] } as never,
          } as never,
        },
      })
    })
    expect(panel().dataset.prepState).toBe('edit_ready')
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
