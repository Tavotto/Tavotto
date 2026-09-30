/**
 * 素材库脚本行遇到 `dependency_preparation_required`（开跑前要先准备依赖）时的一句话 + 一键修复。
 *
 * 2026-09-29 干净 macOS 虚拟机实测：单包修复装完 openpyxl，自动重跑撞上跑前门「缺 pandas」，这一行以前变成
 * 一行红字、归到「可能需要原环境」、只有「选择渲染环境」——没有一键修复，也不弹授权框，小白卡死。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { __resetDepRepairParkingForTests, useDepRepairStore } from '@/store/depRepairStore'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  fetchRegistry: vi.fn(),
  probeScript: vi.fn(),
  cancelProbe: vi.fn().mockResolvedValue({ cancelling: true }),
  fetchPanels: vi.fn().mockResolvedValue({ figures_dir: '', panels: [] }),
  fetchRuntimeAssets: vi.fn().mockResolvedValue({ assets: [] }),
  createJointDependencyPlan: vi.fn(),
  prepareJointDependencies: vi.fn(),
  cancelJointDependencies: vi.fn().mockResolvedValue({}),
  fetchEngineEnvironment: vi.fn().mockResolvedValue({}),
}))

import {
  createJointDependencyPlan,
  DEPENDENCY_PREPARATION_CODE,
  fetchRegistry,
  prepareJointDependencies,
  probeScript,
  type DependencyPreparationOffer,
  type JointDependencyPlan,
  type ProbeResult,
  type RegistryView,
  type ScriptInventoryEntry,
} from '@/lib/api'
import { ScriptLibrary } from '@/components/left/ScriptLibrary'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { useEnvStore } from '@/store/envStore'
import { useScriptLibraryStore } from '@/store/scriptLibraryStore'
import { useScriptRunStore } from '@/store/scriptRunStore'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const mockRegistry = vi.mocked(fetchRegistry)
const mockProbe = vi.mocked(probeScript)

const SCRIPT = 'plot_incidence.py'

const entry: ScriptInventoryEntry = {
  script: SCRIPT,
  registered: false,
  static_stems: [],
  entry_candidates: ['__main__'],
  reason: 'no_static_output',
  can_probe: true,
}
const view: RegistryView = {
  source: 'tavotto_registry.json',
  scripts: {},
  candidates: [],
  conflicts: {},
  all_scripts: [entry],
}

const jointPlan = (requirements: string[]): JointDependencyPlan =>
  ({
    plan_version: 1,
    status: 'ready',
    target_kind: 'tavotto_managed',
    script: SCRIPT,
    needed: [],
    missing: [],
    satisfied: [],
    unknown: [],
    possible: [],
    requirements,
    constraints: [],
    require_hashes: false,
    adapter: [],
    blocked: [],
    selection: { selected_groups: [], available_groups: [], unselected_groups: [], skipped_marker: [] },
    identity: 'x',
  }) as JointDependencyPlan

const offerOf = (over: Partial<DependencyPreparationOffer> = {}): DependencyPreparationOffer => ({
  code: DEPENDENCY_PREPARATION_CODE,
  script: SCRIPT,
  plan: jointPlan(['pandas', 'openpyxl']),
  target_kind: 'tavotto_managed',
  targets: [
    {
      kind: 'tavotto_managed', venv: '', python: '', modifies_user_environment: false,
      creates_environment: true, available: true, reason: '',
    },
  ],
  rounds_remaining: 3,
  skipped: false,
  ...over,
})

const preparationResult = (offer: DependencyPreparationOffer): ProbeResult => ({
  script: SCRIPT,
  entry: null,
  stems: [],
  descriptors: [],
  error: {
    code: DEPENDENCY_PREPARATION_CODE,
    message: '这个脚本开跑就需要的包目标环境里没有：pandas',
    dependency_preparation: offer,
  },
  tried: [],
  registered: false,
})

let host: HTMLElement
let root: Root

const flush = async () => {
  await act(async () => {
    await new Promise((r) => setTimeout(r, 0))
  })
}

async function mountAndRun() {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => {
    root.render(
      <TooltipProvider>
        <ScriptLibrary query="" />
      </TooltipProvider>,
    )
  })
  await flush()
  await act(async () => host.querySelector<HTMLButtonElement>('button[aria-label$="并发现图"]')!.click())
  await flush()
}

const buttonByText = (text: string): HTMLButtonElement => {
  const btn = [...host.querySelectorAll('button')].find((b) => (b.textContent ?? '').includes(text))
  if (!btn) throw new Error(`没有找到按钮: ${text}`)
  return btn as HTMLButtonElement
}

const plan = (id: string) =>
  ({
    plan_id: id, script: SCRIPT, target_kind: 'tavotto_managed', python: '', requirements: ['pandas', 'openpyxl'],
    constraints: [], require_hashes: false, adapter: [], identity: 'x', needed_imports: [], groups: [],
    modifies_user_environment: false, creates_environment: true, network_required: true, expires_at: 0,
    joint: jointPlan(['pandas', 'openpyxl']),
  }) as never

beforeEach(() => {
  __resetDepRepairParkingForTests()
  localStorage.clear()
  useScriptLibraryStore.getState().clear()
  useScriptRunStore.getState().clear()
  useDepRepairStore.getState().reset()
  useEnvStore.setState({ dependencyPreparation: null })
  mockRegistry.mockReset()
  mockProbe.mockReset()
  vi.mocked(createJointDependencyPlan).mockReset()
  vi.mocked(prepareJointDependencies).mockReset()
  mockRegistry.mockResolvedValue(view)
})

afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
  useDepRepairStore.getState().reset()
})

describe('脚本行：开跑前要先准备依赖', () => {
  it('一句话 + 一个主按钮，归「需要修复」组，不再叠「可能需要原环境」的恢复说明', async () => {
    mockProbe.mockResolvedValue(preparationResult(offerOf()))
    await mountAndRun()
    const card = host.querySelector('[data-script-preparation]')
    expect(card, '脚本行上没有准备依赖的卡片').toBeTruthy()
    expect(card!.querySelector('[data-script-preparation-sentence]')!.textContent).toBe(
      '这个脚本还缺 pandas和openpyxl，点一下自动装好。',
    )
    // 可见的主按钮只有一个；完整需求串收在默认折叠的「详情」里
    expect([...card!.querySelectorAll('button')].filter((b) => !b.closest('details')).map((b) => b.textContent)).toEqual([
      '一键修复',
    ])
    expect(card!.querySelector('details')!.hasAttribute('open')).toBe(false)
    expect(host.querySelector('[data-script-recovery]')).toBeNull()
    const groups = [...host.querySelectorAll('section ul[aria-label]')].map((ul) => ul.getAttribute('aria-label'))
    expect(groups).toEqual(['需要修复'])
  })

  it('点一键修复：走联合准备（绑定计划 → 只发 plan_id 执行），进度是一行；装好后这一行自动重跑', async () => {
    mockProbe.mockResolvedValue(preparationResult(offerOf()))
    vi.mocked(createJointDependencyPlan).mockResolvedValue({ plan: plan('joint-row') })
    vi.mocked(prepareJointDependencies).mockResolvedValue({ started: true } as never)
    await mountAndRun()
    await act(async () => buttonByText('一键修复').click())
    await flush()
    expect(createJointDependencyPlan).toHaveBeenCalledWith({ script: SCRIPT, target: 'tavotto_managed' })
    expect(prepareJointDependencies).toHaveBeenCalledWith('joint-row')
    // 没有弹授权框：这一行自己承载进度
    expect(useEnvStore.getState().dependencyPreparation).toBeNull()
    expect(host.querySelector('[data-script-preparation-sentence]')).toBeNull()
    expect(host.querySelector('[data-script-preparation] [role="progressbar"], [data-script-preparation] progress')).toBeDefined()
    // 装好：进度带着计划所属的脚本 → 这一行重跑
    mockProbe.mockClear()
    mockProbe.mockResolvedValue({ ...preparationResult(offerOf()), error: null, descriptors: [] })
    await act(async () => {
      useDepRepairStore.getState().onProgress({
        plan_id: 'joint-row', state: 'done', log: '', error: null, code: '', flow: 'joint', script: SCRIPT,
      } as never)
    })
    await flush()
    expect(mockProbe).toHaveBeenCalledTimes(1)
    expect(mockProbe.mock.calls[0][0]).toBe(SCRIPT)
    expect(host.querySelector('[data-script-preparation]')).toBeNull()
  })

  it('装失败：只说一句原因 + 「重试」，重试再走一遍联合准备', async () => {
    mockProbe.mockResolvedValue(preparationResult(offerOf()))
    vi.mocked(createJointDependencyPlan).mockResolvedValue({ plan: plan('joint-row') })
    vi.mocked(prepareJointDependencies).mockResolvedValue({ started: true } as never)
    await mountAndRun()
    await act(async () => buttonByText('一键修复').click())
    await flush()
    await act(async () => {
      useDepRepairStore.getState().onProgress({
        plan_id: 'joint-row', state: 'failed', log: '', error: '连不上', code: 'dependency_network_unavailable',
        flow: 'joint', script: SCRIPT,
      } as never)
    })
    await flush()
    const retry = buttonByText('重试')
    expect(retry.closest('[data-script-preparation]')).toBeTruthy()
    await act(async () => retry.click())
    await flush()
    expect(createJointDependencyPlan).toHaveBeenCalledTimes(2)
  })

  it('要在用户环境 / 目标之间选（有装齐的用户环境）：主按钮打开授权框，不擅自执行', async () => {
    const offer = offerOf({
      user_environments: [
        {
          id: 'env1', source: 'conda', label: 'sci', ok: true, code: '', support: 'verified',
          python_version: '3.12', matplotlib_version: '3.9', missing: [], satisfies: true,
        },
      ],
    })
    mockProbe.mockResolvedValue(preparationResult(offer))
    await mountAndRun()
    await act(async () => buttonByText('一键修复').click())
    await flush()
    expect(useEnvStore.getState().dependencyPreparation?.script).toBe(SCRIPT)
    expect(createJointDependencyPlan).not.toHaveBeenCalled()
  })
})
