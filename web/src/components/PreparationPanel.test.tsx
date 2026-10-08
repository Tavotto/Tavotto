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
import { useDocumentStore } from '@/store/documentStore'
import { useEnvStore } from '@/store/envStore'
import { renderKeyOf } from '@/store/renderStore'
import { seedExactRender } from '@/test/renderFixtures'
import type { PanelObject } from '@/types/document'
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
  useDocumentStore.setState((st) => ({ doc: { ...st.doc, objects: [] } }))
  // 入口动作做完之后，画布上有这张图的面板（真实实现由 `addRuntimePanelToCanvas` / `openFastEdit` 完成）
  vi.mocked(openFastEdit).mockImplementation((id: string) => {
    putPanel(id)
    return 'editing'
  })
  vi.mocked(addRuntimePanelToCanvas).mockImplementation(((d: CapturedFigureDescriptor) => putPanel(d.asset_id)) as never)
})

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
    expect(mockAct).toHaveBeenCalledWith('psess-1', { action_id: 'act-run', expected_config_revision: 1 }, 'pj-a', expect.any(AbortSignal))
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

  it('无参数运行替换掉了此前带参数产出的图名（T09b）：同一句里说清哪些、怎么恢复，主按钮仍是进入编辑', async () => {
    await mount()
    await openWith(report(STATES.completedUnlinked))
    expect(panel().dataset.prepState).toBe('completed_unlinked')
    expect(panel().querySelector('[data-prep-line]')?.textContent).toBe(
      '已捕获 1 张图；此前带其他参数生成的 a_scaled 和 b_scaled 已不再关联，用原参数再运行一次即可恢复。',
    )
    expect(primary()?.dataset.prepPrimary).toBe('enter_edit')
    // 没有替换掉任何东西：照旧那一句
    await act(async () => root.unmount())
    host.remove()
    useProjectPreparationStore.getState().clear()
    await mount()
    await openWith(report({ ...STATES.completed, unlinked_stems: [] }))
    expect(panel().dataset.prepState).toBe('completed')
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
    await act(async () => seedExactRender(panelFor('runtime:plot.py#a'), { elements: [] } as never))
    expect(panel().dataset.prepState).toBe('edit_ready')
  })
})

