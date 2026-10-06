/**
 * T09：GUI 入口走同一个准备会话（本地开关默认开）。
 *
 *   * 素材库脚本行的 ▶ 与项目检查条上的「准备并运行」都只**打开**准备面板：后端只做只读检查（创建会话），
 *     一个试运行请求都不发、`run` 动作也不认领；参数草稿在那一刻随目标带上；
 *   * 脚本行的状态一句话翻译会话的 phase（不另判），点它回到面板。
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
import { ProjectScanBar } from '@/components/ProjectScanBar'
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
  useUiStore.setState({ preparationOpen: false, scanPanelOpen: false })
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
    expect(useUiStore.getState().preparationOpen).toBe(true)
    // 行上的状态一句话翻译会话 phase；点它回到面板
    const status = host.querySelector<HTMLButtonElement>('[data-script-prep-status]')!
    expect(status.dataset.scriptPrepStatus).toBe('ready_to_run')
    expect(status.textContent).toBe('可以运行')
    useUiStore.setState({ preparationOpen: false })
    await act(async () => status.click())
    expect(useUiStore.getState().preparationOpen).toBe(true)
  })
})

describe('项目检查条', () => {
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

  it('「准备并运行」打开准备面板（只读检查），不走旧的试运行', async () => {
    resetProjectScanBookkeeping()
    useProjectStore.setState({ project: { open: true, id: 'pj-a' } } as never)
    useOnboardingStore.setState({ ...ONBOARDING_DEFAULTS })
    useProjectScanStore.setState({ scan: scan() })
    useUiStore.setState({ scanPanelOpen: true })
    mockCreate.mockResolvedValueOnce(prep())
    await mount(<ProjectScanBar />)
    const btn = host.querySelector<HTMLButtonElement>('[data-scan-prepare="plot.py"]')!
    expect(btn.textContent).toBe('准备并运行')
    await act(async () => btn.click())
    await flush()
    expect(mockCreate).toHaveBeenCalledWith({ script: 'plot.py' }, 'pj-a', expect.anything())
    expect(vi.mocked(probeScript)).not.toHaveBeenCalled()
    expect(useProjectPreparationStore.getState().focus).toBe('script:plot.py')
  })
})
