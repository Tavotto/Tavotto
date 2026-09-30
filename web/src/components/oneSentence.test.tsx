/**
 * 「一句话就能读懂」（2026-09-29 用户硬性要求）的横向看护：修复卡 / 跑前授权框的**每一种状态 × 每一种语种**，默认可见的
 * 主区域（收起的「详情 / 高级」不算）至多一句话、至多一个主按钮；起点状态恰好一句。
 *
 * 句子按语种自己的句末标点数（`sentenceCount`：中文「。！？」，英文后面跟空白或到结尾的「. ! ?」）——Codex #742
 * 抓到过只数「。」时英文两句蒙混过关。新加一个语种时 `LOCALES` 自动带上它，`sentenceCount` 没有它的规则就抛错。
 *
 * 失败态不在这里：错误原因来自 `repairError.*` 那张大表（按错误码给出下一步，有的就是两句），不属于这张卡的文案。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  createDependencyPlan: vi.fn(() => new Promise(() => {})),
  installDependencyPlan: vi.fn(),
  createJointDependencyPlan: vi.fn(),
  prepareJointDependencies: vi.fn(),
  fetchEngineEnvironment: vi.fn().mockResolvedValue({}),
}))

import {
  DEPENDENCY_PREPARATION_CODE,
  type DependencyPreparationOffer,
  type DependencyProgress,
  type DependencyRepairOffer,
  type JointDependencyPlan,
} from '@/lib/api'
import { DependencyRepairCard } from '@/components/DependencyRepairCard'
import { DependencyPrepareDialog } from '@/components/DependencyPrepareDialog'
import { i18n, resources } from '@/i18n'
import { setCurrentProjectId } from '@/lib/session'
import { useDepRepairStore } from '@/store/depRepairStore'
import { useEnvStore } from '@/store/envStore'
import { visiblePrimaryButtons, visibleSentenceCount } from '@/test/visibleBlocks'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const LOCALES = Object.keys(resources)

const PP = {
  id: 'pbs', version: '3.13.15', target: 'darwin-arm64', source_host: 'github.com',
  download_bytes: 25 * 1048576, required: true, cached: false, network_required: true,
}
const REQ = {
  import_name: 'openpyxl', distribution: 'openpyxl', specifier: '', requirement: 'openpyxl',
  resolution_source: 'curated' as const, confidence: 'high', installable: true,
}
const MANAGED = {
  kind: 'tavotto_managed' as const, venv: '', python: '', modifies_user_environment: false,
  creates_environment: true, available: true as boolean | null, reason: '',
}
const offer = (over: Partial<DependencyRepairOffer> = {}): DependencyRepairOffer => ({
  import_name: 'openpyxl', script: 'fig.py', requirement: REQ, targets: [MANAGED], rounds_remaining: 3,
  python_supported: { min: '3.10', max: '3.14' }, ...over,
})

/** 起点：恰好一句 */
const IDLE_CARDS: [string, DependencyRepairOffer][] = [
  ['受管环境', offer()],
  ['受管环境 + 要下载私有 Python', offer({ targets: [{ ...MANAGED, private_python: { ...PP, origin: 'download' } }] })],
  ['受管环境 + 自带私有 Python', offer({ targets: [{ ...MANAGED, private_python: { ...PP, origin: 'bundled', download_bytes: 0, cached: true } }] })],
  ['已有环境', offer({ targets: [{ ...MANAGED, kind: 'system_interpreter', python: '/usr/bin/python3', python_version: '3.12.4' }] })],
  ['无路可走', offer({ targets: [{ ...MANAGED, available: false, reason: 'managed_env_unavailable' }] })],
]

const dl = (stage: string, origin: 'bundled' | 'cached' | 'download' = 'download') => ({
  download: { stage, done_bytes: 10 * 1048576, total_bytes: 25 * 1048576 },
  private_python: { ...PP, origin },
})
/** 进行中 / 刚结束 / 取消：至多一句 */
const PROGRESS: [string, Partial<DependencyProgress>][] = [
  ['preparing', { state: 'preparing' }],
  ['下载中', { state: 'downloading_python', result: dl('downloading') }],
  ['自带归档的 downloading', { state: 'downloading_python', result: dl('downloading', 'bundled') }],
  ['校验', { state: 'downloading_python', result: dl('verifying') }],
  ['解压', { state: 'downloading_python', result: dl('extracting') }],
  ['试启动', { state: 'downloading_python', result: dl('launching') }],
  ['认不出的子阶段', { state: 'downloading_python', result: dl('something_new') }],
  ['创建环境', { state: 'creating_env' }],
  ['安装', { state: 'installing' }],
  ['验证', { state: 'verifying' }],
  ['装好', { state: 'done' }],
  ['取消（受管环境）', { state: 'cancelled', code: 'dependency_install_cancelled' }],
  ['取消（项目环境）', { state: 'cancelled', code: 'dependency_install_cancelled', target_kind: 'project_venv' }],
]

