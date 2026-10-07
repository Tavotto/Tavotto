/**
 * T09 / T13b：GUI 入口走同一个准备会话（本地开关默认开），落在同一张引导卡上。
 *
 *   * 素材库脚本行的 ▶ 与引导卡上的「开始准备」都只**打开**会话：后端只做只读检查（创建会话），
 *     一个试运行请求都不发、`run` 动作也不认领；参数草稿在那一刻随目标带上；
 *   * 扫描发现绘图脚本时引导卡**每个项目自动弹一次**，弹出时**不建会话**（建会话 = 检查 = 会起解释器）；
 *   * 脚本行的状态一句话翻译会话的 phase（不另判），点它回到卡片；「已关联」与扫描同一判据（后端 `linked`），
 *     这一次出错 / 跑完没图的脚本不再留在「尚未运行」组。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  fetchRegistry: vi.fn(),
  probeScript: vi.fn(),
  createPreparationSession: vi.fn(),
  fetchPreparationSession: vi.fn(),
  actOnPreparationSession: vi.fn(),
  fetchPanels: vi.fn().mockResolvedValue({ figures_dir: '', panels: [] }),
  fetchRuntimeAssets: vi.fn().mockResolvedValue({ assets: [] }),
  fetchEngineEnvironment: vi.fn().mockResolvedValue({}),
  fetchScriptAnswers: vi.fn().mockResolvedValue({ scripts: {}, location: '', pending: [] }),
  startProjectScan: vi.fn(),
  fetchProjectScan: vi.fn(),
  cancelProjectScan: vi.fn(),
}))

import {
  actOnPreparationSession,
  createPreparationSession,
  fetchRegistry,
  probeScript,
  type PreparationReport,
  type ProjectScan,
} from '@/lib/api'
import { setCurrentProjectId } from '@/lib/session'
import { PreparationCard } from '@/components/PreparationCard'
import { ScriptLibrary } from '@/components/left/ScriptLibrary'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { ONBOARDING_DEFAULTS, useOnboardingStore } from '@/store/onboardingStore'
import { useProjectPreparationStore } from '@/store/projectPreparationStore'
import { resetProjectScanBookkeeping, useProjectScanStore } from '@/store/projectScanStore'
import { useProjectStore } from '@/store/projectStore'
import { useScriptArgvStore } from '@/store/scriptArgvStore'
import { useScriptLibraryStore } from '@/store/scriptLibraryStore'
import { useScriptRunStore } from '@/store/scriptRunStore'
import { useUiStore } from '@/store/uiStore'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const mockCreate = vi.mocked(createPreparationSession)

const prep = (over: Partial<PreparationReport> = {}): PreparationReport => ({
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
  actions: [
    {
      id: 'act-run',
      kind: 'run',
      config_revision: 1,
      impact: { executes_user_script: true, installs_packages: false, changes_environment: false, writes_to_project: [] },
    },
  ],
  provider: { plan_id: 'p', attempt_id: null, attempts: 0, dependency: null },
  runtime_input: null,
  captured: [],
  result: null,
  ...over,
})

let host: HTMLElement
let root: Root
const flush = async () => {
  await act(async () => {
    await new Promise((r) => setTimeout(r, 0))
  })
}

async function mount(node: React.ReactNode) {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => root.render(<TooltipProvider>{node}</TooltipProvider>))
  await flush()
}

beforeEach(() => {
  localStorage.clear()
  setCurrentProjectId('pj-a')
  mockCreate.mockReset()
  vi.mocked(probeScript).mockReset()
  vi.mocked(actOnPreparationSession).mockReset()
  useProjectPreparationStore.getState().clear()
  useScriptLibraryStore.getState().clear()
  useScriptRunStore.getState().clear()
  useScriptArgvStore.getState().clear()
  useUiStore.setState({ guideCard: 'closed' })
})

afterEach(async () => {
  await act(async () => root?.unmount())
  host?.remove()
})

describe('素材库脚本行', () => {
  it('▶ 打开准备面板：只建检查会话（带此刻的参数），不发试运行、不认领 run', async () => {
    vi.mocked(fetchRegistry).mockResolvedValue({
      source: 'tavotto_registry.json',
      scripts: {},
      candidates: [],
      conflicts: {},
      all_scripts: [
        { script: 'plot.py', registered: false, static_stems: [], entry_candidates: ['__main__'], reason: 'dynamic_stems', can_probe: true },
      ],
    })
    useScriptArgvStore.getState().setTokens('plot.py', ['--freq', '2'])
    mockCreate.mockResolvedValueOnce(prep())
    await mount(<ScriptLibrary query="" />)
    const run = host.querySelector<HTMLButtonElement>('[data-script-run="plot.py"]')!
    expect(run.getAttribute('aria-label')).toContain('准备并运行')
    await act(async () => run.click())
    await flush()
    expect(mockCreate).toHaveBeenCalledTimes(1)
    expect(mockCreate.mock.calls[0][0]).toEqual({ script: 'plot.py', argv: ['--freq', '2'], argv_sensitive: false })
    expect(vi.mocked(probeScript)).not.toHaveBeenCalled()
    expect(vi.mocked(actOnPreparationSession)).not.toHaveBeenCalled()
    expect(useUiStore.getState().guideCard).toBe('card')
    // 行上的状态一句话翻译会话 phase；点它回到卡片
    const status = host.querySelector<HTMLButtonElement>('[data-script-prep-status]')!
    expect(status.dataset.scriptPrepStatus).toBe('ready_to_run')
    expect(status.textContent).toBe('待运行') // 行上也不说「可以运行」：必填参数可能还没填（T13b）
    useUiStore.setState({ guideCard: 'pill' })
    await act(async () => status.click())
    expect(useUiStore.getState().guideCard).toBe('card')
  })
})

const inventory = (rows: Partial<import('@/lib/api').ScriptInventoryEntry>[]) => ({
  source: 'tavotto_registry.json',
  scripts: Object.fromEntries(
    rows.filter((r) => r.registered).map((r) => [r.script!, { entry: '__main__', stems: ['Fig'] }]),
  ) as never,
  candidates: [],
  conflicts: {},
  all_scripts: rows.map((r) => ({
    script: 'plot.py',
    registered: false,
    static_stems: [],
    entry_candidates: ['__main__'],
    reason: 'static_candidate' as const,
    can_probe: true,
    ...r,
  })) as import('@/lib/api').ScriptInventoryEntry[],
})

const groupOfRow = (script: string) =>
  host.querySelector(`[data-script-row="${script}"]`)?.closest('ul')?.getAttribute('aria-label')

describe('素材库脚本行的「已关联」与分组（T13b）', () => {
  it('登记了图名但一张能编辑的图都没有（后端 linked=false）：不说「已关联」，在「尚未运行」组', async () => {
    vi.mocked(fetchRegistry).mockResolvedValue(
      inventory([
        { script: 'never.py', registered: true, linked: false },
        { script: 'done.py', registered: true, linked: true },
      ]),
    )
    await mount(<ScriptLibrary query="" />)
    const never = host.querySelector('[data-script-row="never.py"]')!
    expect(never.textContent).not.toContain('已关联')
    expect(groupOfRow('never.py')).toBe('尚未运行')
    expect(host.querySelector('[data-script-row="done.py"]')!.textContent).toContain('已关联')
    expect(groupOfRow('done.py')).toBe('已关联')
  })

  it.each([
    ['failed', { phase: 'action_required', outcome: { kind: 'failed', code: 'script_error' } }, '需要修复'],
    ['no figure', { phase: 'partial', outcome: { kind: 'execution_finished_no_figure' } }, '跑完没有图'],
  ] as const)('这一次的会话结局是 %s：脚本行离开「尚未运行」组', async (_name, over, group) => {
    vi.mocked(fetchRegistry).mockResolvedValue(inventory([{ script: 'plot.py' }]))
    mockCreate.mockResolvedValueOnce(prep(over as Partial<PreparationReport>))
    await mount(<ScriptLibrary query="" />)
    expect(groupOfRow('plot.py')).toBe('尚未运行')
    await act(async () => {
      await useProjectPreparationStore.getState().open({ script: 'plot.py' })
    })
    await flush()
    expect(groupOfRow('plot.py')).toBe(group)
  })
})

describe('引导卡：扫描发现绘图脚本', () => {
  const scan = (): ProjectScan =>
    ({
      scan_version: 1,
      project_id: 'pj-a',
      scan_id: 's1',
      epoch: 1,
      observation_seq: 1,
      reason: 'claim',
      state: 'complete',
      phase: 'awaiting_confirmation',
      outcome: { kind: 'target_found' },
      budget: { entries: 1, scripts: 1, assets: 0, elapsed_s: 0.01 },
      issues: [],
      assets: { count: 0, pdf: 0, raster: 0, browsable: false },
      scripts: [],
      targets: [
        {
          script: 'plot.py',
          role: 'plot',
          evidence: 'dynamic_stems',
          registered: false,
          entry: '__main__',
          scope: '.',
          checked: true,
          session_target: { script: 'plot.py', entry: '__main__' },
        },
      ],
      default_target: 'plot.py',
      target_choice: 'single',
      checks: [],
      environment: null,
      dependencies: null,
      actions: [],
    }) as unknown as ProjectScan

  const card = () => host.querySelector<HTMLElement>('[data-prep-card]')

  beforeEach(() => {
    resetProjectScanBookkeeping()
    useProjectStore.setState({ project: { open: true, id: 'pj-a' } } as never)
    useOnboardingStore.setState({ ...ONBOARDING_DEFAULTS })
  })

  it('自动弹一次、弹出时不建会话；「开始准备」才建（只读检查），不走旧的试运行', async () => {
    useProjectScanStore.setState({ scan: scan() })
    mockCreate.mockResolvedValueOnce(prep())
    await mount(<PreparationCard />)
    expect(card()).not.toBeNull()
    expect(card()!.dataset.prepState).toBe('discover')
    expect(card()!.querySelector('[data-prep-line]')!.textContent).toBe('发现绘图脚本 plot.py')
    expect(mockCreate).not.toHaveBeenCalled() // 弹出 ≠ 检查
    const btn = card()!.querySelector<HTMLButtonElement>('[data-prep-primary="start"]')!
    expect(btn.textContent).toBe('开始准备')
    await act(async () => btn.click())
    await flush()
    expect(mockCreate).toHaveBeenCalledWith({ script: 'plot.py' }, 'pj-a', expect.anything())
    expect(vi.mocked(probeScript)).not.toHaveBeenCalled()
    expect(useProjectPreparationStore.getState().focus).toBe('script:plot.py')
    expect(card()!.dataset.prepState).toBe('ready')
  })

  it('同一个项目第二次打开不再自动弹；「稍后」缩成角标，角标 × 才收起', async () => {
    useProjectScanStore.setState({ scan: scan() })
    await mount(<PreparationCard />)
    expect(card()).not.toBeNull()
    await act(async () => card()!.querySelector<HTMLButtonElement>('[data-prep-later]')!.click())
    expect(useUiStore.getState().guideCard).toBe('pill')
    expect(card()).toBeNull()
    const pill = host.querySelector<HTMLElement>('[data-prep-pill]')!
    expect(pill.textContent).toContain('plot.py')
    await act(async () => pill.querySelector<HTMLButtonElement>('[data-prep-pill-close]')!.click())
    expect(useUiStore.getState().guideCard).toBe('closed')
    expect(host.querySelector('[data-prep-pill]')).toBeNull()
    // 换代后同一项目再来一轮扫描：标记在本机，不再自动弹
    await act(async () => root.unmount())
    useProjectScanStore.setState({ scan: { ...scan(), scan_id: 's2' } })
    root = createRoot(host)
    await act(async () => root.render(<TooltipProvider><PreparationCard /></TooltipProvider>))
    await flush()
    expect(useUiStore.getState().guideCard).toBe('closed')
    expect(card()).toBeNull()
  })

  it('本地开关关闭：「开始准备」回到旧的试运行，不建会话', async () => {
    localStorage.setItem('tavotto.preparationPanel', 'off')
    useProjectScanStore.setState({ scan: scan() })
    vi.mocked(probeScript).mockResolvedValue({ ok: true, script: 'plot.py', stems: [], figures: [] } as never)
    await mount(<PreparationCard />)
    await act(async () => card()!.querySelector<HTMLButtonElement>('[data-prep-primary="start"]')!.click())
    await flush()
    expect(vi.mocked(probeScript)).toHaveBeenCalledTimes(1)
    expect(mockCreate).not.toHaveBeenCalled()
    expect(useUiStore.getState().guideCard).toBe('closed')
  })
})
