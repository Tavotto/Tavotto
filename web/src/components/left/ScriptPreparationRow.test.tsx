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

const buttonByText = (text: string): HTMLButtonElement => {
  const btn = [...host.querySelectorAll('button')].find((b) => (b.textContent ?? '').includes(text))
  if (!btn) throw new Error(`没有找到按钮: ${text}`)
  return btn as HTMLButtonElement
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
      '这个脚本还缺 pandas 和 openpyxl，点一下自动装好。',
    )
    // 可见的主按钮只有一个；完整需求串收在默认折叠的「详情」里
    expect([...card!.querySelectorAll('button')].filter((b) => !b.closest('details')).map((b) => b.textContent)).toEqual([
      '一键修复',
    ])
    expect(card!.querySelector('details')!.hasAttribute('open')).toBe(false)
    // 「详情」第一段：将安装：全部
    expect(card!.querySelector('[data-script-preparation-will-install]')!.textContent).toBe(
      '将安装：pandas 和 openpyxl',
    )
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
    // 只剩一行进度 + 取消（不再有主按钮）
    const card = host.querySelector('[data-script-preparation]')!
    expect([...card.querySelectorAll('button')].map((x) => x.textContent)).toEqual(['取消'])
    // 安装阶段的进度行说真正在装的整组包（单包修复的同一句），不是泛泛的「正在安装…」
    await act(async () => {
      useDepRepairStore.getState().onProgress({
        plan_id: 'joint-row', state: 'installing', log: '', error: null, code: '', flow: 'joint',
        script: SCRIPT, requirements: ['pandas', 'openpyxl'], target_kind: 'tavotto_managed',
      } as never)
    })
    expect(card.querySelector('[data-repair-line]')!.textContent).toBe('正在安装 pandas 和 openpyxl…（3/4）')
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

  it('「详情」里的「其他方式（备选）」有「不准备，直接运行」：走同一个 skip 接口，然后这一行重跑（#740 的能力）', async () => {
    mockProbe.mockResolvedValue(preparationResult(offerOf()))
    vi.mocked(skipDependencyPreparation).mockResolvedValue({ ok: true, script: SCRIPT, skipped: true })
    await mountAndRun()
    const card = host.querySelector('[data-script-preparation]')!
    const skip = card.querySelector<HTMLButtonElement>('[data-script-preparation-skip]')!
    // 默认折叠：可见区仍只有一句话 + 一个主按钮
    expect(skip.closest('details')!.hasAttribute('open')).toBe(false)
    expect(card.querySelector('details')!.textContent).toContain('其他方式（备选）')
    mockProbe.mockClear()
    mockProbe.mockResolvedValue({ ...preparationResult(offerOf()), error: null, descriptors: [] })
    await act(async () => skip.click())
    await flush()
    expect(skipDependencyPreparation).toHaveBeenCalledWith(SCRIPT)
    expect(mockProbe).toHaveBeenCalledTimes(1)
    expect(mockProbe.mock.calls[0][0]).toBe(SCRIPT)
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

  it('计划超出了用户看到的（绑定回来多了 numpy）：不执行，脚本行重跑一次拿新的披露（Codex #760 P1）', async () => {
    mockProbe.mockResolvedValue(preparationResult(offerOf()))
    vi.mocked(createJointDependencyPlan).mockResolvedValue({
      plan: { ...(plan('joint-row') as object), requirements: ['pandas', 'openpyxl', 'numpy'] } as never,
    })
    await mountAndRun()
    mockProbe.mockClear()
    await act(async () => buttonByText('一键修复').click())
    await flush()
    expect(prepareJointDependencies).not.toHaveBeenCalled()
    expect(mockProbe).toHaveBeenCalledTimes(1) // 按此刻的输入重新披露
    expect(useDepRepairStore.getState().jointScript).toBe('')
  })

  it('授权框里同样：绑定回来的计划超出 offer，不执行、框关掉重排', async () => {
    const offer = offerOf({
      user_environments: [
        {
          id: 'env1', source: 'conda', label: 'sci', ok: false, code: '', support: 'verified',
          python_version: '3.12', matplotlib_version: '3.9', missing: ['pandas'], satisfies: false,
        },
      ],
    })
    vi.mocked(createJointDependencyPlan).mockResolvedValue({
      plan: { ...(plan('joint-dlg') as object), requirements: ['pandas', 'openpyxl', 'numpy'] } as never,
    })
    await mountAndRun()
    await act(async () => useEnvStore.getState().requestDependencyPreparation(offer))
    await act(async () => dialogButton('一键修复').click())
    await flush()
    expect(prepareJointDependencies).not.toHaveBeenCalled()
    expect(useEnvStore.getState().dependencyPreparation).toBeNull()
  })

  it('授权框里改用装齐的用户环境：成功后发起的脚本行重跑（Codex #760 P2）', async () => {
    const offer = offerOf({
      user_environments: [
        {
          id: 'env1', source: 'conda', label: 'sci', ok: true, code: '', support: 'verified',
          python_version: '3.12', matplotlib_version: '3.9', missing: [], satisfies: true,
        },
      ],
    })
    mockProbe.mockResolvedValue(preparationResult(offer))
    vi.mocked(setProjectUserEnvironment).mockResolvedValue({ project: { python: '/envs/sci/bin/python' } } as never)
    await mountAndRun()
    await act(async () => buttonByText('一键修复').click()) // 有装齐的用户环境：打开授权框
    await flush()
    mockProbe.mockClear()
    mockProbe.mockResolvedValue({ ...preparationResult(offer), error: null, descriptors: [] })
    await act(async () => dialogButton('改用这个环境').click())
    await flush()
    expect(setProjectUserEnvironment).toHaveBeenCalledWith('env1', SCRIPT)
    expect(mockProbe).toHaveBeenCalledTimes(1)
    expect(mockProbe.mock.calls[0][0]).toBe(SCRIPT)
  })

  describe('从脚本行发起后切项目再切回（A → B → A）', () => {
    const switchTo = async (id: string) => {
      await act(async () => {
        setCurrentProjectId(id)
        useScriptRunStore.getState().clear()
        useDepRepairStore.getState().clear()
      })
      await flush()
    }
    const startFromRow = async () => {
      setCurrentProjectId('pA')
      mockProbe.mockResolvedValue(preparationResult(offerOf()))
      vi.mocked(createJointDependencyPlan).mockResolvedValue({ plan: plan('joint-row') })
      vi.mocked(prepareJointDependencies).mockResolvedValue({ started: true } as never)
      await mountAndRun()
      await act(async () => buttonByText('一键修复').click())
      await flush()
      await act(async () => {
        useDepRepairStore.getState().onProgress({
          plan_id: 'joint-row', state: 'installing', log: '', error: null, code: '', flow: 'joint',
          script: SCRIPT, requirements: ['pandas', 'openpyxl'], target_kind: 'tavotto_managed',
        } as never)
      })
      await switchTo('pB')
    }
    afterEach(() => setCurrentProjectId(null))

    it('还在装：切回 A，脚本行仍认得这份进度并能取消', async () => {
      await startFromRow()
      await switchTo('pA')
      expect(useDepRepairStore.getState().jointScript).toBe(SCRIPT)
      // 脚本行的运行记录随切项目清空了：先让它回到「要先准备」，进度按归属挂回这一行
      mockProbe.mockResolvedValue(preparationResult(offerOf()))
      await act(async () => host.querySelector<HTMLButtonElement>('button[aria-label$="并发现图"]')!.click())
      await flush()
      const card = host.querySelector('[data-script-preparation]')!
      expect(card.querySelector('[data-repair-line]')!.textContent).toContain('正在安装 pandas 和 openpyxl')
      await act(async () => buttonByText('取消').click())
      expect(cancelJointDependencies).toHaveBeenCalledWith('joint-row')
    })

    it('切走期间装好：切回 A 自动重跑那一行一次，不重复', async () => {
      await startFromRow()
      mockProbe.mockClear()
      mockProbe.mockResolvedValue({ ...preparationResult(offerOf()), error: null, descriptors: [] })
      await act(async () => {
        useDepRepairStore.getState().onProgress({
          plan_id: 'joint-row', state: 'done', log: '', error: null, code: '', flow: 'joint', script: SCRIPT,
        } as never)
      })
      await flush()
      expect(mockProbe).not.toHaveBeenCalled()
      await switchTo('pA')
      expect(mockProbe).toHaveBeenCalledTimes(1)
      expect(mockProbe.mock.calls[0][0]).toBe(SCRIPT)
      await switchTo('pB')
      await switchTo('pA')
      expect(mockProbe).toHaveBeenCalledTimes(1)
    })
  })
})
