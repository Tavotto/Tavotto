/** 跑前缺依赖：直接弹同一个修复框；授权、进度与取消在框内，完成后继续发起的试运行。 */
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
  setProjectUserEnvironment: vi.fn(),
  skipDependencyPreparation: vi.fn(),
  fetchEngineEnvironment: vi.fn().mockResolvedValue({}),
}))

import {
  createJointDependencyPlan,
  DEPENDENCY_PREPARATION_CODE,
  fetchRegistry,
  prepareJointDependencies,
  probeScript,
  setProjectUserEnvironment,
  skipDependencyPreparation,
  cancelJointDependencies,
  type DependencyPreparationOffer,
  type JointDependencyPlan,
  type ProbeResult,
  type RegistryView,
  type ScriptInventoryEntry,
} from '@/lib/api'
import { DependencyPrepareDialog } from '@/components/DependencyPrepareDialog'
import { ScriptLibrary } from '@/components/left/ScriptLibrary'
import { setCurrentProjectId } from '@/lib/session'
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
  impact_digest: 'shown-digest',
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
        <DependencyPrepareDialog />
      </TooltipProvider>,
    )
  })
  await flush()
  await act(async () => host.querySelector<HTMLButtonElement>('button[aria-label$="并发现图"]')!.click())
  await flush()
}

const dialogButton = (text: string): HTMLButtonElement => {
  const btn = [...document.querySelectorAll('[data-dialog="dependency-prepare"] button')].find((b) =>
    (b.textContent ?? '').includes(text),
  )
  if (!btn) throw new Error(`授权框里没有按钮: ${text}`)
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
  useEnvStore.getState().resetProject()
  setCurrentProjectId('pA')
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
  useEnvStore.getState().resetProject()
  setCurrentProjectId(null)
})

const dialog = () => document.querySelector<HTMLElement>('[data-dialog="dependency-prepare"]')
const reopen = () => host.querySelector<HTMLButtonElement>('[data-script-preparation-fix]')!
const progress = (state: string, over: Record<string, unknown> = {}) => ({
  plan_id: 'joint-row', state, log: '', error: null, code: '', flow: 'joint',
  script: SCRIPT, requirements: ['pandas', 'openpyxl'], target_kind: 'tavotto_managed', ...over,
}) as never
const succeedProbe = () => mockProbe.mockResolvedValue({ ...preparationResult(offerOf()), error: null, descriptors: [] })

async function startRepair() {
  mockProbe.mockResolvedValue(preparationResult(offerOf()))
  vi.mocked(createJointDependencyPlan).mockResolvedValue({ plan: plan('joint-row') })
  vi.mocked(prepareJointDependencies).mockResolvedValue({ started: true } as never)
  await mountAndRun()
  await act(async () => dialogButton('一键修复').click())
  await flush()
}

async function switchTo(id: string) {
  await act(async () => {
    setCurrentProjectId(id)
    useEnvStore.getState().resetProject()
    useScriptRunStore.getState().clear()
    useDepRepairStore.getState().clear()
  })
  await flush()
}

