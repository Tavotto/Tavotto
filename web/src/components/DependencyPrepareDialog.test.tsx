/**
 * 跑前的那一次授权（U04，ADR 0061 §六）。判据的主语：后端给的 `dependency_preparation_required`
 * 载荷在界面上怎么变成一次授权——
 * ① 要装的包按项目声明的完整形态列出、认不出的 import 单独说、目标默认是后端算的那个；
 * ② 「准备并继续」= 先绑定计划（POST plan）再只发 plan_id（POST prepare）；③ 进度按 state 换文案，
 *   `done` 关框并把「先准备」的面板重新排上；④ blocked 的计划把理由摆出来、不装；⑤ 「稍后」只关框，
 *   载荷留在渲染条目上、错误块的按钮能再打开；⑥ 换项目的旧载荷不弹；⑦ 同一时刻只开一份。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  createJointDependencyPlan: vi.fn(),
  prepareJointDependencies: vi.fn(),
  cancelJointDependencies: vi.fn(),
  skipDependencyPreparation: vi.fn(),
  fetchEngineEnvironment: vi.fn(),
  setProjectUserEnvironment: vi.fn(),
}))

import {
  createJointDependencyPlan,
  DEPENDENCY_PREPARATION_CODE,
  prepareJointDependencies,
  fetchEngineEnvironment,
  setProjectUserEnvironment,
  skipDependencyPreparation,
  type DependencyPreparationOffer,
  type UserEnvironment,
  type JointDependencyPlan,
  type PrivatePythonOffer,
} from '@/lib/api'
import { DependencyPrepareDialog } from '@/components/DependencyPrepareDialog'
import { DependencyPrepareButton } from '@/components/WorkdirRow'
import { i18n, t } from '@/i18n'
import { listJoin } from '@/i18n/format'
import {
  mentionCount,
  repeatedSentences,
  visibleBlocks,
  visiblePrimaryButtons,
  visibleSentenceCount,
} from '@/test/visibleBlocks'
import { PRODUCT_NAME } from '@/lib/brand'
import { setCurrentProjectId } from '@/lib/session'
import { __resetDepRepairParkingForTests, useDepRepairStore } from '@/store/depRepairStore'
import { useEnvStore } from '@/store/envStore'
import { useRenderStore } from '@/store/renderStore'
import { useProjectPreparationStore } from '@/store/projectPreparationStore'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const planMock = vi.mocked(createJointDependencyPlan)
const prepareMock = vi.mocked(prepareJointDependencies)
const envMock = vi.mocked(fetchEngineEnvironment)
const skipMock = vi.mocked(skipDependencyPreparation)
const adoptMock = vi.mocked(setProjectUserEnvironment)
const en = (key: string, values?: Record<string, unknown>) => t(`engine.${key}`, { ns: 'errors', ...values })

const joint = (over: Partial<JointDependencyPlan> = {}): JointDependencyPlan => ({
  plan_version: 1,
  status: 'ready',
  target_kind: 'tavotto_managed',
  script: 'figure.py',
  needed: [],
  missing: [
    { import_name: 'tabulate', distribution: 'tabulate', resolution_source: 'project_declared', declared: true, specifiers: ['==0.9.0'], via: [] },
    { import_name: 'six', distribution: 'six', resolution_source: 'project_declared', declared: true, specifiers: [], via: [] },
  ],
  satisfied: [],
  unknown: ['zzz_private'],
  possible: [],
  requirements: ['six==1.17.0', 'tabulate[widechars]==0.9.0'],
  constraints: ['sortedcontainers==2.4.0'],
  require_hashes: false,
  adapter: ['matplotlib>=3.8,<3.12', 'numpy>=1.24,<3'],
  blocked: [],
  selection: { selected_groups: ['requirements.txt'], available_groups: ['requirements.txt'], unselected_groups: [], skipped_marker: [] },
  identity: 'abc',
  ...over,
})

const offer = (over: Partial<DependencyPreparationOffer> = {}): DependencyPreparationOffer => ({
  code: DEPENDENCY_PREPARATION_CODE,
  script: 'figure.py',
  plan: joint(),
  target_kind: 'tavotto_managed',
  targets: [
    { kind: 'tavotto_managed', venv: '', python: '', modifies_user_environment: false, creates_environment: true, available: true, reason: '' },
  ],
  rounds_remaining: 3,
  skipped: false,
  ...over,
})

const withProjectVenv = (): DependencyPreparationOffer =>
  offer({
    target_kind: 'project_venv',
    targets: [
      { kind: 'project_venv', venv: '.venv', python: '.venv/bin/python', modifies_user_environment: true, creates_environment: false, available: true, reason: '' },
      { kind: 'tavotto_managed', venv: '', python: '', modifies_user_environment: false, creates_environment: true, available: true, reason: '' },
    ],
  })

let host: HTMLDivElement
let root: Root
async function render(node: React.ReactNode) {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => {
    root.render(node)
  })
  await act(async () => {})
}
const text = () => document.body.textContent ?? ''
const dialog = () => document.querySelector('[data-dialog="dependency-prepare"]')
const radio = (kind: string) =>
  document.querySelector(`[data-dependency-option="${kind}"] input[type="radio"]`) as HTMLInputElement | null
const button = (label: string) =>
  [...document.querySelectorAll('button')].find((b) => b.textContent?.trim() === label) as
    | HTMLButtonElement
    | undefined

beforeEach(() => {
  // 模块级的停放槽活得比 zustand reset 长：每条用例从空的开始（互不串）
  __resetDepRepairParkingForTests()
  planMock.mockReset()
  prepareMock.mockReset()
  envMock.mockReset()
  skipMock.mockReset()
  adoptMock.mockReset()
  useEnvStore.setState({ dependencyPreparation: null })
  envMock.mockResolvedValue({ ok: true, project: { open: true } } as never)
  setCurrentProjectId('p1')
  useDepRepairStore.getState().reset()
})
afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
  document.body.innerHTML = ''
  await i18n.changeLanguage('zh-CN')
})

describe('DependencyPrepareDialog', () => {
  it('没有载荷时什么都不渲染', async () => {
    await render(<DependencyPrepareDialog />)
    expect(dialog()).toBeNull()
  })

  it('列出要装的包（项目声明的完整形态）、认不出的 import、目标默认是后端算的那个', async () => {
    await render(<DependencyPrepareDialog />)
    await act(async () => useEnvStore.getState().requestDependencyPreparation(offer()))
    expect(dialog()).not.toBeNull()
    expect(text()).toContain(en('oneClickSentence', { packages: listJoin(['six', 'tabulate']) }))
    expect(text()).toContain('tabulate[widechars]==0.9.0')
    expect(text()).toContain('six==1.17.0')
    expect(text()).toContain(en('dependencyPrepareUnknown', { modules: 'zzz_private' }))
    expect(text()).toContain(en('dependencyPrepareConstraints', { count: 1 }))
    expect(radio('tavotto_managed')!.checked).toBe(true)
    expect(radio('project_venv')).toBeNull()
  })

  it('项目 venv 就是此刻选中的解释器时：它是默认目标，文案说清会改用户环境', async () => {
    await render(<DependencyPrepareDialog />)
    await act(async () => useEnvStore.getState().requestDependencyPreparation(withProjectVenv()))
    expect(radio('project_venv')!.checked).toBe(true)
    expect(radio('tavotto_managed')!.checked).toBe(false)
    expect(text()).toContain(en('dependencyTargetHint_project_venv', { venv: '.venv' }))
    // 会改用户环境的那一档：目标单选摆在外面、不叫「一键修复」，要用户看清再点；每个选项的说明压成一句短语，
    // 需求串 / 下载 / 「不准备，直接运行」照样在「详情」里
    const d = dialog()!
    expect(d.querySelector('[data-dependency-target]')!.closest('details')).toBeNull()
    expect(d.querySelector('[data-dependency-requirements]')!.closest('[data-repair-advanced]')).toBeTruthy()
    expect(visibleSentenceCount(d)).toBeLessThanOrEqual(1)
    expect(visiblePrimaryButtons(d)).toBe(1)
    // 看得见的逐块钉死：标题、两个选项各「名字 + 一句短语」、折叠标题、底部两颗按钮——多一块说明就红
    expect(visibleBlocks(d).map((b) => b.text)).toEqual([
      en('dependencyPrepareTitle', { count: 2 }),
      en('dependencyTarget_project_venv'),
      en('dependencyTargetHint_project_venv', { venv: '.venv' }),
      en('dependencyTarget_tavotto_managed', { product: PRODUCT_NAME }),
      en('dependencyTargetHint_tavotto_managed'),
      en('repairAdvanced'),
      en('dependencyPrepareLater'),
      en('dependencyPrepareRun'),
    ])
    expect(button(en('oneClickRepair'))).toBeUndefined()
    expect(button(en('dependencyPrepareRun'))).toBeDefined()
  })

  it('一键修复：默认可见的只有一句话 +「详情」+「稍后」「一键修复」（按可见元素数）；其余都在「详情」里', async () => {
    await render(<DependencyPrepareDialog />)
    await act(async () => useEnvStore.getState().requestDependencyPreparation(offer()))
    expect(visibleBlocks(dialog()!)).toEqual([
      { tag: 'h2', text: en('oneClickSentence', { packages: listJoin(['six', 'tabulate']) }) },
      { tag: 'summary', text: en('repairAdvanced') },
      { tag: 'button', text: en('dependencyPrepareLater') },
      { tag: 'button', text: en('oneClickRepair') },
    ])
    expect(button(en('oneClickRepair'))!.className).toContain('text-white')
    expect(visibleSentenceCount(dialog()!)).toBe(1)
    expect(visiblePrimaryButtons(dialog()!)).toBe(1)
    const advanced = document.querySelector('[data-repair-advanced]') as HTMLDetailsElement
    expect(advanced.querySelector('[data-one-click-cost]')!.textContent).toBe(en('repairFactNetwork'))
    expect(advanced.querySelector('[data-dependency-requirements]')).toBeTruthy()
    expect(advanced.querySelector('[data-dependency-target]')).toBeTruthy()
    // 「不准备，直接运行」不再是并列的次按钮，收在详情里
    expect(advanced.querySelector('[data-dependency-skip]')).toBeTruthy()
  })

  it('一键修复框展开「详情」后没有重复的句子，下载 / 联网 / 不改动各只说一次', async () => {
    const pp = {
      id: 'pbs', version: '3.13.15', target: 'darwin-arm64', source_host: 'github.com',
      download_bytes: 25 * 1048576, required: true, cached: false, network_required: true, origin: 'download' as const,
    }
    await render(<DependencyPrepareDialog />)
    await act(async () =>
      useEnvStore.getState().requestDependencyPreparation(
        offer({ private_python: pp, targets: [{ ...offer().targets[0], private_python: pp }] }),
      ),
    )
    const details = document.querySelector('[data-repair-advanced]')!
    expect(repeatedSentences(details)).toEqual([])
    expect(mentionCount(details, 'MB')).toBe(1)
    expect(mentionCount(details, '联网')).toBe(1)
    expect(mentionCount(details, '不改动')).toBe(1)
  })

  it('跑前授权框选了项目 venv：乐观进度就带着目标，只有两步（Codex #742）', async () => {
    planMock.mockResolvedValue({ plan: { plan_id: 'jpv', target_kind: 'project_venv', script: 'figure.py', requirements: [] } as never })
    prepareMock.mockImplementation(() => new Promise(() => {}))
    await render(<DependencyPrepareDialog />)
    await act(async () => useEnvStore.getState().requestDependencyPreparation(withProjectVenv()))
    await act(async () => button(en('dependencyPrepareRun'))!.click())
    await act(async () => {})
    expect(useDepRepairStore.getState().progress!.target_kind).toBe('project_venv')
    expect(document.querySelector('[data-repair-line]')!.textContent).toBe(
      `${en('dependencyPrepareState_preparing')}${en('repairStep', { n: 1, total: 2 })}`,
    )
  })

  it('联合准备换用了 PyPI 镜像（#743 真事务实测的快照）：只在折叠的「详情」里说一句；没有这个键时不说', async () => {
    planMock.mockResolvedValue({ plan: { plan_id: 'QGb0roUeTiyBLmNa1lKlDp8bKSDtZYfU', requirements: [] } as never })
    prepareMock.mockResolvedValue({ started: true } as never)
    await render(<DependencyPrepareDialog />)
    await act(async () => useEnvStore.getState().requestDependencyPreparation(offer()))
    await act(async () => button(en('oneClickRepair'))!.click())
    await act(async () => {})
    // 照抄 #743 @ 7cc13c53d 联合准备（受管环境换代）真事务里的快照
    const snapshot = JSON.parse(
      '{"plan_id":"QGb0roUeTiyBLmNa1lKlDp8bKSDtZYfU","state":"installing","code":"","error":null,"result":null,"target_kind":"tavotto_managed","script":"figure.py","requirements":["tavotto-test-alpha"],"flow":"joint","pypi_mirror":"https://pypi.tuna.tsinghua.edu.cn/simple","log":"…"}',
    )
    // 先来一条没有这个键的：安静，不说也不报错
    await act(async () => useDepRepairStore.getState().onProgress({ ...snapshot, pypi_mirror: undefined }))
    expect(document.querySelector('[data-repair-pypi-mirror]')).toBeNull()
    expect(document.querySelector('[data-repair-line]')).toBeTruthy()
    await act(async () => useDepRepairStore.getState().onProgress(snapshot))
    const note = document.querySelector('[data-repair-pypi-mirror]')!
    expect(note.textContent).toBe(en('repairPypiMirror', { mirror: 'https://pypi.tuna.tsinghua.edu.cn/simple' }))
    expect(note.closest('details')!.open).toBe(false)
    // 默认可见区不变：一句（标题）、一行进度、「详情」、「取消」
    expect(visibleBlocks(dialog()!).map((b) => b.tag)).toEqual(['h2', 'p', 'summary', 'button'])
  })

  it('一键修复进行中：只剩一行进度', async () => {
    planMock.mockResolvedValue({ plan: { plan_id: 'jp9', requirements: [] } as never })
    prepareMock.mockResolvedValue({ started: true } as never)
    await render(<DependencyPrepareDialog />)
    await act(async () => useEnvStore.getState().requestDependencyPreparation(offer()))
    await act(async () => button(en('oneClickRepair'))!.click())
    await act(async () => {})
    await act(async () =>
      useDepRepairStore.getState().onProgress({
        plan_id: 'jp9', state: 'installing', log: '', error: null, code: '', flow: 'joint',
      } as never),
    )
    const tags = visibleBlocks(dialog()!).map((b) => b.tag)
    // 一行进度 + 折叠的「详情」+「取消」
    expect(tags).toEqual(['h2', 'p', 'summary', 'button'])
    expect(document.querySelector('[data-repair-line]')!.textContent).toBe(
      `${en('dependencyPrepareState_installing')}${en('repairStep', { n: 3, total: 4 })}`,
    )
  })

  it('安装阶段的进度行说真正在装的整组包；「详情」第一段是「将安装：…」', async () => {
    planMock.mockResolvedValue({ plan: { plan_id: 'jp10', requirements: ['six', 'tabulate'] } as never })
    prepareMock.mockResolvedValue({ started: true } as never)
    await render(<DependencyPrepareDialog />)
    await act(async () => useEnvStore.getState().requestDependencyPreparation(offer()))
    expect(document.querySelector('[data-dependency-will-install]')!.textContent).toBe(
      en('repairWillInstall', { requirement: listJoin(['six==1.17.0', 'tabulate[widechars]==0.9.0']) }),
    )
    await act(async () => button(en('oneClickRepair'))!.click())
    await act(async () => {})
    await act(async () =>
      useDepRepairStore.getState().onProgress({
        plan_id: 'jp10', state: 'installing', log: '', error: null, code: '', flow: 'joint',
        requirements: ['six', 'tabulate'],
      } as never),
    )
    expect(document.querySelector('[data-repair-line]')!.textContent).toBe(
      `${en('repairInstalling', { module: listJoin(['six', 'tabulate']) })}${en('repairStep', { n: 3, total: 4 })}`,
    )
  })

  it('干净机器、什么包都不缺（requirements 为空）：那一句说准备环境本身，不写「还缺 」（Codex #742）', async () => {
    const pp = {
      id: 'pbs', version: '3.13.15', target: 'darwin-arm64', source_host: 'github.com',
      download_bytes: 25 * 1048576, required: true, cached: false, network_required: true, origin: 'download' as const,
    }
    await render(<DependencyPrepareDialog />)
    const clean = (private_python: typeof pp | null) =>
      offer({
        clean_machine: true,
        private_python,
        plan: joint({ status: 'nothing_needed', missing: [], unknown: [], requirements: [], constraints: [] }),
      })
    await act(async () => useEnvStore.getState().requestDependencyPreparation(clean(pp)))
    const title = () => dialog()!.querySelector('h2')!.textContent
    expect(title()).toBe(en('oneClickSentenceEnvDownload', { mb: 25 }))
    expect(visibleSentenceCount(dialog()!)).toBe(1)
    await act(async () => useEnvStore.getState().dismissDependencyPreparation())
    await act(async () =>
      useEnvStore.getState().requestDependencyPreparation(clean({ ...pp, origin: 'bundled' as never, download_bytes: 0 })),
    )
    expect(title()).toBe(en('oneClickSentenceEnv'))
  })

  it('只准备环境（requirements 为空）：「详情」里不说「装包需要联网」，只说准备哪份 Python（Codex #742）', async () => {
    const pp = {
      id: 'pbs', version: '3.13.15', target: 'darwin-arm64', source_host: 'github.com',
      download_bytes: 25 * 1048576, required: true, cached: false, network_required: true, origin: 'download' as const,
    }
    const clean = (private_python: PrivatePythonOffer | null) =>
      offer({
        clean_machine: true,
        private_python,
        plan: joint({ status: 'nothing_needed', missing: [], unknown: [], requirements: [], constraints: [] }),
      })
    const cost = () => document.querySelector('[data-one-click-cost]')?.textContent ?? null
    const details = () => document.querySelector('[data-repair-advanced]')!.textContent ?? ''
    await render(<DependencyPrepareDialog />)
    for (const [pp2, expected] of [
      [pp, en('repairFactEnvDownload', { product: PRODUCT_NAME, version: '3.13.15', mb: 25 })],
      [{ ...pp, origin: 'bundled' as const, download_bytes: 0 }, en('repairFactEnvBundled', { product: PRODUCT_NAME, version: '3.13.15' })],
      [{ ...pp, origin: 'cached' as const, download_bytes: 0 }, en('repairFactEnvCached', { product: PRODUCT_NAME, version: '3.13.15' })],
      [null, null],
    ] as const) {
      await act(async () => useEnvStore.getState().dismissDependencyPreparation())
      await act(async () => useEnvStore.getState().requestDependencyPreparation(clean(pp2)))
      expect(cost(), JSON.stringify(pp2)).toBe(expected)
      // 没有要装的包：「装包」的联网说明一个都不许出现（受管环境那条与项目 venv 那条都不说）
      for (const said of [en('repairFactNetwork'), '装包需要联网', '装包也需要联网']) expect(details()).not.toContain(said)
      expect(details()).not.toContain(en('dependencyPrepareNetwork'))
    }
  })

  it('私有 Python 的披露跟着选中的目标走：选了项目 venv 就不说要下载 / 供应 Python（Codex #742）', async () => {
    const pp = {
      id: 'pbs', version: '3.13.15', target: 'darwin-arm64', source_host: 'github.com',
      download_bytes: 25 * 1048576, required: true, cached: false, network_required: true, origin: 'download' as const,
    }
    const managedWithPython = { ...offer().targets[0], private_python: pp }
    const venvTarget = withProjectVenv().targets[0]
    // ① 一键修复框里把目标从受管环境换成项目 venv：标题与「详情」都不再提下载
    await render(<DependencyPrepareDialog />)
    await act(async () =>
      useEnvStore.getState().requestDependencyPreparation(offer({ targets: [managedWithPython, venvTarget] })),
    )
    expect(dialog()!.querySelector('h2')!.textContent).toContain('25 MB')
    expect(document.querySelector('[data-dependency-private-python]')).not.toBeNull()
    await act(async () => radio('project_venv')!.click())
    expect(dialog()!.querySelector('h2')!.textContent).not.toContain('MB')
    expect(document.querySelector('[data-dependency-private-python]')).toBeNull()
    // 项目 venv 不下载、不供应 Python：「要下载什么」那一条整条不出现，换成项目环境那句联网说明
    expect(document.querySelector('[data-one-click-cost]')).toBeNull()
    expect(text()).toContain(en('dependencyPrepareNetwork'))
    // ② 默认目标就是项目 venv：「详情」里不说受管目标的那份 Python
    await act(async () => useEnvStore.getState().dismissDependencyPreparation())
    await act(async () =>
      useEnvStore.getState().requestDependencyPreparation(
        offer({ target_kind: 'project_venv', targets: [venvTarget, managedWithPython] }),
      ),
    )
    expect(radio('project_venv')!.checked).toBe(true)
    expect(document.querySelector('[data-dependency-private-python]')).toBeNull()
  })

  it('一键修复要下载私有 Python 时说大小；安装包自带时不提下载', async () => {
    const pp = {
      id: 'pbs', version: '3.13.15', target: 'darwin-arm64', source_host: 'github.com',
      download_bytes: 25 * 1048576, required: true, cached: false, network_required: true,
    }
    await render(<DependencyPrepareDialog />)
    await act(async () =>
      useEnvStore.getState().requestDependencyPreparation(
        offer({ targets: [{ ...offer().targets[0], private_python: { ...pp, origin: 'download' } }] }),
      ),
    )
    expect(document.querySelector('[data-one-click-cost]')!.textContent).toBe(
      en('repairFactDownload', { version: '3.13.15', mb: 25, product: PRODUCT_NAME }),
    )
    // 大小放进标题那一句里（括号），仍是一句
    expect(dialog()!.querySelector('h2')!.textContent).toBe(
      en('oneClickSentenceDownload', { packages: listJoin(['six', 'tabulate']), mb: 25 }),
    )
    expect(visibleSentenceCount(dialog()!)).toBe(1)
    // 同一时刻只开一份：先收掉这一份
    await act(async () => useEnvStore.getState().dismissDependencyPreparation())
    await act(async () =>
      useEnvStore.getState().requestDependencyPreparation(
        offer({ targets: [{ ...offer().targets[0], private_python: { ...pp, origin: 'bundled', download_bytes: 0 } }] }),
      ),
    )
    expect(document.querySelector('[data-one-click-cost]')!.textContent).toBe(
      en('repairFactBundled', { version: '3.13.15', product: PRODUCT_NAME }),
    )
    expect(dialog()!.querySelector('h2')!.textContent).not.toContain('MB')
    expect(document.querySelector('[data-dependency-private-python]')!.textContent).toBe(
      en('repairFactBundled', { version: '3.13.15', product: PRODUCT_NAME }),
    )
  })

  it('「准备并继续」= 先绑定计划再只发 plan_id；进度到 done 关框并把「先准备」的面板重新排上', async () => {
    planMock.mockResolvedValue({
      plan: {
        plan_id: 'jp1', script: 'figure.py', target_kind: 'tavotto_managed', python: '', requirements: ['six==1.17.0', 'tabulate[widechars]==0.9.0'],
        constraints: [], require_hashes: false, adapter: [], identity: 'abc', needed_imports: ['six', 'tabulate'], groups: [],
        modifies_user_environment: false, creates_environment: true, network_required: true, expires_at: 0, joint: joint(),
      },
    })
    prepareMock.mockResolvedValue({ started: true, plan_id: 'jp1', state: 'preparing', log: '', error: null, code: '' })
    useRenderStore.setState({
      byKey: {
        k: {
          ...(useRenderStore.getState().byKey.k ?? ({} as never)),
          fileId: 'figure.pdf', status: 'error', code: DEPENDENCY_PREPARATION_CODE,
          lastPatches: '[]', wantPatches: '[]', stale: false,
        } as never,
      },
      tracked: {},
    })
    await render(<DependencyPrepareDialog />)
    await act(async () => useEnvStore.getState().requestDependencyPreparation(offer()))
    await act(async () => button(en('oneClickRepair'))!.click())
    await act(async () => {})
    expect(planMock).toHaveBeenCalledTimes(1)
    expect(planMock).toHaveBeenCalledWith({ script: 'figure.py', target: 'tavotto_managed' })
    expect(prepareMock).toHaveBeenCalledWith('jp1')
    // 进度按 state 换文案
    await act(async () =>
      useDepRepairStore.getState().onProgress({ plan_id: 'jp1', state: 'installing', log: '', error: null, code: '', flow: 'joint' }),
    )
    expect(text()).toContain(en('dependencyPrepareState_installing'))
    expect(button(en('dependencyPrepareCancel'))).toBeDefined()
    await act(async () =>
      useDepRepairStore.getState().onProgress({ plan_id: 'jp1', state: 'done', log: '', error: null, code: '', flow: 'joint', committed: true }),
    )
    expect(useEnvStore.getState().dependencyPreparation).toBeNull()
    expect(dialog()).toBeNull()
    expect(useRenderStore.getState().byKey.k.stale, '没重新排上').toBe(true)
  })

  it('别的计划的进度不认：另一个标签页 / 项目的联合准备装完，这里的框不关、渲染不重排', async () => {
    planMock.mockResolvedValue({
      plan: {
        plan_id: 'jp-mine', script: 'figure.py', target_kind: 'tavotto_managed', python: '', requirements: ['six==1.17.0'],
        constraints: [], require_hashes: false, adapter: [], identity: 'abc', needed_imports: ['six'], groups: [],
        modifies_user_environment: false, creates_environment: true, network_required: true, expires_at: 0, joint: joint(),
      },
    })
    prepareMock.mockResolvedValue({ started: true, plan_id: 'jp-mine', state: 'preparing', log: '', error: null, code: '' })
    useRenderStore.setState({
      byKey: {
        k: {
          ...(useRenderStore.getState().byKey.k ?? ({} as never)),
          fileId: 'figure.pdf', status: 'error', code: DEPENDENCY_PREPARATION_CODE,
          lastPatches: '[]', wantPatches: '[]', stale: false,
        } as never,
      },
      tracked: {},
    })
    await render(<DependencyPrepareDialog />)
    await act(async () => useEnvStore.getState().requestDependencyPreparation(offer()))
    await act(async () => button(en('oneClickRepair'))!.click())
    await act(async () => {})
    // 同一条广播上来了别人的计划：installing 不换进度、done 不关框、不重排
    await act(async () =>
      useDepRepairStore.getState().onProgress({ plan_id: 'jp-theirs', state: 'installing', log: '', error: null, code: '', flow: 'joint' }),
    )
    expect(useDepRepairStore.getState().progress?.plan_id).toBe('jp-mine')
    expect(useDepRepairStore.getState().progress?.state).toBe('preparing')
    await act(async () =>
      useDepRepairStore.getState().onProgress({ plan_id: 'jp-theirs', state: 'done', log: '', error: null, code: '', flow: 'joint', committed: true }),
    )
    expect(useEnvStore.getState().dependencyPreparation).not.toBeNull()
    expect(dialog()).not.toBeNull()
    expect(useDepRepairStore.getState().jointPlan?.plan_id).toBe('jp-mine')
    expect(useRenderStore.getState().byKey.k.stale, '别人的 done 把这里的渲染重排了').toBe(false)
    // 自己的到了才算
    await act(async () =>
      useDepRepairStore.getState().onProgress({ plan_id: 'jp-mine', state: 'done', log: '', error: null, code: '', flow: 'joint', committed: true }),
    )
    expect(dialog()).toBeNull()
    expect(useRenderStore.getState().byKey.k.stale).toBe(true)
  })

  it('失败按 code 换文案、可重试；取消也是明确终态', async () => {
    planMock.mockResolvedValue({
      plan: { plan_id: 'jp2', script: 'figure.py', target_kind: 'tavotto_managed', python: '', requirements: [], constraints: [], require_hashes: false, adapter: [], identity: 'x', needed_imports: [], groups: [], modifies_user_environment: false, creates_environment: true, network_required: true, expires_at: 0, joint: joint() },
    })
    prepareMock.mockResolvedValue({ started: true, plan_id: 'jp2', state: 'preparing', log: '', error: null, code: '' })
    await render(<DependencyPrepareDialog />)
    await act(async () => useEnvStore.getState().requestDependencyPreparation(offer()))
    await act(async () => button(en('oneClickRepair'))!.click())
    await act(async () =>
      useDepRepairStore.getState().onProgress({ plan_id: 'jp2', state: 'failed', log: '', error: '', code: 'dependency_hash_mismatch', flow: 'joint' }),
    )
    expect(text()).toContain(t('engine.repairError.dependency_hash_mismatch', { ns: 'errors' }))
    expect(button(en('dependencyPrepareRetry'))).toBeDefined()
    expect(dialog()).not.toBeNull()
  })

  it('blocked 的计划：理由摆出来、不装', async () => {
    planMock.mockRejectedValue(
      Object.assign(new Error('blocked'), {
        body: { code: 'dependency_plan_blocked', joint: joint({ status: 'blocked', blocked: [{ code: 'dependency_conflict', conflicts: [{ name: 'six', specifiers: ['==1.16.0', '==1.17.0'], reasons: ['x'] }] }] }) },
      }),
    )
    await render(<DependencyPrepareDialog />)
    await act(async () => useEnvStore.getState().requestDependencyPreparation(offer()))
    await act(async () => button(en('oneClickRepair'))!.click())
    await act(async () => {})
    expect(prepareMock).not.toHaveBeenCalled()
    expect(text()).toContain(en('dependencyBlocked_dependency_conflict'))
    expect(text()).toContain(t('engine.repairError.dependency_plan_blocked', { ns: 'errors' }))
  })

  it('「稍后」只关框；载荷留在渲染条目上，错误块的按钮能再打开', async () => {
    const payload = offer()
    await render(
      <>
        <DependencyPrepareDialog />
        <DependencyPrepareButton offer={payload} />
      </>,
    )
    await act(async () => useEnvStore.getState().requestDependencyPreparation(payload))
    await act(async () => button(en('dependencyPrepareLater'))!.click())
    expect(dialog()).toBeNull()
    expect(useEnvStore.getState().dependencyPreparation).toBeNull()
    await act(async () => button(en('dependencyPrepareOpen'))!.click())
    expect(dialog()).not.toBeNull()
  })

  it('「不准备，直接运行」= 明确的 skip：POST 一次、关框、把那次「先准备」的面板重新排上', async () => {
    skipMock.mockResolvedValue({ ok: true, script: 'figure.py', skipped: true })
    useRenderStore.setState({
      byKey: {
        k: {
          ...(useRenderStore.getState().byKey.k ?? ({} as never)),
          fileId: 'figure.pdf', status: 'error', code: DEPENDENCY_PREPARATION_CODE,
          lastPatches: '[]', wantPatches: '[]', stale: false,
        } as never,
      },
      tracked: {},
    })
    await render(<DependencyPrepareDialog />)
    await act(async () => useEnvStore.getState().requestDependencyPreparation(offer()))
    await act(async () => button(en('dependencyPrepareSkip'))!.click())
    await act(async () => {})
    expect(skipMock).toHaveBeenCalledWith('figure.py')
    expect(planMock).not.toHaveBeenCalled()
    expect(dialog()).toBeNull()
    expect(useRenderStore.getState().byKey.k.stale, '没重新排上').toBe(true)
  })

  it('授权框是薄展示适配器（T09b）：装完 / 明确跳过都让准备面板里空闲的会话只读地重新检查，一次，不认领 run', async () => {
    const recheck = vi.fn()
    const real = useProjectPreparationStore.getState().recheckIdle
    useProjectPreparationStore.setState({ recheckIdle: recheck })
    try {
      planMock.mockResolvedValue({
        plan: {
          plan_id: 'jp7', script: 'figure.py', target_kind: 'tavotto_managed', python: '', requirements: ['six==1.17.0'],
          constraints: [], require_hashes: false, adapter: [], identity: 'abc', needed_imports: ['six'], groups: [],
          modifies_user_environment: false, creates_environment: true, network_required: true, expires_at: 0, joint: joint(),
        },
      })
      prepareMock.mockResolvedValue({ started: true, plan_id: 'jp7', state: 'preparing', log: '', error: null, code: '' })
      await render(<DependencyPrepareDialog />)
      await act(async () => useEnvStore.getState().requestDependencyPreparation(offer()))
      await act(async () => button(en('oneClickRepair'))!.click())
      await act(async () => {})
      await act(async () =>
        useDepRepairStore.getState().onProgress({ plan_id: 'jp7', state: 'installing', log: '', error: null, code: '', flow: 'joint' }),
      )
      expect(recheck).not.toHaveBeenCalled() // 还在装：会话不动
      await act(async () =>
        useDepRepairStore.getState().onProgress({ plan_id: 'jp7', state: 'done', log: '', error: null, code: '', flow: 'joint', committed: true }),
      )
      expect(recheck).toHaveBeenCalledTimes(1)
      // 明确跳过同样只是让会话重新检查
      recheck.mockClear()
      skipMock.mockResolvedValue({ ok: true, script: 'figure.py', skipped: true })
      await act(async () => useEnvStore.getState().requestDependencyPreparation(offer()))
      await act(async () => button(en('dependencyPrepareSkip'))!.click())
      await act(async () => {})
      expect(recheck).toHaveBeenCalledTimes(1)
    } finally {
      useProjectPreparationStore.setState({ recheckIdle: real })
    }
  })

  it('这台电脑没有可用的 Python：受管目标那一行把「将先下载 N MB」说出口；有缓存时说不联网（U05）', async () => {
    const privatePython = {
      id: 'cpython-3.13.15-7d50bb42813a',
      version: '3.13.15',
      target: 'macos-arm64',
      download_bytes: 25304407,
      source_host: 'github.com',
      required: true,
      cached: false,
      network_required: true,
    }
    const clean = (over: Partial<typeof privatePython>) =>
      offer({
        targets: [
          {
            kind: 'tavotto_managed',
            venv: '',
            python: '',
            modifies_user_environment: false,
            creates_environment: true,
            available: true,
            reason: '',
            private_python: { ...privatePython, ...over },
          },
        ],
        private_python: { ...privatePython, ...over },
      })
    await render(<DependencyPrepareDialog />)
    await act(async () => useEnvStore.getState().requestDependencyPreparation(clean({})))
    expect(document.querySelector('[data-dependency-private-python]')).not.toBeNull()
    expect(text()).toContain(en('repairFactDownload', { version: '3.13.15', mb: 24, product: PRODUCT_NAME }))
    expect(radio('tavotto_managed')!.disabled).toBe(false)
    // 同一时刻只开一份：换载荷要先关掉这一份
    await act(async () => useEnvStore.setState({ dependencyPreparation: null }))
    await act(async () =>
      useEnvStore.getState().requestDependencyPreparation(
        clean({ cached: true, download_bytes: 0, network_required: false }),
      ),
    )
    expect(text()).toContain(en('repairFactCached', { version: '3.13.15', product: PRODUCT_NAME }))
    expect(text()).not.toContain('MB')
    // 别的项目已经把私有 Python 供应好了（required=false、零字节）：本项目照样要建自己的一代，来源照样说出口
    await act(async () => useEnvStore.setState({ dependencyPreparation: null }))
    await act(async () =>
      useEnvStore.getState().requestDependencyPreparation(
        clean({ required: false, cached: true, download_bytes: 0, network_required: false }),
      ),
    )
    expect(text()).toContain(en('repairFactCached', { version: '3.13.15', product: PRODUCT_NAME }))
    expect(radio('tavotto_managed')!.disabled).toBe(false)
    // 没有这一段（有基础解释器）时一个字都不出现
    await act(async () => useEnvStore.setState({ dependencyPreparation: null }))
    await act(async () => useEnvStore.getState().requestDependencyPreparation(offer()))
    expect(document.querySelector('[data-dependency-private-python]')).toBeNull()
  })

  it('换了项目的旧载荷不弹；同一时刻只开一份', async () => {
    await render(<DependencyPrepareDialog />)
    await act(async () => useEnvStore.getState().requestDependencyPreparation(offer(), 'p0'))
    expect(dialog()).toBeNull()
    await act(async () => useEnvStore.getState().requestDependencyPreparation(offer(), 'p1'))
    await act(async () => useEnvStore.getState().requestDependencyPreparation(offer({ script: 'other.py' }), 'p1'))
    expect(useEnvStore.getState().dependencyPreparation?.script).toBe('figure.py')
  })
})

// ------------------------------------------------------------------ 用户自己的环境（ADR 0079）

const userEnv = (over: Partial<UserEnvironment> = {}): UserEnvironment => ({
  id: 'e1',
  source: 'conda',
  label: 'lab',
  ok: true,
  code: '',
  support: 'verified',
  python_version: '3.12.4',
  matplotlib_version: '3.10.0',
  missing: [],
  satisfies: true,
  ...over,
})
const envRadio = (source: string) =>
  document.querySelector(`[data-user-env="${source}"] input[type="radio"]`) as HTMLInputElement | null

describe('DependencyPrepareDialog：用户自己的环境', () => {
  it('装齐的环境排在安装目标前面并预选；「改用这个环境」只交 id 与脚本，成功后关框、重排「先准备」的面板', async () => {
    adoptMock.mockResolvedValue({ ok: true, project: { open: true } } as never)
    useRenderStore.setState({
      byKey: {
        k: {
          ...(useRenderStore.getState().byKey.k ?? ({} as never)),
          fileId: 'figure.pdf', status: 'error', code: DEPENDENCY_PREPARATION_CODE,
          lastPatches: '[]', wantPatches: '[]', stale: false,
        } as never,
      },
      tracked: {},
    })
    await render(<DependencyPrepareDialog />)
    await act(async () =>
      useEnvStore.getState().requestDependencyPreparation(
        offer({
          user_environments: [
            userEnv({ id: 'best', source: 'login_shell', label: '' }),
            userEnv({ id: 'second', source: 'conda', label: 'lab' }),
            userEnv({ id: 'part', source: 'pyenv', label: '3.11.9', missing: ['six'], satisfies: false }),
          ],
        }),
      ),
    )
    expect(envRadio('login_shell')!.checked, '后端排第一的预选').toBe(true)
    expect(text(), '说明换成「这台电脑上已有装好的环境」').toContain(en('userEnvBody', { script: 'figure.py' }))
    expect(envRadio('conda')!.checked).toBe(false)
    expect(radio('tavotto_managed')!.checked).toBe(false)
    expect(text()).toContain(en('userEnvSource_login_shell'))
    expect(text()).toContain(en('userEnvSource_conda', { label: 'lab' }))
    expect(text()).toContain(en('userEnvMissing', { packages: 'six' }))
    // 选了用户环境：主按钮是「改用这个环境」，联网那句不说（不装东西）
    expect(button(en('dependencyPrepareRun'))).toBeUndefined()
    expect(text()).not.toContain(en('dependencyPrepareNetwork'))
    await act(async () => envRadio('conda')!.click())
    await act(async () => button(en('userEnvUse'))!.click())
    await act(async () => {})
    expect(adoptMock).toHaveBeenCalledWith('second', 'figure.py')
    expect(planMock).not.toHaveBeenCalled()
    expect(useEnvStore.getState().dependencyPreparation).toBeNull()
    expect(useRenderStore.getState().byKey.k.stale, '没重新排上').toBe(true)
  })

  it('没检查过的环境（ADR 0114）：单列、不冒充装齐也不冒充没装齐；选它 = 「检查并使用」，只交 id 与脚本', async () => {
    adoptMock.mockResolvedValue({ ok: true, project: { open: true } } as never)
    await render(<DependencyPrepareDialog />)
    await act(async () =>
      useEnvStore.getState().requestDependencyPreparation(
        offer({
          user_environments: [
            userEnv({
              id: 'venv1',
              source: 'project_venv',
              label: '.venv',
              checked: false,
              ok: null,
              satisfies: null,
              support: '',
              python_version: '',
              matplotlib_version: '',
            }),
          ],
        }),
      ),
    )
    // 没有「装齐」的：默认还是安装目标（一键修复形态不变）；「没有装齐这些包的环境」那句不说——它没被检查过
    expect(radio('tavotto_managed')!.checked).toBe(true)
    expect(text()).not.toContain(en('userEnvNone'))
    expect(text()).toContain(en('userEnvSource_project_venv', { label: '.venv' }))
    expect(text()).toContain(en('userEnvUnchecked'))
    expect(text()).not.toContain(en('userEnvComplete'))
    await act(async () => envRadio('project_venv')!.click())
    expect(button(en('userEnvUse'))).toBeUndefined()
    await act(async () => button(en('userEnvCheckUse'))!.click())
    await act(async () => {})
    expect(adoptMock).toHaveBeenCalledWith('venv1', 'figure.py')
    expect(planMock).not.toHaveBeenCalled()
  })

  it('一个都没装齐：说出口、安装目标预选；没装齐的收在折叠里只说还缺什么（不可选）', async () => {
    await render(<DependencyPrepareDialog />)
    await act(async () =>
      useEnvStore.getState().requestDependencyPreparation(
        offer({ user_environments: [userEnv({ missing: ['tabulate', 'six'], satisfies: false })] }),
      ),
    )
    expect(text()).toContain(en('userEnvNone'))
    expect(text()).toContain(en('oneClickSentence', { packages: listJoin(['six', 'tabulate']) }))
    expect(radio('tavotto_managed')!.checked).toBe(true)
    expect(envRadio('conda')).toBeNull()
    const partial = document.querySelector('[data-user-env-partial]')!
    expect(partial.textContent).toContain(en('userEnvMissing', { packages: 'tabulate, six' }))
    expect(button(en('oneClickRepair'))).toBeDefined()
  })

  it('老后端（载荷里没有 user_environments）：不多说一句「没找到」', async () => {
    await render(<DependencyPrepareDialog />)
    await act(async () => useEnvStore.getState().requestDependencyPreparation(offer()))
    expect(document.querySelector('[data-user-env-none]')).toBeNull()
    expect(document.querySelector('[data-user-env]')).toBeNull()
  })

  it('采用失败：框不关，后端原文留在框里', async () => {
    adoptMock.mockRejectedValue(
      Object.assign(new Error('这个 Python 环境已经找不到了，请重新检查'), {
        body: { code: 'user_environment_gone', error: '这个 Python 环境已经找不到了，请重新检查' },
      }),
    )
    await render(<DependencyPrepareDialog />)
    await act(async () =>
      useEnvStore.getState().requestDependencyPreparation(offer({ user_environments: [userEnv()] })),
    )
    await act(async () => button(en('userEnvUse'))!.click())
    await act(async () => {})
    expect(useEnvStore.getState().dependencyPreparation).not.toBeNull()
    expect(text()).toContain('这个 Python 环境已经找不到了')
  })
})