const joint = (over: Partial<JointDependencyPlan> = {}): JointDependencyPlan => ({
  plan_version: 1, status: 'ready', target_kind: 'tavotto_managed', script: 'fig.py', needed: [],
  missing: [], satisfied: [], unknown: [], possible: [], requirements: ['openpyxl==3.1.5', 'pypdf'],
  constraints: [], require_hashes: false, adapter: [], blocked: [],
  selection: { selected_groups: [], available_groups: [], unselected_groups: [], skipped_marker: [] },
  identity: 'x', ...over,
})
const prep = (over: Partial<DependencyPreparationOffer> = {}): DependencyPreparationOffer => ({
  code: DEPENDENCY_PREPARATION_CODE, script: 'fig.py', plan: joint(), target_kind: 'tavotto_managed',
  targets: [MANAGED], rounds_remaining: 3, skipped: false, ...over,
})
const VENV = {
  kind: 'project_venv' as const, venv: '.venv', python: '.venv/bin/python', modifies_user_environment: true,
  creates_environment: false, available: true, reason: '',
}
const DIALOGS: [string, DependencyPreparationOffer][] = [
  ['一键修复', prep()],
  ['一键修复 + 下载', prep({ targets: [{ ...MANAGED, private_python: { ...PP, origin: 'download' } }] })],
  ['只准备环境', prep({ clean_machine: true, plan: joint({ status: 'nothing_needed', requirements: [] }) })],
  ['只准备环境 + 下载', prep({ clean_machine: true, private_python: { ...PP, origin: 'download' }, plan: joint({ status: 'nothing_needed', requirements: [] }) })],
  ['默认项目 venv', prep({ target_kind: 'project_venv', targets: [VENV, MANAGED] })],
  ['已有装齐的用户环境', prep({ user_environments: [{
    id: 'e1', source: 'conda', label: 'lab', ok: true, code: '', support: 'verified', python_version: '3.12.4',
    matplotlib_version: '3.10.0', missing: [], satisfies: true,
  }] })],
]

let host: HTMLDivElement
let root: Root
async function mount(node: React.ReactNode) {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => root.render(node))
  await act(async () => {})
}

beforeEach(() => {
  setCurrentProjectId('p1')
  useDepRepairStore.getState().reset()
  useDepRepairStore.setState({ managedPreviews: {} })
  useEnvStore.setState({ dependencyPreparation: null })
})
afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
  document.body.innerHTML = ''
  useDepRepairStore.getState().reset()
  await i18n.changeLanguage('zh-CN')
})

describe.each(LOCALES)('一句话（%s）', (lang) => {
  beforeEach(async () => {
    await i18n.changeLanguage(lang)
  })

  it.each(IDLE_CARDS)('修复卡起点：%s —— 恰好一句、至多一个主按钮', async (_name, o) => {
    await mount(<DependencyRepairCard offer={o} module="openpyxl" script="fig.py" />)
    const card = host.querySelector('.shadow-card')!
    expect(visibleSentenceCount(card, lang)).toBe(1)
    expect(visiblePrimaryButtons(card)).toBeLessThanOrEqual(1)
  })

  it.each(PROGRESS)('修复卡进度：%s —— 至多一句、没有主按钮', async (_name, p) => {
    useDepRepairStore.setState({
      progress: { plan_id: 'p', log: '', error: null, code: '', distribution: 'openpyxl', target_kind: 'tavotto_managed', ...p } as DependencyProgress,
    })
    await mount(<DependencyRepairCard offer={offer()} module="openpyxl" script="fig.py" />)
    const card = host.querySelector('.shadow-card')!
    expect(visibleSentenceCount(card, lang)).toBeLessThanOrEqual(1)
    expect(visiblePrimaryButtons(card)).toBe(0)
  })

  it.each(DIALOGS)('跑前授权框：%s —— 至多一句、恰好一个主按钮', async (_name, o) => {
    await mount(<DependencyPrepareDialog />)
    await act(async () => useEnvStore.getState().requestDependencyPreparation(o))
    const dialog = document.querySelector('[data-dialog="dependency-prepare"]')!
    expect(visibleSentenceCount(dialog, lang)).toBeLessThanOrEqual(1)
    expect(visiblePrimaryButtons(dialog)).toBe(1)
  })

  it('跑前授权框进行中：至多一句', async () => {
    await mount(<DependencyPrepareDialog />)
    await act(async () => useEnvStore.getState().requestDependencyPreparation(prep()))
    await act(async () => {
      useDepRepairStore.setState({
        progress: { plan_id: 'j', state: 'downloading_python', log: '', error: null, code: '', flow: 'joint', result: dl('verifying') } as DependencyProgress,
      })
    })
    const dialog = document.querySelector('[data-dialog="dependency-prepare"]')!
    expect(visibleSentenceCount(dialog, lang)).toBeLessThanOrEqual(1)
  })
})