describe('脚本行：开跑前要先准备依赖', () => {
  it('直接弹修复框：一句话 + 一个主动作，详情默认折叠；没点击前不安装', async () => {
    mockProbe.mockResolvedValue(preparationResult(offerOf()))
    await mountAndRun()
    expect(dialog(), '缺依赖后没有直接弹框').toBeTruthy()
    expect(dialog()!.textContent).toContain('这个脚本还缺 pandas 和 openpyxl，点一下自动装好。')
    expect(dialog()!.querySelector('[data-repair-advanced]')!.hasAttribute('open')).toBe(false)
    expect(dialog()!.querySelector('[data-dependency-will-install]')!.textContent).toBe('将安装：pandas 和 openpyxl')
    expect(dialogButton('一键修复')).toBeTruthy()
    expect(createJointDependencyPlan).not.toHaveBeenCalled()
    expect(prepareJointDependencies).not.toHaveBeenCalled()
    expect(host.querySelector('[data-script-preparation-sentence]')).toBeNull()
    expect(host.querySelector('[data-repair-line]')).toBeNull()
    expect(host.querySelector('[data-script-recovery]')).toBeNull()
    expect([...host.querySelectorAll('section ul[aria-label]')].map((ul) => ul.getAttribute('aria-label'))).toEqual(['需要修复'])
  })

  it('「稍后」后可再次打开同一修复框，不用再运行脚本；重新打开本身不安装', async () => {
    const offer = offerOf()
    mockProbe.mockResolvedValue(preparationResult(offer))
    await mountAndRun()
    await act(async () => dialogButton('稍后').click())
    await flush()
    expect(dialog()).toBeNull()
    expect(useEnvStore.getState().dependencyPreparation).toBeNull()
    await act(async () => reopen().click())
    await flush()
    expect(dialog()).toBeTruthy()
    expect(useEnvStore.getState().dependencyPreparation).toEqual(offer)
    expect(mockProbe).toHaveBeenCalledTimes(1)
    expect(createJointDependencyPlan).not.toHaveBeenCalled()
  })

  it('点一次绑定计划并发 plan_id + 用户看到的影响摘要；进度留在弹窗，完成后关框并自动重跑一次', async () => {
    await startRepair()
    expect(createJointDependencyPlan).toHaveBeenCalledWith({ script: SCRIPT, target: 'tavotto_managed' })
    expect(prepareJointDependencies).toHaveBeenCalledWith('joint-row', 'shown-digest')
    expect(useDepRepairStore.getState().jointScript).toBe(SCRIPT)
    await act(async () => useDepRepairStore.getState().onProgress(progress('installing')))
    expect(dialog()!.querySelector('[data-repair-line]')!.textContent).toBe('正在安装 pandas 和 openpyxl…（3/4）')
    expect(host.querySelector('[data-repair-line]')).toBeNull()
    mockProbe.mockClear()
    succeedProbe()
    await act(async () => useDepRepairStore.getState().onProgress(progress('done')))
    await flush()
    expect(dialog()).toBeNull()
    expect(mockProbe.mock.calls.map((call) => call[0])).toEqual([SCRIPT])
    expect(host.querySelector('[data-script-preparation]')).toBeNull()
  })

  it('页脚 start 槽的「不准备，直接运行」，然后重跑脚本', async () => {
    mockProbe.mockResolvedValue(preparationResult(offerOf()))
    vi.mocked(skipDependencyPreparation).mockResolvedValue({ ok: true, script: SCRIPT, skipped: true })
    await mountAndRun()
    const skip = dialog()!.querySelector<HTMLButtonElement>('[data-dependency-skip]')!
    // 2026-10-07 设计审计 §10.2：不再埋在「详情」里，是页脚 start 槽的次要入口
    expect(skip.closest('[data-dialog-footer]')).not.toBeNull()
    mockProbe.mockClear()
    succeedProbe()
    await act(async () => skip.click())
    await flush()
    expect(skipDependencyPreparation).toHaveBeenCalledWith(SCRIPT)
    expect(mockProbe.mock.calls.map((call) => call[0])).toEqual([SCRIPT])
  })

  it('安装失败留在弹窗：一句原因 + 重试，重试再绑定一份计划', async () => {
    await startRepair()
    await act(async () => useDepRepairStore.getState().onProgress(progress('failed', {
      error: '连不上', code: 'dependency_network_unavailable',
    })))
    await flush()
    expect(dialog()).toBeTruthy()
    await act(async () => dialogButton('重试').click())
    await flush()
    expect(createJointDependencyPlan).toHaveBeenCalledTimes(2)
  })

  it('找到装齐的用户环境时弹选择框，不擅自创建或安装环境', async () => {
    const offer = offerOf({ user_environments: [{
      id: 'env1', source: 'conda', label: 'sci', ok: true, code: '', support: 'verified',
      python_version: '3.12', matplotlib_version: '3.9', missing: [], satisfies: true,
    }] })
    mockProbe.mockResolvedValue(preparationResult(offer))
    await mountAndRun()
    expect(dialogButton('改用这个环境')).toBeTruthy()
    expect(createJointDependencyPlan).not.toHaveBeenCalled()
  })

  it('绑定计划新增了 numpy：不安装，重新试运行披露所需的全部包', async () => {
    mockProbe.mockResolvedValue(preparationResult(offerOf()))
    vi.mocked(createJointDependencyPlan).mockResolvedValue({
      plan: { ...(plan('joint-row') as object), requirements: ['pandas', 'openpyxl', 'numpy'] } as never,
    })
    await mountAndRun()
    mockProbe.mockClear()
    await act(async () => dialogButton('一键修复').click())
    await flush()
    expect(prepareJointDependencies).not.toHaveBeenCalled()
    expect(mockProbe.mock.calls.map((call) => call[0])).toEqual([SCRIPT])
    expect(useDepRepairStore.getState().jointScript).toBe('')
    expect(dialog()).toBeTruthy()
  })

  it('改用装齐的用户环境后关闭弹窗并重跑发起的脚本', async () => {
    const offer = offerOf({ user_environments: [{
      id: 'env1', source: 'conda', label: 'sci', ok: true, code: '', support: 'verified',
      python_version: '3.12', matplotlib_version: '3.9', missing: [], satisfies: true,
    }] })
    mockProbe.mockResolvedValue(preparationResult(offer))
    vi.mocked(setProjectUserEnvironment).mockResolvedValue({ project: { python: '/envs/sci/bin/python' } } as never)
    await mountAndRun()
    mockProbe.mockClear()
    succeedProbe()
    await act(async () => dialogButton('改用这个环境').click())
    await flush()
    expect(setProjectUserEnvironment).toHaveBeenCalledWith('env1', SCRIPT)
    expect(mockProbe.mock.calls.map((call) => call[0])).toEqual([SCRIPT])
    expect(dialog()).toBeNull()
  })

  describe('安装期间切换项目', () => {
    it('B 上不显示 A 的授权；回到 A 恢复弹窗和进度，并能取消', async () => {
      await startRepair()
      await act(async () => useDepRepairStore.getState().onProgress(progress('installing')))
      await switchTo('pB')
      expect(dialog()).toBeNull()
      await switchTo('pA')
      expect(useScriptRunStore.getState().byScript[SCRIPT], '前提：运行记录确实被清空').toBeUndefined()
      expect(useDepRepairStore.getState().jointScript).toBe(SCRIPT)
      expect(dialog()!.querySelector('[data-repair-line]')!.textContent).toContain('正在安装 pandas 和 openpyxl')
      await act(async () => dialogButton('取消').click())
      expect(cancelJointDependencies).toHaveBeenCalledWith('joint-row')
    })

    it('取消结局仍留在弹窗，能重新授权重试', async () => {
      await startRepair()
      await switchTo('pB')
      await switchTo('pA')
      await act(async () => dialogButton('取消').click())
      expect(cancelJointDependencies).toHaveBeenCalledWith('joint-row')
      await act(async () => useDepRepairStore.getState().onProgress(progress('cancelled', { code: 'dependency_cancelled' })))
      expect(dialogButton('重试')).toBeTruthy()
      await act(async () => dialogButton('重试').click())
      await flush()
      expect(createJointDependencyPlan).toHaveBeenCalledTimes(2)
    })

    it('切走期间装好：回到 A 自动补跑一次；再次切走切回不重复', async () => {
      await startRepair()
      await switchTo('pB')
      mockProbe.mockClear()
      succeedProbe()
      await act(async () => useDepRepairStore.getState().onProgress(progress('done')))
      await flush()
      expect(mockProbe).not.toHaveBeenCalled()
      await switchTo('pA')
      expect(mockProbe.mock.calls.map((call) => call[0])).toEqual([SCRIPT])
      expect(dialog()).toBeNull()
      await switchTo('pB')
      await switchTo('pA')
      expect(mockProbe).toHaveBeenCalledTimes(1)
    })

    it('只有画布渲染的门没有脚本行意图：切走期间装好，切回不额外试运行脚本', async () => {
      mockProbe.mockResolvedValue({ ...preparationResult(offerOf()), error: null, descriptors: [] })
      vi.mocked(createJointDependencyPlan).mockResolvedValue({ plan: plan('joint-row') })
      vi.mocked(prepareJointDependencies).mockResolvedValue({ started: true } as never)
      await mountAndRun()
      await act(async () => useEnvStore.getState().requestDependencyPreparation(offerOf()))
      await act(async () => dialogButton('一键修复').click())
      await flush()
      expect(useDepRepairStore.getState().jointScript).toBe('')
      await switchTo('pB')
      mockProbe.mockClear()
      await act(async () => useDepRepairStore.getState().onProgress(progress('done')))
      await switchTo('pA')
      expect(mockProbe).not.toHaveBeenCalled()
    })
  })
})
