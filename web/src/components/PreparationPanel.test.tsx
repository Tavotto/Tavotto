/**
 * 准备面板（T09，ADR 0116）：
 *   * 「卡片一句话读懂」：每一种状态 × 每一种语种，默认可见区至多一句话、至多一个主按钮（`visibleBlocks` 同一把尺子）；
 *   * 主按钮就是报告里后端给的那件事：没有 `run` 动作就没有「确认并运行」；
 *   * 执行结束 ≠ 首图成功：跑完没图说「运行完成，未发现可编辑图」，没有「进入编辑」；
 *   * 进入编辑直接用这次捕获的图（加进画布），不发任何运行请求；「已进入编辑」等那张图的编辑渲染可用才说；
 *   * 同一问只有一个展示面：面板开着且展示那一问时认领，原对话框让开；关面板就放手，对话框接着显示同一问。
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
  probeScript,
  type CapturedFigureDescriptor,
  type PreparationAction,
  type PreparationReport,
} from '@/lib/api'
import { i18n, resources } from '@/i18n'
import { setCurrentProjectId } from '@/lib/session'
import { PreparationPanel } from '@/components/PreparationPanel'
import { ScriptInputDialog } from '@/components/ScriptInputDialog'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { useProjectPreparationStore } from '@/store/projectPreparationStore'
import { useRenderStore } from '@/store/renderStore'
import { useScriptInputStore } from '@/store/scriptInputStore'
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
      { id: 'workdir', kind: 'workdir_choice', code: 'workdir_confirmation_required', payload: { code: 'workdir_confirmation_required' } as never },
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
        <PreparationPanel />
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

const panel = () => host.querySelector('[data-preparation-panel]') as HTMLElement
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
        // 详情默认收起
        expect((panel().querySelector('[data-prep-details]') as HTMLDetailsElement).open).toBe(false)
      })
    }
  }
})

describe('主按钮就是后端给的那件事', () => {
  it('ready：确认并运行 = 认领报告里的 run 动作', async () => {
    await mount()
    await openWith(report())
    expect(primary()?.dataset.prepPrimary).toBe('run')
    mockAct.mockResolvedValueOnce({ claimed: true, report: report({ phase: 'running', observation_seq: 2, actions: [action('cancel')] }) })
    await act(async () => primary()!.click())
    expect(mockAct).toHaveBeenCalledWith('psess-1', { action_id: 'act-run', expected_config_revision: 1 }, 'pj-a')
  })

  it('报告里没有 run 动作：就没有「确认并运行」', async () => {
    await mount()
    await openWith(report({ actions: [action('recheck')] }))
    expect(primary()).toBeNull()
  })

  it('依赖授权：主按钮认领 prepare_dependencies 并回显影响摘要；详情里列出要装的包', async () => {
    await mount()
    await openWith(report(STATES.deps))
    expect(primary()?.dataset.prepPrimary).toBe('prepare_dependencies')
    expect(panel().querySelector('[data-prep-impact]')?.textContent).toContain('numpy, pandas')
    mockAct.mockResolvedValueOnce({ claimed: true, report: report({ phase: 'preparing_environment', observation_seq: 2 }) })
    await act(async () => primary()!.click())
    expect(mockAct.mock.calls[0][1]).toEqual({
      action_id: 'act-prepare_dependencies',
      expected_config_revision: 1,
      impact_digest: 'imp-1',
    })
  })
})

describe('执行结束、捕获到图、首次编辑渲染是三件事', () => {
  it('跑完没图：说「运行完成，未发现可编辑图」，没有「进入编辑」', async () => {
    await mount()
    await openWith(report(STATES.noFigure))
    expect(panel().dataset.prepState).toBe('no_figure')
    expect(panel().querySelector('[data-prep-line]')?.textContent).toBe('运行完成，未发现可编辑图。')
    expect(primary()).toBeNull()
  })

  it('进入编辑：用这次捕获的图进入图内编辑（稳定动作），不发任何运行请求；编辑渲染可用之后才说「已进入编辑」', async () => {
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
    expect(panel().dataset.prepState).toBe('edit_opening')
    await act(async () => {
      useRenderStore.setState({
        byKey: {
          [`runtime:plot.py#a`]: {
            ...(useRenderStore.getState().byKey['x'] ?? {}),
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
  const req = { id: 'req-1', script: 'plot.py', index: 1, input_kind: 'input' as const, prompt: '选哪个？', stdout_tail: '1) a\n2) b' }

  it('面板展示那一问时认领，原对话框让开；关面板就放手，对话框接着显示同一问', async () => {
    await mount(true)
    useScriptInputStore.getState().onRequested(req)
    await openWith(report(STATES.input))
    expect(panel().querySelector('[data-prep-input]')).not.toBeNull()
    expect(useScriptInputStore.getState().presenters).toEqual(['prep-panel'])
    expect(document.querySelector('[data-script-input-answer]')).not.toBeNull()
    // 只有一个答题框（面板里那个），对话框没出来
    expect(document.querySelectorAll('[data-script-input-answer]').length).toBe(1)
    await act(async () => useUiStore.getState().setPreparationOpen(false))
    expect(useScriptInputStore.getState().presenters).toEqual([])
    expect(useScriptInputStore.getState().queue[0]?.id).toBe('req-1') // 关面板不取消那一问
    expect(document.querySelectorAll('[data-script-input-answer]').length).toBe(1) // 现在是对话框里那个
    expect(panel()).toBeNull()
  })

  it('队首不是报告里的那一问（别的脚本在问）：面板不认领，原对话框照旧', async () => {
    await mount(true)
    useScriptInputStore.getState().onRequested({ ...req, id: 'other', script: 'other.py' })
    await openWith(report(STATES.input))
    expect(panel().querySelector('[data-prep-input]')).toBeNull()
    expect(useScriptInputStore.getState().presenters).toEqual([])
  })
})