describe('await 之后复核项目归属：A 的描述符绝不落进 B 的文档', () => {
  const deferAssets = () => {
    let release!: () => void
    useRuntimeAssetStore.setState({ assets: [], loadAssets: () => new Promise<void>((r) => (release = r)) })
    return () => release()
  }
  it('进入编辑：loadAssets 挂起时切到 B，解析后不调 addRuntimePanelToCanvas / openFastEdit，B 的文档不变', async () => {
    await mount()
    await openWith(report(STATES.completed))
    const release = deferAssets()
    await act(async () => primary()!.click())
    const before = useDocumentStore.getState().doc
    await act(async () => {
      setCurrentProjectId('pj-b')
      useProjectPreparationStore.getState().clear() // 与 projectStore 切项目时一样换代
    })
    await act(async () => release())
    expect(vi.mocked(addRuntimePanelToCanvas)).not.toHaveBeenCalled()
    expect(vi.mocked(openFastEdit)).not.toHaveBeenCalled()
    expect(useDocumentStore.getState().doc).toBe(before)
  })
  it('A → B → A（回到同一个项目 id）也算换过：同样放弃', async () => {
    await mount()
    await openWith(report(STATES.completed))
    const release = deferAssets()
    await act(async () => primary()!.click())
    await act(async () => {
      setCurrentProjectId('pj-b')
      setCurrentProjectId('pj-a')
    })
    await act(async () => release())
    expect(vi.mocked(addRuntimePanelToCanvas)).not.toHaveBeenCalled()
    expect(vi.mocked(openFastEdit)).not.toHaveBeenCalled()
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

describe('「已进入编辑」只认入口动作创建的那个面板的精确新渲染', () => {
  it('markStale() 留下的旧渲染、同文件别的变体，都不算；该键上非 stale 的精确 manifest 才算', async () => {
    await mount()
    await openWith(report(STATES.completed))
    const stalePanel = panelFor('runtime:plot.py#a')
    // 点击之前，画布上这张图已经有一份被 markStale() 留下的旧渲染，以及一个别的 override 变体的就绪渲染
    useRenderStore.getState().patch(renderKeyOf(stalePanel), {
      fileId: 'runtime:plot.py#a', status: 'ready', manifest: { elements: [] } as never, stale: true,
      lastPatches: '[]', wantPatches: '[]',
    })
    useRenderStore.getState().patch('runtime:plot.py#a|other-variant', {
      fileId: 'runtime:plot.py#a', status: 'ready', manifest: { elements: [] } as never, stale: false,
      lastPatches: '[{"x":1}]', wantPatches: '[{"x":1}]',
    })
    await act(async () => primary()!.click())
    expect(panel().dataset.prepState).toBe('edit_opening')
    // 新渲染到了（同一键上非 stale、与 overrides 对得上）才说已进入编辑
    await act(async () => seedExactRender(stalePanel, { elements: [] } as never))
    expect(panel().dataset.prepState).toBe('edit_ready')
  })
})

describe('「改用内置环境」只在需求允许选环境时给', () => {
  const lockedEnv = () => {
    const base = STATES.env.requirements![0] as { payload: { decision: Record<string, unknown> } }
    return {
      ...STATES.env,
      requirements: [{ ...base, payload: { ...base.payload, decision: { ...base.payload.decision, locked_by: { source: 'global' } } } }],
    } as Partial<PreparationReport>
  }

  it('locked_by 在：详情里没有这个按钮（点了只会丢掉项目偏好而环境不变）', async () => {
    await mount()
    await openWith(report(lockedEnv()))
    const labels = Array.from(panel().querySelectorAll('button')).map((b) => b.textContent)
    expect(labels).not.toContain('改用内置环境')
  })

  it('没锁时有；设置失败要显示出来', async () => {
    await mount()
    await openWith(report(STATES.env))
    const btn = panel().querySelector('[data-prep-use-builtin]') as HTMLButtonElement
    expect(btn).not.toBeNull()
    const spy = vi.spyOn(useEnvStore.getState(), 'setProjectPython').mockResolvedValueOnce('设置失败了')
    useEnvStore.setState({ setProjectPython: spy as never })
    await act(async () => btn.click())
    expect(spy).toHaveBeenCalledWith(null)
    expect(panel().querySelector('[data-prep-env-error]')?.textContent).toBe('设置失败了')
  })
})

describe('多张图的结果对话框', () => {
  it('从对话框里加进画布的图也记入编辑记录（面板才会往前走）', async () => {
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

describe('切换聚焦条目：面板的条目局部状态不带到另一个脚本', () => {
  const otherReport = (over: Partial<PreparationReport> = {}) =>
    report({
      ...over,
      session_id: 'psess-2',
      target: { kind: 'script', script: 'other.py', entry: '__main__', asset_id: null, stem: null },
    })
  const openOther = async (over: Partial<PreparationReport> = {}) => {
    mockCreate.mockResolvedValueOnce(otherReport(over))
    await act(async () => {
      await useProjectPreparationStore.getState().open({ script: 'other.py' })
    })
  }
  const focusKey = async (key: string) => {
    await act(async () => useProjectPreparationStore.setState({ focus: key }))
  }

  it('A 的本地失败文案（采用环境失败）不出现在 B 上；切回 A 也是干净的', async () => {
    await mount()
    await openOther() // B 先开好（报告已到），再回 A 制造失败，最后只切焦点：切换时两份报告的修订 / 观察序号相同
    await openWith(report(STATES.env))
    const spy = vi.spyOn(useEnvStore.getState(), 'adoptCandidate').mockResolvedValue('采用失败了')
    useEnvStore.setState({ adoptCandidate: spy as never })
    expect(primary()?.dataset.prepPrimary).toBe('adopt')
    await act(async () => primary()!.click())
    expect(panel().dataset.prepState).toBe('action_failed')
    expect(panel().querySelector('[data-prep-line]')?.textContent).toBe('采用失败了')
    await focusKey('script:other.py')
    expect(panel().getAttribute('aria-label')).toContain('other.py')
    expect(panel().dataset.prepState).not.toBe('action_failed')
    expect(panel().querySelector('[data-prep-line]')?.textContent).not.toBe('采用失败了')
    await focusKey('script:plot.py')
    expect(panel().dataset.prepState).not.toBe('action_failed')
  })

  it('A 的多图结果对话框开着，切到 B：对话框不跟过去（切回 A 也是关着的）', async () => {
    await mount()
    await openOther()
    await openWith(report(STATES.completedMany))
    await act(async () => primary()!.click())
    expect(document.body.querySelectorAll('[role="dialog"] ul button').length).toBe(3)
    await focusKey('script:other.py')
    expect(panel().getAttribute('aria-label')).toContain('other.py')
    expect(document.body.querySelector('[role="dialog"]')).toBeNull()
    await focusKey('script:plot.py')
    expect(document.body.querySelector('[role="dialog"]')).toBeNull()
  })

  it('详情里「改用内置环境」的失败文案同样不带过去', async () => {
    await mount()
    await openOther(STATES.env) // B 也有「改用内置环境」按钮，才看得出失败文案是不是被带过来
    await openWith(report(STATES.env))
    const spy = vi.spyOn(useEnvStore.getState(), 'setProjectPython').mockResolvedValueOnce('设置失败了')
    useEnvStore.setState({ setProjectPython: spy as never })
    await act(async () => (panel().querySelector('[data-prep-use-builtin]') as HTMLButtonElement).click())
    expect(panel().querySelector('[data-prep-env-error]')).not.toBeNull()
    await focusKey('script:other.py')
    expect(panel().querySelector('[data-prep-use-builtin]')).not.toBeNull()
    expect(panel().querySelector('[data-prep-env-error]')).toBeNull()
  })
})

describe('结果对话框记录的是它刚加进画布的那个面板', () => {
  it('同一素材文档里已有一个面板（旧的就绪渲染）：新加的还在渲染时不报就绪', async () => {
    await mount()
    await openWith(report(STATES.completedMany))
    // 文档里已有 b 的旧实例（用户改过样式 = 另一个渲染键），且它有一份就绪的精确渲染；
    // 按素材 id 回找会选中它，它的就绪渲染会让「首次编辑渲染」立刻成立
    const old = { ...panelFor('runtime:plot.py#b'), id: 'panel-old-b', overrides: [{ op: 'x' }] } as unknown as PanelObject
    useDocumentStore.setState((st) => ({ doc: { ...st.doc, objects: [...st.doc.objects, old] } }))
    seedExactRender(old, { elements: [] } as never)
    // 对话框加图：新实例是另一个 id
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
    expect(panel().dataset.prepState).toBe('edit_opening') // 旧实例的就绪渲染不算
    await act(async () => seedExactRender(fresh, { elements: [] } as never))
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
