/**
 * 「这个项目还缺少 X」这张卡片（ADR 0019）。
 *
 * 盯四件事：
 *
 * ① **不写成 Python 教程**：主界面上不出现 pip / site-packages / virtualenv。
 * ② **改用户环境要说清楚**：装进项目 `.venv` 之前必须先出现「这会修改这个
 *    项目现有的 Python 环境」，按钮写「安装到项目环境」而不是「确定」。
 * ③ **解析不出包名就不给一键安装**：那时只给「指定安装包…」。
 * ④ **进度按状态说人话**：pip 日志折叠在「安装详情」里，不糊在主文案上。
 *
 * 还有一条与后端同源的纪律：**安装请求只带 plan_id**——前端不自己拼包名。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  createDependencyPlan: vi.fn(),
  installDependencyPlan: vi.fn(),
  cancelDependencyPlan: vi.fn(),
  fetchEngineEnvironment: vi.fn(),
  setProjectEnvironment: vi.fn(),
  adoptEnvironmentCandidate: vi.fn(),
  setEngineEnvironment: vi.fn(),
}))

import {
  ApiError,
  adoptEnvironmentCandidate,
  cancelDependencyPlan,
  createDependencyPlan,
  fetchEngineEnvironment,
  installDependencyPlan,
  setEngineEnvironment,
  setProjectEnvironment,
  type DependencyRepairOffer,
  type DependencyRepairPlan,
} from '@/lib/api'
import { DependencyRepairCard } from '@/components/DependencyRepairCard'
import { PRODUCT_NAME } from '@/lib/brand'
import { i18n, t } from '@/i18n'
import { __resetDepRepairParkingForTests, useDepRepairStore } from '@/store/depRepairStore'
import { useRenderStore } from '@/store/renderStore'
import {
  mentionCount,
  repeatedSentences,
  visibleBlocks,
  visiblePrimaryButtons,
  visibleSentenceCount,
} from '@/test/visibleBlocks'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const planMock = vi.mocked(createDependencyPlan)
const installMock = vi.mocked(installDependencyPlan)
const cancelMock = vi.mocked(cancelDependencyPlan)
const envMock = vi.mocked(fetchEngineEnvironment)
const adoptMock = vi.mocked(setProjectEnvironment)
const adoptCandidateMock = vi.mocked(adoptEnvironmentCandidate)
const clearGlobalMock = vi.mocked(setEngineEnvironment)

const en = (key: string, v?: Record<string, unknown>) =>
  t(`engine.${key}`, { ns: 'errors', ...(v ?? {}) })

const OFFER: DependencyRepairOffer = {
  import_name: 'lmfit',
  script: 'figure.py',
  requirement: {
    import_name: 'lmfit',
    distribution: 'lmfit',
    specifier: '>=1.3',
    requirement: 'lmfit>=1.3',
    resolution_source: 'project_declared',
    confidence: 'high',
    installable: true,
  },
  targets: [
    {
      kind: 'project_venv',
      venv: '.venv',
      python: '.venv/bin/python',
      modifies_user_environment: true,
      creates_environment: false,
      available: true,
      reason: '',
    },
    {
      kind: 'tavotto_managed',
      venv: '',
      python: '',
      modifies_user_environment: false,
      creates_environment: true,
      available: true,
      reason: '',
    },
  ],
  rounds_remaining: 3,
  python_supported: { min: '3.10', max: '3.14' },
}

const PRIVATE_PYTHON = {
  id: 'pinned', version: '3.13.15', target: 'windows-x86_64', source_host: 'github.com',
  download_bytes: 47131996, required: true, cached: false, network_required: true,
}

const PLAN: DependencyRepairPlan = {
  plan_id: 'plan-abc',
  impact_digest: 'dg-abc',
  target_kind: 'project_venv',
  python: '.venv/bin/python',
  creates_environment: false,
  modifies_user_environment: true,
  network_required: true,
  expires_at: 0,
  import_name: 'lmfit',
  distribution: 'lmfit',
  specifier: '>=1.3',
  requirement: 'lmfit>=1.3',
  resolution_source: 'project_declared',
  confidence: 'high',
  installable: true,
}

/** 与 OFFER 的受管目标逐项相符的计划（一次授权直接执行的那种） */
const MANAGED_PLAN: DependencyRepairPlan = {
  ...PLAN,
  plan_id: 'plan-managed',
  impact_digest: 'dg-managed',
  target_kind: 'tavotto_managed',
  python: '',
  creates_environment: true,
  modifies_user_environment: false,
  private_python: PRIVATE_PYTHON,
}

let host: HTMLDivElement
let root: Root

async function render(offer: DependencyRepairOffer = OFFER) {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => {
    root.render(<DependencyRepairCard offer={offer} module="lmfit" script="figure.py" />)
  })
  await act(async () => {})
}

const text = () => document.body.textContent ?? ''

const buttons = () => [...document.querySelectorAll('button')] as HTMLButtonElement[]
const byName = (name: string) =>
  buttons().find((b) => (b.getAttribute('aria-label') ?? b.textContent ?? '').includes(name))
const click = async (name: string) => {
  const button = byName(name)
  expect(button, `找不到按钮：${name}`).toBeTruthy()
  await act(async () => {
    button!.click()
  })
  await act(async () => {})
}

beforeEach(() => {
  // 模块级的停放槽活得比 zustand reset 长：每条用例从空的开始（互不串）
  __resetDepRepairParkingForTests()
  planMock.mockReset()
  installMock.mockReset()
  cancelMock.mockReset()
  envMock.mockReset()
  envMock.mockResolvedValue({} as never)
  adoptMock.mockReset()
  adoptCandidateMock.mockReset()
  clearGlobalMock.mockReset()
  useDepRepairStore.getState().reset()
  // 预读按卡分格、`reset()` 不动它们（关一张卡不该让另一张回到「正在检查」）：用例之间自己清
  useDepRepairStore.setState({ managedPreviews: {} })
})

afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
  document.body.innerHTML = ''
  useDepRepairStore.getState().reset()
  await i18n.changeLanguage('zh-CN')
})

describe('缺依赖的修复卡片', () => {
  it('主界面不出现 pip / site-packages / virtualenv 这些词', async () => {
    await render()
    expect(text()).toContain(en('oneClickSentence', { packages: 'lmfit' }))
    for (const jargon of ['pip', 'site-packages', 'virtualenv', 'venv activate']) {
      expect(text().toLowerCase()).not.toContain(jargon)
    }
  })

  it('两个目标都在：一键修复（受管环境）是唯一的主按钮，装进项目环境收在「高级」里', async () => {
    await render()
    const primary = byName(en('oneClickRepair'))!
    expect(primary).toBeTruthy()
    expect(primary.hasAttribute('data-one-click-repair-button')).toBe(true)
    const advanced = document.querySelector('[data-repair-advanced]') as HTMLDetailsElement
    expect(advanced.open, '「高级」默认折叠').toBe(false)
    expect(advanced.contains(byName(en('repairUseProjectEnv'))!)).toBe(true)
    // 主按钮只有一颗（UI 纪律：一个上下文最多一个填色主动作）
    expect(buttons().filter((b) => b.getAttribute('data-variant') === 'primary')).toHaveLength(1)
  })

  it('装进项目环境之前先说清楚「这会修改你的环境」，按钮不是「确定」', async () => {
    planMock.mockResolvedValue({ plan: PLAN })
    await render()
    await click(en('repairUseProjectEnv'))
    expect(planMock).toHaveBeenCalledWith({
      module: 'lmfit',
      script: 'figure.py',
      target: 'project_venv',
    })
    expect(text()).toContain(en('repairModifiesEnv'))
    expect(text()).toContain(en('repairWillInstall', { requirement: 'lmfit>=1.3' }))
    expect(byName(en('repairInstallToProject'))).toBeTruthy()
    expect(byName('确定')).toBeUndefined()
  })

  it('Tavotto 隔离环境的文案说明不会动用户已有的环境 —— 点之前就在卡片上', async () => {
    await render({ ...OFFER, targets: [OFFER.targets[1]] })
    expect(text()).toContain(en('repairFactUntouched'))
    expect(text()).not.toContain(en('repairModifiesEnv'))
  })

  it('确认之后只发 plan_id —— 前端不自己拼包名', async () => {
    planMock.mockResolvedValue({ plan: PLAN })
    installMock.mockResolvedValue({ started: true } as never)
    await render()
    await click(en('repairUseProjectEnv'))
    await click(en('repairInstallToProject'))
    // 计划 id + 界面上这份计划的影响摘要（Codex r4217992305）；没有第三样东西
    expect(installMock).toHaveBeenCalledWith('plan-abc', 'dg-abc')
    expect(installMock.mock.calls[0]).toHaveLength(2)
  })

  it('计划没有影响摘要 —— 不发空串去撞后端的 400，明确停下、不执行', async () => {
    planMock.mockResolvedValue({ plan: { ...PLAN, impact_digest: undefined } })
    installMock.mockResolvedValue({ started: true } as never)
    await render()
    await click(en('repairUseProjectEnv'))
    await click(en('repairInstallToProject'))
    expect(installMock).not.toHaveBeenCalled()
    expect(useDepRepairStore.getState().errorCode).toBe('dependency_impact_required')
    expect(useDepRepairStore.getState().busy).toBe(false)
  })

  it('解析不出包名时不给一键安装，只给「指定安装包」', async () => {
    await render({ ...OFFER, requirement: null, targets: [], code: 'dependency_unresolved' })
    expect(text()).toContain(en('repairUnresolved', { module: 'lmfit' }))
    expect(byName(en('oneClickRepair'))).toBeUndefined()
    expect(byName(en('repairUseProjectEnv'))).toBeUndefined()
    expect(byName(en('repairInstallToManaged', { module: 'lmfit', product: PRODUCT_NAME }))).toBeUndefined()
    expect(text()).toContain(en('repairSpecifyPackage'))
  })

  it('即使后端给了目标，没有可信包名也不给一键安装', async () => {
    // `requirement` 与 `targets` 是两件事：解析不出包名时后端本来就不该给
    // 目标，但**前端不靠这条约定**——一键安装的前提是「知道要装什么」，
    // 而不是「有地方可以装」。这一条守的正是那个前提。
    await render({ ...OFFER, requirement: null, code: 'dependency_unresolved' })
    expect(byName(en('oneClickRepair'))).toBeUndefined()
    expect(byName(en('repairUseProjectEnv'))).toBeUndefined()
    expect(byName(en('repairInstallToManaged', { module: 'lmfit', product: PRODUCT_NAME }))).toBeUndefined()
    expect(text()).toContain(en('repairSpecifyPackage'))
  })

  it('用户手填的包名照样经后端解析（前端不做安装决定）', async () => {
    planMock.mockResolvedValue({ plan: PLAN })
    await render({
      ...OFFER,
      requirement: null,
      targets: [OFFER.targets[1]],
      code: 'dependency_unresolved',
    })
    const input = document.querySelector('input') as HTMLInputElement
    // 受控 input 要走原生 setter：直接赋 value React 认不到（仓库里其它
    // 输入类用例同一写法）
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
    await act(async () => {
      setter.call(input, 'my-lab-tools')
      input.dispatchEvent(new Event('input', { bubbles: true }))
    })
    await click(en('repairContinue'))
    expect(planMock).toHaveBeenCalledWith({
      module: 'lmfit',
      script: 'figure.py',
      target: 'tavotto_managed',
      distribution: 'my-lab-tools',
    })
  })

  it('修复轮次用完之后不再给安装入口', async () => {
    await render({
      ...OFFER,
      rounds_remaining: 0,
      targets: [],
      code: 'dependency_repair_rounds_exhausted',
    })
    expect(text()).toContain(en('repairExhausted'))
    expect(byName(en('repairUseProjectEnv'))).toBeUndefined()
  })

  it('没有基础 Python 时不列出「创建 Tavotto 环境」', async () => {
    await render({
      ...OFFER,
      targets: [
        OFFER.targets[0],
        { ...OFFER.targets[1], available: false, reason: 'managed_env_unavailable' },
      ],
    })
    expect(byName(en('oneClickRepair'))).toBeUndefined()
    expect(byName(en('repairInstallToManaged', { module: 'lmfit', product: PRODUCT_NAME }))).toBeUndefined()
    expect(byName(en('repairUseProjectEnv'))).toBeTruthy()
    // 还有项目环境这条路：不说「这台电脑无路可走」
    expect(document.querySelector('[data-managed-env-unavailable]')).toBeNull()
  })

  it('只有旧 Python 且私有 Python 未开放时，说清缺的是建环境的基础解释器', async () => {
    await render({
      ...OFFER,
      // 范围取自 offer（支持矩阵的运行时镜像），故意与当前口径不同：文案不许手写版本号
      python_supported: { min: '3.11', max: '3.15' },
      targets: [{ ...OFFER.targets[1], available: false, reason: 'managed_env_unavailable' }],
      system_rejected: [{
        python: 'C:\\Python37\\python.exe', code: 'project_env_unsupported_python', python_version: '3.7.6',
      }],
    })
    const sentence = document.querySelector('[data-managed-env-unavailable]')!
    expect(sentence.textContent).toBe(en('repairManagedUnavailable'))
    // 无路可走也只有一句、没有主按钮；要装哪段版本（取自 offer）在「详情」里
    const card = sentence.closest('.shadow-card')!
    expect(visibleSentenceCount(card)).toBe(1)
    expect(visiblePrimaryButtons(card)).toBe(0)
    const hint = document.querySelector('[data-managed-env-unavailable-hint]')!
    expect(hint.closest('[data-repair-advanced]')).toBeTruthy()
    expect(hint.textContent).toContain('3.11–3.15')
    expect(text()).toContain(en('repairSystemRejectedUnsupported', {
      python: 'C:\\Python37\\python.exe', module: 'lmfit', version: '3.7.6', product: PRODUCT_NAME,
    }))
    expect(byName(en('repairInstallToManaged', { module: 'lmfit', product: PRODUCT_NAME }))).toBeUndefined()
    expect(text()).toContain(en('repairUseOtherPythonShort'))
  })

  it('私有 Python 可用时，点之前就明示下载大小；计划超出卡片说过的就停在确认页再说一遍', async () => {
    await render({
      ...OFFER,
      targets: [{ ...OFFER.targets[1], private_python: PRIVATE_PYTHON }],
    })
    const disclosure = en('repairFactDownload', { version: '3.13.15', mb: 45, product: PRODUCT_NAME })
    expect(text()).toContain(disclosure)
    // 后端算出来的计划要下载的比卡片说的多：不执行，确认页把计划本身的数字说出口
    planMock.mockResolvedValue({
      plan: { ...MANAGED_PLAN, private_python: { ...PRIVATE_PYTHON, download_bytes: 90_000_000 } },
    })
    await click(en('oneClickRepair'))
    expect(installMock).not.toHaveBeenCalled()
    expect(text()).toContain(
      en('dependencyPreparePrivatePython', { version: '3.13.15', mb: 86, product: PRODUCT_NAME }),
    )
    expect(byName(en('repairPrepareAndContinue'))).toBeTruthy()
  })

  it('下载 Python 的进度仍在运行中，允许取消且不显示关闭', async () => {
    useDepRepairStore.setState({
      progress: { plan_id: 'plan-abc', state: 'downloading_python', log: '', error: null, code: '' },
    })
    await render()
    expect(text()).toContain(en('dependencyPrepareState_downloading_python'))
    expect(byName(en('repairCancel'))).toBeTruthy()
    expect(byName(en('repairClose'))).toBeUndefined()
  })
})

describe('一键修复（2026-09-29：面向不懂 Python 的用户）', () => {
  const managedOnly = (private_python: DependencyRepairOffer['targets'][number]['private_python']) => ({
    ...OFFER,
    targets: [{ ...OFFER.targets[1], private_python }],
  })
  const cost = () => document.querySelector('[data-one-click-cost]')?.textContent ?? ''
  const mainText = () =>
    visibleBlocks(document.querySelector('[data-one-click-repair]')!)
      .map((b) => b.text)
      .join('\n')

  it('默认可见的只有一句话 + 一个主按钮 +「详情」折叠标题（按可见元素数）', async () => {
    // 2026-09-29 用户：「太冗杂，坚决不能出现，一定要让用户一句话就能读懂」
    for (const [offer, sentence] of [
      [managedOnly(PRIVATE_PYTHON), en('oneClickSentenceDownload', { packages: 'lmfit', mb: 45 })],
      [OFFER, en('oneClickSentence', { packages: 'lmfit' })],
    ] as const) {
      await act(async () => root?.unmount())
      host?.remove()
      await render(offer)
      const card = document.querySelector('[data-one-click-repair]')!
      expect(card.getAttribute('data-one-click-repair')).toBe('tavotto_managed')
      expect(visibleBlocks(card)).toEqual([
        { tag: 'p', text: sentence },
        { tag: 'button', text: en('oneClickRepair') },
        { tag: 'summary', text: en('repairAdvanced') },
      ])
    }
    // 系统解释器那一档同样只有一句
    await act(async () => root.unmount())
    host.remove()
    await render({ ...OFFER, targets: [{ ...OFFER.targets[1], kind: 'system_interpreter', python: '/usr/bin/python3' }] })
    expect(visibleBlocks(document.querySelector('[data-one-click-repair]')!).map((b) => b.tag)).toEqual([
      'p',
      'button',
      'summary',
    ])
  })

  it('主区域（「详情」之外）只有一句话、一个主按钮：起点 / 系统解释器 / 进行中 / 无路可走各量一遍', async () => {
    const shapes: [string, () => Promise<void>][] = [
      ['受管环境', () => render(managedOnly(PRIVATE_PYTHON))],
      ['受管环境 + 项目环境', () => render(OFFER)],
      ['系统解释器', () => render({ ...OFFER, targets: [{ ...OFFER.targets[1], kind: 'system_interpreter', python: '/usr/bin/python3' }] })],
    ]
    for (const [name, mount] of shapes) {
      await act(async () => root?.unmount())
      host?.remove()
      await mount()
      const card = document.querySelector('.shadow-card')!
      expect(visibleSentenceCount(card), name).toBe(1)
      expect(visiblePrimaryButtons(card), name).toBe(1)
    }
    // 进行中：一行（不以「。」结尾也行），没有主按钮
    await act(async () => root.unmount())
    host.remove()
    await render()
    await act(() => {
      useDepRepairStore.setState({ progress: { plan_id: 'plan-abc', state: 'installing', log: 'x', error: null, code: '' } })
    })
    const card = document.querySelector('.shadow-card')!
    expect(visibleSentenceCount(card)).toBeLessThanOrEqual(1)
    expect(visibleBlocks(card).filter((b) => b.tag === 'p')).toHaveLength(1)
  })

  it('一句话里不出现版本号、路径与「隔离环境」；说明与明细都在「详情」里', async () => {
    await render(managedOnly(PRIVATE_PYTHON))
    for (const jargon of ['3.13.15', '隔离', '/', 'Python 3']) expect(mainText()).not.toContain(jargon)
    const details = document.querySelector('[data-repair-advanced]')!
    expect(details.textContent).toContain(en('repairFactDownload', { version: '3.13.15', mb: 45, product: PRODUCT_NAME }))
    expect(details.textContent).toContain(en('repairFactUntouched'))
  })

  it('要下载时说大小；安装包自带 / 已缓存时不提下载；老后端没有 origin 时按 cached 推（缺省 = 下载）', async () => {
    await render(managedOnly(PRIVATE_PYTHON))
    expect(cost()).toBe(en('repairFactDownload', { version: '3.13.15', mb: 45, product: PRODUCT_NAME }))
    for (const [pp, said] of [
      [{ ...PRIVATE_PYTHON, origin: 'download' as const }, en('repairFactDownload', { version: '3.13.15', mb: 45, product: PRODUCT_NAME })],
      [{ ...PRIVATE_PYTHON, origin: 'bundled' as const, download_bytes: 0, cached: false }, en('repairFactBundled', { version: '3.13.15', product: PRODUCT_NAME })],
      [{ ...PRIVATE_PYTHON, origin: 'cached' as const, download_bytes: 0, cached: true }, en('repairFactCached', { version: '3.13.15', product: PRODUCT_NAME })],
      [{ ...PRIVATE_PYTHON, cached: true, download_bytes: 0 }, en('repairFactCached', { version: '3.13.15', product: PRODUCT_NAME })],
      [null, en('repairFactNetwork')],
    ] as const) {
      await act(async () => root.unmount())
      host.remove()
      await render(managedOnly(pp))
      expect(cost(), JSON.stringify(pp)).toBe(said)
      if (!said.includes('MB')) expect(mainText()).not.toContain('MB')
    }
  })

  it('要下载私有 Python：下载大小用括号放进那一句里（点之前说出多大，仍是一句、一个句号）', async () => {
    // 缺 origin 的老后端按 cached 推，cached=false 即下载
    for (const pp of [PRIVATE_PYTHON, { ...PRIVATE_PYTHON, origin: 'download' as const }]) {
      await act(async () => root?.unmount())
      host?.remove()
      await render(managedOnly(pp))
      const sentence = document.querySelector('[data-one-click-sentence]')!.textContent ?? ''
      expect(sentence).toContain('45 MB')
      expect(visibleSentenceCount(document.querySelector('[data-one-click-repair]')!)).toBe(1)
    }
  })

  it('计划要装的不止缺的这一个（新一代要补齐）：那一句话与「详情」首段列出全部，单个时不变', async () => {
    // 要装的全部包只有形成计划时才算得出（offer 在失败响应路径上不起解释器）：卡片预读计划，按它写那一句话
    const wide = (requirements: string[] | null) => {
      planMock.mockResolvedValue({
        plan: { ...MANAGED_PLAN, private_python: null, ...(requirements ? { requirements } : {}) },
      })
      return { ...OFFER, targets: [{ ...OFFER.targets[1] }] }
    }
    for (const [requirements, sentence, details] of [
      [['pandas', 'lmfit>=1.3'], '这个脚本还缺 pandas 和 lmfit，点一下自动装好。', '将安装：pandas 和 lmfit>=1.3'],
      [
        ['numpy', 'pandas', 'lmfit>=1.3'],
        '这个脚本还缺 numpy 等 3 个包，点一下自动装好。',
        '将安装：numpy、pandas 和 lmfit>=1.3',
      ],
      [null, '这个脚本还缺 lmfit，点一下自动装好。', '将安装：lmfit>=1.3'],
    ] as const) {
      await act(async () => root?.unmount())
      host?.remove()
      useDepRepairStore.setState({ managedPreviews: {} }) // 预读按脚本 + 模块只问一次
      await render(wide(requirements ? [...requirements] : null))
      expect(document.querySelector('[data-one-click-sentence]')!.textContent).toBe(sentence)
      expect(visibleSentenceCount(document.querySelector('[data-one-click-repair]')!)).toBe(1)
      // 「详情」第一段：将安装：全部
      expect(document.querySelector('[data-repair-primary-facts] p')!.textContent).toBe(details)
    }
  })

  it('安装包自带 / 已缓存 / 已就位的 Python：那一句里不提下载', async () => {
    for (const pp of [
      { ...PRIVATE_PYTHON, origin: 'bundled' as const, download_bytes: 0, cached: true, network_required: false },
      { ...PRIVATE_PYTHON, origin: 'cached' as const, download_bytes: 0, cached: true, network_required: false },
      // present_payload：已就位、没有 origin
      { ...PRIVATE_PYTHON, required: false, download_bytes: 0, cached: true, network_required: false },
    ]) {
      await act(async () => root?.unmount())
      host?.remove()
      await render(managedOnly(pp))
      const sentence = document.querySelector('[data-one-click-sentence]')!.textContent ?? ''
      expect(sentence, JSON.stringify(pp)).not.toContain('MB')
      expect(sentence).toBe(en('oneClickSentence', { packages: 'lmfit' }))
    }
  })

  it('展开「详情」后没有重复：最多三条事实（装什么 / 下载什么多大 / 不改动什么），同一件事只说一次', async () => {
    for (const offer of [managedOnly(PRIVATE_PYTHON), managedOnly({ ...PRIVATE_PYTHON, origin: 'bundled', download_bytes: 0 }), OFFER]) {
      await act(async () => root?.unmount())
      host?.remove()
      await render(offer)
      const details = document.querySelector('[data-one-click-repair] [data-repair-advanced]')!
      expect(repeatedSentences(details)).toEqual([])
      expect(document.querySelectorAll('[data-dependency-disclosure] > p').length).toBeLessThanOrEqual(3)
      expect(mentionCount(details, '不改动'), '「不改动…」说了不止一次').toBe(1)
      expect(mentionCount(details, 'MB')).toBeLessThanOrEqual(1)
      expect(mentionCount(details, '联网'), '联网说了不止一次').toBe(1)
      expect(mentionCount(details, '隔离')).toBe(0)
    }
  })

  it('安装包自带的 Python：「高级」里的明细说自带，不说「已下载」', async () => {
    await render(managedOnly({ ...PRIVATE_PYTHON, origin: 'bundled', download_bytes: 0 }))
    const line = document.querySelector('[data-dependency-private-python]')!.textContent
    expect(line).toBe(en('repairFactBundled', { version: '3.13.15', product: PRODUCT_NAME }))
  })

  it('「换一个 Python」与「选择渲染环境」收在默认折叠的「高级」里；后者就地打开渲染环境对话框', async () => {
    const { useUiStore } = await import('@/store/uiStore')
    useUiStore.setState({ engineEnvOpen: false })
    await render(managedOnly(PRIVATE_PYTHON))
    const advanced = document.querySelector('[data-repair-advanced]') as HTMLDetailsElement
    expect(advanced.open).toBe(false)
    expect(advanced.querySelector(`input[aria-label="${en('pathAria')}"]`)).toBeTruthy()
    const open = advanced.querySelector('[data-repair-open-environment]') as HTMLButtonElement
    await act(async () => open.click())
    expect(useUiStore.getState().engineEnvOpen).toBe(true)
  })

  it('受管目标「能不能用」还不知道（available=null）：先预读计划，按计划的真实下载说出口，点一次就开始（不多一步确认）', async () => {
    let resolvePreview!: (v: { plan: DependencyRepairPlan }) => void
    planMock.mockImplementationOnce(() => new Promise((r) => (resolvePreview = r)))
    installMock.mockResolvedValue({ started: true } as never)
    await render({ ...OFFER, targets: [{ ...OFFER.targets[1], available: null }] })
    // 预读的是受管目标的计划（计划这一步什么都不装）
    expect(planMock).toHaveBeenCalledWith({ module: 'lmfit', script: 'figure.py', target: 'tavotto_managed' })
    expect(installMock).not.toHaveBeenCalled()
    // 预读回来之前：主按钮等它，说正在检查
    expect(cost()).toBe(en('oneClickChecking'))
    expect(byName(en('oneClickRepair'))!.disabled).toBe(true)
    await act(async () => resolvePreview({ plan: { ...MANAGED_PLAN, plan_id: 'plan-preview' } }))
    expect(cost()).toBe(en('repairFactDownload', { version: '3.13.15', mb: 45, product: PRODUCT_NAME }))
    expect(byName(en('oneClickRepair'))!.disabled).toBe(false)
    planMock.mockResolvedValue({ plan: MANAGED_PLAN })
    await click(en('oneClickRepair'))
    expect(installMock).toHaveBeenCalledTimes(1)
    expect(installMock).toHaveBeenCalledWith('plan-managed', 'dg-managed')
    expect(byName(en('repairPrepareAndContinue'))).toBeUndefined()
  })

  it('两张卡同时预读（右栏一张、脚本行一张，缺的包不同）：各拿各的结果，谁都不停在「正在检查」（Codex #742）', async () => {
    const resolvers: Record<string, (v: { plan: DependencyRepairPlan }) => void> = {}
    planMock.mockImplementation(
      (args: { module: string }) => new Promise((r) => (resolvers[args.module] = r)) as never,
    )
    const pending = { ...OFFER.targets[1], available: null }
    host = document.createElement('div')
    document.body.appendChild(host)
    root = createRoot(host)
    await act(async () => {
      root.render(
        <>
          <div data-card-a>
            <DependencyRepairCard offer={{ ...OFFER, targets: [pending] }} module="lmfit" script="figure.py" />
          </div>
          <div data-card-b>
            <DependencyRepairCard
              offer={{ ...OFFER, import_name: 'openpyxl', script: 'other.py', targets: [pending],
                requirement: { ...OFFER.requirement!, import_name: 'openpyxl', distribution: 'openpyxl', requirement: 'openpyxl' } }}
              module="openpyxl"
              script="other.py"
            />
          </div>
        </>,
      )
    })
    expect(Object.keys(resolvers).sort()).toEqual(['lmfit', 'openpyxl'])
    // A 先发、B 后发；A 的结果回来时 B 还在途——A 不许因为「格子被 B 占了」而认不出自己的结果
    await act(async () => resolvers.lmfit({ plan: { ...MANAGED_PLAN, plan_id: 'pa' } }))
    await act(async () =>
      resolvers.openpyxl({ plan: { ...MANAGED_PLAN, plan_id: 'pb', import_name: 'openpyxl', distribution: 'openpyxl', requirement: 'openpyxl', private_python: null } }),
    )
    const button = (sel: string) =>
      document.querySelector(`${sel} [data-one-click-repair-button]`) as HTMLButtonElement
    const sentence = (sel: string) => document.querySelector(`${sel} [data-one-click-sentence]`)!.textContent
    expect(button('[data-card-a]').disabled, 'A 卡停在「正在检查」').toBe(false)
    expect(button('[data-card-b]').disabled, 'B 卡停在「正在检查」').toBe(false)
    // 各说各的：A 要下载私有 Python，B 不用
    expect(sentence('[data-card-a]')).toBe(en('oneClickSentenceDownload', { packages: 'lmfit', mb: 45 }))
    expect(sentence('[data-card-b]')).toBe(en('oneClickSentence', { packages: 'openpyxl' }))
  })

  it('预读说这台电脑建不了环境（managed_env_unavailable）：没有一键修复，说清下一步，出口在「高级」里', async () => {
    planMock.mockRejectedValue(new ApiError('没有 Python', 400, { code: 'managed_env_unavailable' }))
    await render({ ...OFFER, targets: [{ ...OFFER.targets[1], available: null }] })
    await act(async () => {})
    expect(byName(en('oneClickRepair'))).toBeUndefined()
    expect(document.querySelector('[data-managed-env-unavailable]')?.textContent).toBe(
      en('repairManagedUnavailable'),
    )
    expect(document.querySelector('[data-repair-advanced] [data-repair-open-environment]')).toBeTruthy()
  })

  it('新建第一代时预读（要装的全部包只有计划里才有）；不新建环境（已有一代）时不预读', async () => {
    planMock.mockResolvedValue({ plan: MANAGED_PLAN })
    await render(managedOnly(PRIVATE_PYTHON))
    expect(planMock).toHaveBeenCalledTimes(1)
    await act(async () => root?.unmount())
    host?.remove()
    planMock.mockClear()
    await render({
      ...OFFER,
      targets: [{ ...OFFER.targets[1], creates_environment: false, private_python: PRIVATE_PYTHON }],
    })
    expect(planMock).not.toHaveBeenCalled()
  })
})

describe('预读、披露、「检查中」只跟着主按钮指向的那个目标走（Codex #742）', () => {
  const SYSTEM_READY = {
    kind: 'system_interpreter' as const, venv: '', python: '/usr/local/bin/python3', modifies_user_environment: false,
    creates_environment: false, available: true, reason: '', python_version: '3.12.4', support: 'verified',
  }
  const MANAGED_UNKNOWN = { ...OFFER.targets[1], available: null }
  const oneClick = () => document.querySelector('[data-one-click-repair-button]') as HTMLButtonElement | null

  it('主按钮是已有解释器、受管目标还在探（available=null）：不预读受管计划，主按钮不被「检查中」禁用，点了就改用', async () => {
    planMock.mockImplementation(() => new Promise(() => {})) // 真去预读的话它永远不回来
    adoptMock.mockResolvedValue({ ok: true, project: { open: true } } as never)
    await render({ ...OFFER, targets: [SYSTEM_READY, MANAGED_UNKNOWN] })
    expect(document.querySelector('[data-one-click-repair]')!.getAttribute('data-one-click-repair')).toBe('system_interpreter')
    expect(planMock, '为用不上的受管目标发了预读').not.toHaveBeenCalled()
    expect(oneClick()!.disabled).toBe(false)
    expect(oneClick()!.hasAttribute('aria-busy')).toBe(false)
    // 那一句说的是改用已有环境，不带受管那边的下载大小
    expect(document.querySelector('[data-one-click-sentence]')!.textContent).toBe(
      en('oneClickSentenceSystem', { module: 'lmfit' }),
    )
    await click(en('oneClickRepair'))
    expect(adoptMock).toHaveBeenCalledWith('/usr/local/bin/python3', 'lmfit')
    expect(planMock).not.toHaveBeenCalled()
  })

  it('主按钮是受管环境、还在探：预读受管计划，回来之前主按钮等它（对照：尺子是活的）', async () => {
    planMock.mockImplementation(() => new Promise(() => {}))
    await render({ ...OFFER, targets: [MANAGED_UNKNOWN] })
    expect(document.querySelector('[data-one-click-repair]')!.getAttribute('data-one-click-repair')).toBe('tavotto_managed')
    expect(planMock).toHaveBeenCalledWith({ module: 'lmfit', script: 'figure.py', target: 'tavotto_managed' })
    expect(oneClick()!.disabled).toBe(true)
  })

  it('主目标是项目环境（受管目标用不了）：不预读，项目环境那颗按钮可点', async () => {
    await render({ ...OFFER, targets: [OFFER.targets[0], { ...OFFER.targets[1], available: false, reason: 'managed_env_unavailable' }] })
    expect(planMock).not.toHaveBeenCalled()
    expect(oneClick()).toBeNull()
    expect(byName(en('repairUseProjectEnv'))!.disabled).toBe(false)
  })

  it('主动作是「恢复自动检测」（全局固定着）：不预读，恢复按钮可点', async () => {
    await render({ ...OFFER, targets: [MANAGED_UNKNOWN], pinned: { python: '/opt/venv/bin/python', source: 'configured' } })
    expect(planMock).not.toHaveBeenCalled()
    expect(byName(en('repairPinnedClear'))!.disabled).toBe(false)
  })
})

describe('「详情」首段只说主按钮真正要做的事，别的路放在「其他方式（备选）」（Codex #742）', () => {
  const SYSTEM_READY = {
    kind: 'system_interpreter' as const, venv: '', python: '/usr/local/bin/python3', modifies_user_environment: false,
    creates_environment: false, available: true, reason: '', python_version: '3.12.4', support: 'verified',
  }
  const MANAGED_WITH_PY = { ...OFFER.targets[1], private_python: PRIVATE_PYTHON }
  const facts = () => document.querySelector('[data-repair-advanced] [data-repair-primary-facts]')
  const alternatives = () => document.querySelector('[data-repair-advanced] [data-repair-alternatives]')!
  const willInstall = () => en('repairWillInstall', { requirement: 'lmfit>=1.3' })
  const download = () => en('repairFactDownload', { version: '3.13.15', mb: 45, product: PRODUCT_NAME })

  it('主按钮是改用已有 Python：首段说改用哪一个、不装不下；「将安装」「需下载」只出现在备选里', async () => {
    await render({ ...OFFER, targets: [SYSTEM_READY, MANAGED_WITH_PY, OFFER.targets[0]] })
    const lead = facts()!.textContent!
    expect(lead).toContain('/usr/local/bin/python3')
    expect(lead).toContain(en('repairFactUntouched'))
    for (const wrong of [willInstall(), download(), 'MB', en('repairFactNetwork')]) expect(lead).not.toContain(wrong)
    // 受管环境的要素仍在，但在备选这一节、带着「其他方式（备选）」的标题
    const alt = alternatives()
    expect(alt.querySelector('.type-meta')!.textContent).toBe(en('repairAlternatives'))
    expect(alt.querySelector('[data-alternative-managed-disclosure]')!.textContent).toContain(willInstall())
    expect(alt.querySelector('[data-alternative-managed-disclosure]')!.textContent).toContain(download())
    // 首段在备选之前
    expect(facts()!.compareDocumentPosition(alt) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('主按钮是受管环境：首段就是它的三条事实（装什么 / 下载什么多大 / 不改动什么）', async () => {
    await render({ ...OFFER, targets: [MANAGED_WITH_PY, OFFER.targets[0]] })
    const lead = facts()!
    expect(lead.querySelectorAll('p')).toHaveLength(3)
    expect(lead.textContent).toContain(willInstall())
    expect(lead.textContent).toContain(download())
    expect(alternatives().querySelector('[data-alternative-managed-disclosure]')).toBeNull()
  })

  it('主目标是项目环境（受管用不了）：首段说装什么、会改动哪个环境，不说下载', async () => {
    await render({ ...OFFER, targets: [OFFER.targets[0], { ...OFFER.targets[1], available: false, reason: 'managed_env_unavailable' }] })
    const lead = facts()!.textContent!
    expect(lead).toContain(willInstall())
    expect(lead).toContain(en('dependencyTargetHint_project_venv', { venv: '.venv' }))
    for (const wrong of ['MB', en('repairFactNetwork'), en('repairFactUntouched')]) expect(lead).not.toContain(wrong)
  })

  it('主动作是恢复自动检测：卡片不说要装 / 要下载任何东西', async () => {
    await render({ ...OFFER, targets: [MANAGED_WITH_PY], pinned: { python: '/opt/venv/bin/python', source: 'configured' } })
    expect(facts()).toBeNull()
    for (const wrong of [willInstall(), download(), 'MB']) expect(text()).not.toContain(wrong)
  })
})

describe('无障碍与窄栏', () => {
  it('所有动作都是真的 button / input，键盘到得了', async () => {
    await render({ ...OFFER, requirement: null, targets: [OFFER.targets[1]] })
    for (const b of buttons()) {
      // 原生 button 才有 Enter/Space 激活与焦点顺序；换成 div+onClick
      // 键盘用户就点不到了
      expect(b.tagName).toBe('BUTTON')
      expect((b.getAttribute('aria-label') ?? b.textContent ?? '').trim()).not.toBe('')
      expect(b.getAttribute('tabindex')).not.toBe('-1')
    }
    const input = document.querySelector('input')!
    expect(input.getAttribute('aria-label')).toBe(en('repairPackageAria'))
  })

  it('任何场景都留着「换一个 Python」的出口 —— 那是最后一条路', async () => {
    // 一度漏掉过：解析不出包名时卡片只剩「指定安装包」，而文案还写着
    // 「或者换一个已经装好它的 Python 环境」——指不出任何控件。
    // e2e（真浏览器）抓到的，这条把它钉在单测层。
    for (const offer of [
      OFFER,                                                     // 有可信包名
      { ...OFFER, requirement: null, targets: [], code: 'dependency_unresolved' },
      { ...OFFER, rounds_remaining: 0, targets: [],
        code: 'dependency_repair_rounds_exhausted' },            // 轮次用完
    ] as DependencyRepairOffer[]) {
      await act(async () => root?.unmount())
      host?.remove()
      await render(offer)
      expect(
        document.querySelector(`input[aria-label="${en('pathAria')}"]`),
        `这个场景少了「换一个 Python」的出口`,
      ).toBeTruthy()
    }
  })

  it('目标按钮不与说明并排 —— 右栏只有约 272px，并排会把它撑破', async () => {
    await render()
    const button = byName(en('repairUseProjectEnv'))!
    // Button 是 whitespace-nowrap + shrink-0 的：说明必须另起一行，
    // 靠 flex-col 而不是靠「希望它放得下」
    const row = button.parentElement!
    expect(row.className).toContain('flex-col')
    expect(row.textContent).toContain('.venv')
  })
})

describe('这台机器上已有的解释器（ADR 0044）', () => {
  const SYSTEM = {
    kind: 'system_interpreter' as const,
    venv: '',
    python: '/usr/local/bin/python3',
    modifies_user_environment: false,
    creates_environment: false,
    available: true,
    reason: '',
    python_version: '3.12.4',
    matplotlib_version: '3.9.2',
    support: 'verified',
  }
  const WITH_SYSTEM: DependencyRepairOffer = {
    ...OFFER,
    targets: [SYSTEM, ...OFFER.targets],
  }

  it('它是一键修复的首选：主文案说不下载不安装，路径与版本只在「高级」里', async () => {
    await render(WITH_SYSTEM)
    const card = document.querySelector('[data-one-click-repair]')!
    expect(card.getAttribute('data-one-click-repair')).toBe('system_interpreter')
    expect(text()).toContain(en('oneClickSentenceSystem', { module: 'lmfit' }))
    // 它是首选：不装、不联网、不改任何环境，比两种安装都便宜
    expect(byName(en('oneClickRepair'))!.getAttribute('data-variant')).toBe('primary') // primary
    expect(byName(en('repairUseProjectEnv'))!.getAttribute('data-variant')).not.toBe('primary')
    const detail = document.querySelector('[data-one-click-system]')!
    expect(detail.closest('[data-repair-advanced]')).toBeTruthy()
    expect(detail.textContent).toContain('/usr/local/bin/python3')
    expect(detail.textContent).toContain('Python 3.12.4')
  })

  it('点下去走项目环境 PATCH（带 module），不经安装计划，并把失败的渲染重新排上', async () => {
    adoptMock.mockResolvedValue({ ok: true, project: { open: true } } as never)
    useRenderStore.setState({
      byKey: {
        k: {
          ...(useRenderStore.getState().byKey.k ?? ({} as never)),
          fileId: 'Fig1.pdf', status: 'error', code: 'missing_dependency',
          module: 'lmfit', lastPatches: '[]', wantPatches: '[]', stale: false,
        } as never,
      },
      tracked: {},
    })
    await render(WITH_SYSTEM)
    await click(en('oneClickRepair'))
    expect(adoptMock).toHaveBeenCalledWith('/usr/local/bin/python3', 'lmfit')
    expect(planMock).not.toHaveBeenCalled()
    const after = useRenderStore.getState()
    expect(after.byKey.k.stale, '没标过期，图永远不会自己出来').toBe(true)
    expect(after.tracked['Fig1.pdf']).toBe(true)
  })

  it('采用失败时把后端那句话显示出来，不静默', async () => {
    adoptMock.mockRejectedValue(new Error('这个环境里也没有 lmfit'))
    await render(WITH_SYSTEM)
    await click(en('oneClickRepair'))
    expect(text()).toContain('这个环境里也没有 lmfit')
  })

  // 确认模式（ADR 0114）下列出的「改用项目自己的环境」：采用必须绑定用户看到它那一刻的候选 id 与环境代，
  // 与 `MissingDependencyCard` 同一个端点、同一个 409 语义（#814 评审 PRRT_kwDOT51-YM6pwk-Q 的第二个消费者）
  const PROJECT_RECOMMENDED = {
    ...SYSTEM,
    venv: '.venv',
    python: '.venv/bin/python',
    candidate: { id: 'env-shown', generation: 'gen-shown' },
  }

  it('带绑定的项目环境建议：点下去按「候选 id + 看到时的环境代」采用，绝不退回按路径采用', async () => {
    adoptCandidateMock.mockResolvedValue({ ok: true, project: { open: true } } as never)
    await render({ ...OFFER, targets: [PROJECT_RECOMMENDED, ...OFFER.targets] })
    await click(en('oneClickRepair'))
    expect(adoptCandidateMock).toHaveBeenCalledTimes(1)
    const [bound, , module] = adoptCandidateMock.mock.calls[0]
    expect(bound).toEqual({ id: 'env-shown', generation: 'gen-shown' })
    expect(module).toBe('lmfit')
    expect(adoptMock, '按路径无代次采用会拿到被重建后的另一代').not.toHaveBeenCalled()
    expect(planMock).not.toHaveBeenCalled()
  })

  it('环境在看到建议之后被重建（409 environment_changed）：报错、不改按路径重试', async () => {
    adoptCandidateMock.mockRejectedValue(
      new ApiError('这个环境在你确认之前被重建过，请重新查看再确认', 409, { code: 'environment_changed' }),
    )
    await render({ ...OFFER, targets: [PROJECT_RECOMMENDED, ...OFFER.targets] })
    await click(en('oneClickRepair'))
    expect(adoptCandidateMock).toHaveBeenCalledTimes(1)
    expect(adoptMock).not.toHaveBeenCalled()
    expect(text()).toContain('被重建过')
  })

  it('对照：用户手边的机器解释器（没有候选绑定）仍按路径采用', async () => {
    adoptMock.mockResolvedValue({ ok: true, project: { open: true } } as never)
    await render(WITH_SYSTEM)
    await click(en('oneClickRepair'))
    expect(adoptMock).toHaveBeenCalledWith('/usr/local/bin/python3', 'lmfit')
    expect(adoptCandidateMock).not.toHaveBeenCalled()
  })

  it('「指定安装包」装到第一个**安装**目标，绝不装进系统解释器', async () => {
    planMock.mockResolvedValue({ plan: PLAN })
    await render({ ...WITH_SYSTEM, requirement: null, code: 'dependency_unresolved' })
    const input = document.querySelector('input') as HTMLInputElement
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
    await act(async () => {
      setter.call(input, 'my-lab-tools')
      input.dispatchEvent(new Event('input', { bubbles: true }))
    })
    await click(en('repairContinue'))
    expect(planMock.mock.calls[0][0].target).toBe('project_venv')
  })

  it('探到了但没采用的系统解释器要说明原因，三种原因三句话', async () => {
    await render({
      ...OFFER,
      system_rejected: [
        { python: '/usr/bin/python3', code: 'project_env_unsupported_python', python_version: '3.9.6' },
        { python: '/opt/py/bin/python3', code: 'project_env_no_matplotlib', python_version: '3.12.0' },
      ],
    })
    expect(text()).toContain(
      en('repairSystemRejectedUnsupported', {
        python: '/usr/bin/python3', module: 'lmfit', version: '3.9.6', product: PRODUCT_NAME,
      }),
    )
    expect(text()).toContain(
      en('repairSystemRejectedNoMatplotlib', { python: '/opt/py/bin/python3', module: 'lmfit' }),
    )
  })

  it('解析不出包名 / 轮次用完时照样列出「改用已有环境」——采用不装东西', async () => {
    // Codex 评审 P2：这条路不依赖包名解析，也不消耗修复轮次；它正是用户仅剩的路
    for (const offer of [
      { ...WITH_SYSTEM, requirement: null, code: 'dependency_unresolved' },
      { ...WITH_SYSTEM, rounds_remaining: 0, code: 'dependency_repair_rounds_exhausted' },
    ] as DependencyRepairOffer[]) {
      await act(async () => root?.unmount())
      host?.remove()
      await render(offer)
      expect(byName(en('oneClickRepair')), offer.code).toBeTruthy()
      expect(text()).toContain(en('oneClickSentenceSystem', { module: 'lmfit' }))
      // 安装目标仍然不给：一键安装的前提是「知道要装什么」且还有轮次
      expect(byName(en('repairUseProjectEnv'))).toBeUndefined()
      expect(byName(en('repairInstallToManaged', { module: 'lmfit', product: PRODUCT_NAME }))).toBeUndefined()
    }
  })

  it('未经验证的 matplotlib 版本要如实标注', async () => {
    await render({ ...OFFER, targets: [{ ...SYSTEM, support: 'unverified_but_compatible' }] })
    expect(text()).toContain(en('repairSystemUnverified'))
  })
})

/** 这个标签页「发起过」plan-abc：`install()` 在发请求前就把 progress 记下了——store 只认自己发起的那条
 *  （`engine.dependency` 是广播，别的标签页 / 项目的计划不认；Codex #470 P2） */
const own = () => {
  if (!useDepRepairStore.getState().progress) {
    useDepRepairStore.setState({ progress: { plan_id: 'plan-abc', state: 'preparing', log: '', error: null, code: '' } })
  }
}

describe('渲染解释器被全局固定（#465）', () => {
  const PINNED: DependencyRepairOffer = {
    ...OFFER,
    targets: [],
    code: 'dependency_interpreter_pinned',
    pinned: { python: '/opt/venv/bin/python', source: 'configured' },
  }
  const failing = () =>
    useRenderStore.setState({
      byKey: {
        k: {
          ...(useRenderStore.getState().byKey.k ?? ({} as never)),
          fileId: 'Fig1.pdf', status: 'error', code: 'missing_dependency',
          module: 'lmfit', lastPatches: '[]', wantPatches: '[]', stale: false,
        } as never,
      },
      tracked: {},
    })

  it('不列任何安装目标、也不给「选择其他 Python」——装进去也不会被用', async () => {
    await render(PINNED)
    expect(document.querySelector('[data-dependency-repair-pinned]')).toBeTruthy()
    expect(text()).toContain(en('repairTitle', { module: 'lmfit' }))
    expect(text()).toContain('/opt/venv/bin/python')
    expect(byName(en('repairUseProjectEnv'))).toBeUndefined()
    expect(byName(en('repairInstallToManaged', { module: 'lmfit', product: PRODUCT_NAME }))).toBeUndefined()
    expect(document.querySelector('input')).toBeNull()
  })

  it('设置里指定的：「恢复自动检测」清全局设置、把失败的渲染重新排上', async () => {
    clearGlobalMock.mockResolvedValue({ ok: true } as never)
    failing()
    await render(PINNED)
    await click(en('repairPinnedClear'))
    expect(clearGlobalMock).toHaveBeenCalledWith(null)
    expect(planMock).not.toHaveBeenCalled()
    const after = useRenderStore.getState()
    expect(after.byKey.k.stale, '没标过期，图永远不会自己出来').toBe(true)
    expect(after.tracked['Fig1.pdf']).toBe(true)
  })

  it('清不掉时把后端那句话显示出来，渲染不重排', async () => {
    clearGlobalMock.mockRejectedValue(new Error('设置写入失败'))
    failing()
    await render(PINNED)
    await click(en('repairPinnedClear'))
    expect(text()).toContain('设置写入失败')
    expect(useRenderStore.getState().byKey.k.stale).toBe(false)
  })

  it('环境变量固定的：没有可清的按钮，点名供值的那个变量、然后重启', async () => {
    await render({
      ...PINNED,
      pinned: { python: '/opt/venv/bin/python', source: 'env_override', variable: 'MM_WORKER_PYTHON' },
    })
    expect(byName(en('repairPinnedClear'))).toBeUndefined()
    // 旧名供的值：只让用户清新名的话固定还在，所以这里必须是 MM_WORKER_PYTHON
    expect(text()).toContain(en('repairPinnedEnvHint', { variable: 'MM_WORKER_PYTHON' }))
    expect(text()).not.toContain('TAVOTTO_WORKER_PYTHON')
  })

  it('老服务端没给 variable 时退到新名', async () => {
    await render({ ...PINNED, pinned: { python: '/opt/venv/bin/python', source: 'env_override' } })
    expect(text()).toContain(en('repairPinnedEnvHint', { variable: 'TAVOTTO_WORKER_PYTHON' }))
  })

  it('offer 之后才钉上的：plan 的 400 带回 pinned，卡片切到「恢复自动检测」而不是留着旧目标', async () => {
    // Codex 评审 P2：只读 offer.pinned 的话，关掉错误又是那几个注定无效的目标
    planMock.mockRejectedValue(
      new ApiError('渲染解释器已固定', 400, {
        code: 'dependency_interpreter_pinned',
        pinned: { python: '/opt/late/bin/python', source: 'configured', variable: '' },
      }),
    )
    await render()
    // 预读那一步（新建第一代时卡片一出现就读计划）就撞上了固定：与点击后形成计划被拒同一支
    await act(async () => {})
    expect(document.querySelector('[data-dependency-repair-pinned]')).toBeTruthy()
    expect(text()).toContain('/opt/late/bin/python')
    expect(byName(en('repairInstallToManaged', { module: 'lmfit', product: PRODUCT_NAME }))).toBeUndefined()
    expect(byName(en('repairPinnedClear'))).toBeTruthy()
  })

  it('确认之后才钉上的：安装失败事件带回 pinned，同样切到「恢复自动检测」', async () => {
    planMock.mockResolvedValue({ plan: PLAN })
    installMock.mockResolvedValue({ started: true } as never)
    await render()
    await click(en('repairUseProjectEnv'))
    await click(en('repairInstallToProject'))
    await act(() => {
      own()
      useDepRepairStore.getState().onProgress({
        plan_id: 'plan-abc', state: 'failed', log: '', error: '渲染解释器已固定',
        code: 'dependency_interpreter_pinned',
        pinned: { python: '/opt/late/bin/python', source: 'configured', variable: '' },
      } as never)
    })
    expect(document.querySelector('[data-dependency-repair-pinned]')).toBeTruthy()
    expect(document.querySelector('[data-repair-failure]')).toBeNull()
    // 清掉之后 store 里的那条固定也要清，否则卡片永远停在这一支
    clearGlobalMock.mockResolvedValue({ ok: true, project: { open: true } } as never)
    await click(en('repairPinnedClear'))
    expect(useDepRepairStore.getState().pinned).toBeNull()
    expect(document.querySelector('[data-dependency-repair-pinned]')).toBeNull()
  })
})

describe('受管环境一次授权（2026-09-28）', () => {
  const MANAGED_OFFER: DependencyRepairOffer = {
    ...OFFER,
    targets: [{ ...OFFER.targets[1], private_python: PRIVATE_PYTHON }],
  }
  const managedButton = () => en('oneClickRepair')

  it('确认页里的要素点之前全在卡片上：装什么 / 联网 / 隔离且不改源码与现有环境 / 私有 Python 版本与体积', async () => {
    await render(MANAGED_OFFER)
    const block = document.querySelector('[data-dependency-disclosure]')
    expect(block, '受管目标下面没有披露块').toBeTruthy()
    const said = block!.textContent ?? ''
    expect(said).toContain(en('repairWillInstall', { requirement: 'lmfit>=1.3' }))
    // 三条各说一件事：装什么（上一行已查）/ 下载什么多大（含联网）/ 不改动什么
    expect(said).toContain(en('repairFactUntouched'))
    expect(said).toContain(
      en('repairFactDownload', { version: '3.13.15', mb: 45, product: PRODUCT_NAME }),
    )
  })

  it('点一次就形成计划并开始安装（只发 plan_id），不再有第二步「准备环境并继续」', async () => {
    planMock.mockResolvedValue({ plan: MANAGED_PLAN })
    installMock.mockResolvedValue({ started: true } as never)
    await render(MANAGED_OFFER)
    await click(managedButton())
    expect(planMock).toHaveBeenCalledWith({ module: 'lmfit', script: 'figure.py', target: 'tavotto_managed' })
    expect(installMock).toHaveBeenCalledTimes(1)
    expect(installMock).toHaveBeenCalledWith('plan-managed', 'dg-managed')
    expect(byName(en('repairPrepareAndContinue'))).toBeUndefined()
    expect(text()).toContain(en('repairPreparing'))
  })

  it('已缓存的私有 Python：计划比卡片说的少（不用下载），照样一次开始', async () => {
    planMock.mockResolvedValue({
      plan: { ...MANAGED_PLAN, private_python: { ...PRIVATE_PYTHON, cached: true, download_bytes: 0 } },
    })
    installMock.mockResolvedValue({ started: true } as never)
    await render(MANAGED_OFFER)
    await click(managedButton())
    expect(installMock).toHaveBeenCalledWith('plan-managed', 'dg-managed')
  })

  it('包名一个没变、影响摘要变了（次要依赖约束 beta<2 → beta>=2）：不执行，停在确认页按新计划重新披露（Codex r4217992305）', async () => {
    let resolvePreview!: (v: { plan: DependencyRepairPlan }) => void
    planMock.mockImplementationOnce(() => new Promise((r) => (resolvePreview = r)))
    installMock.mockResolvedValue({ started: true } as never)
    await render({ ...OFFER, targets: [{ ...OFFER.targets[1], available: null }] })
    // 卡片预读到的计划：用户点之前看到的就是这一份
    await act(async () => resolvePreview({ plan: { ...MANAGED_PLAN, plan_id: 'plan-preview' } }))
    // 点击时新形成的计划：包名 / 私有 Python 都与卡片说的相符，只有影响摘要变了
    planMock.mockResolvedValue({ plan: { ...MANAGED_PLAN, plan_id: 'plan-new', impact_digest: 'dg-changed' } })
    await click(en('oneClickRepair'))
    expect(installMock).not.toHaveBeenCalled()
    expect(useDepRepairStore.getState().plan?.plan_id).toBe('plan-new')
    // 用户在确认页对着新计划再点一次：发的是这份新计划自己的摘要（他刚看过的），不是旧的
    expect(byName(en('repairPrepareAndContinue'))).toBeTruthy()
    await click(en('repairPrepareAndContinue'))
    expect(installMock).toHaveBeenCalledTimes(1)
    expect(installMock).toHaveBeenCalledWith('plan-new', 'dg-changed')
  })

  it('摘要没变的重试照常；摘要变了的重试回到确认页而不是静默执行', async () => {
    planMock.mockResolvedValue({ plan: MANAGED_PLAN })
    installMock.mockResolvedValue({ started: true } as never)
    await render(MANAGED_OFFER)
    await click(en('oneClickRepair'))
    expect(installMock).toHaveBeenCalledTimes(1)
    expect(useDepRepairStore.getState().authorized?.impact_digest).toBe('dg-managed')
    // 重试时计划的影响变了：授权记的是上一份，新的不在其内 → 不执行
    planMock.mockResolvedValue({ plan: { ...MANAGED_PLAN, plan_id: 'plan-retry', impact_digest: 'dg-retry' } })
    await act(async () => {
      await useDepRepairStore.getState().retry()
    })
    expect(installMock).toHaveBeenCalledTimes(1)
    expect(useDepRepairStore.getState().plan?.plan_id).toBe('plan-retry')
  })

  it('私有 Python 的来源变了（卡片说自带 / 已缓存，计划换成另一个）：两边都零字节也停在确认页，不执行（Codex #742）', async () => {
    const zero = { ...PRIVATE_PYTHON, cached: true, download_bytes: 0, network_required: false }
    for (const [seen, planned] of [
      ['bundled', 'cached'],
      ['cached', 'bundled'],
    ] as const) {
      await act(async () => root?.unmount())
      host?.remove()
      useDepRepairStore.getState().reset()
      installMock.mockClear()
      planMock.mockResolvedValue({ plan: { ...MANAGED_PLAN, private_python: { ...zero, origin: planned } } })
      await render({ ...OFFER, targets: [{ ...OFFER.targets[1], private_python: { ...zero, origin: seen } }] })
      await click(managedButton())
      expect(installMock, `${seen} → ${planned} 被当成同一次授权执行了`).not.toHaveBeenCalled()
      expect(byName(en('repairPrepareAndContinue'))).toBeTruthy()
    }
    // 对照：来源相同照常一次开始；说的是下载、计划变成不用下载（更少）也照常开始
    for (const [seen, planned] of [
      [{ ...zero, origin: 'bundled' as const }, { ...zero, origin: 'bundled' as const }],
      [{ ...PRIVATE_PYTHON, origin: 'download' as const }, { ...zero, origin: 'cached' as const }],
    ]) {
      await act(async () => root?.unmount())
      host?.remove()
      useDepRepairStore.getState().reset()
      installMock.mockClear()
      installMock.mockResolvedValue({ started: true } as never)
      planMock.mockResolvedValue({ plan: { ...MANAGED_PLAN, private_python: planned } })
      await render({ ...OFFER, targets: [{ ...OFFER.targets[1], private_python: seen }] })
      await click(managedButton())
      expect(installMock).toHaveBeenCalledTimes(1)
    }
  })

  it('卡片只披露了一个包、点击时形成的计划多出别的包：停在确认页列出全部，不执行（Codex #760 P1）', async () => {
    // 预读时只有 lmfit（脚本 / 声明在点击前又多了 pandas，或预读那一步没算出来）
    planMock.mockResolvedValueOnce({ plan: MANAGED_PLAN })
    planMock.mockResolvedValue({ plan: { ...MANAGED_PLAN, requirements: ['lmfit>=1.3', 'pandas'] } })
    await render({ ...OFFER, targets: [OFFER.targets[1]] })
    await click(managedButton())
    expect(installMock).not.toHaveBeenCalled()
    expect(byName(en('repairPrepareAndContinue'))).toBeTruthy()
    expect(text()).toContain('将安装：lmfit>=1.3 和 pandas')
  })

  it('预读没读到（失败）时授权按 offer 那一个包算：计划一多就回到确认页', async () => {
    planMock.mockRejectedValueOnce(new ApiError('慢', 500, { code: 'internal_error' }))
    planMock.mockResolvedValue({ plan: { ...MANAGED_PLAN, requirements: ['lmfit>=1.3', 'pandas'] } })
    await render({ ...OFFER, targets: [OFFER.targets[1]] })
    await act(async () => {})
    await click(managedButton())
    expect(installMock).not.toHaveBeenCalled()
    expect(byName(en('repairPrepareAndContinue'))).toBeTruthy()
  })

  it('确认页点过之后重试：沿用用户看到的整份清单，再多出的包同样回到确认页', async () => {
    planMock.mockResolvedValueOnce({ plan: MANAGED_PLAN })
    planMock.mockResolvedValueOnce({ plan: { ...MANAGED_PLAN, requirements: ['lmfit>=1.3', 'pandas'] } })
    installMock.mockResolvedValue({ started: true } as never)
    await render({ ...OFFER, targets: [OFFER.targets[1]] })
    await click(managedButton())
    await click(en('repairPrepareAndContinue')) // 用户在确认页看过 lmfit + pandas 并同意
    expect(installMock).toHaveBeenCalledTimes(1)
    expect(useDepRepairStore.getState().authorized?.requirements).toEqual(['lmfit>=1.3', 'pandas'])
    await act(() => {
      useDepRepairStore.getState().onProgress({
        plan_id: 'plan-managed', state: 'failed', log: '', error: '', code: 'private_python_offline',
        target_kind: 'tavotto_managed',
      } as never)
    })
    // 重试：清单与授权相同 → 直接装；多出 numpy → 回到确认页
    planMock.mockResolvedValueOnce({
      plan: { ...MANAGED_PLAN, plan_id: 'again', requirements: ['lmfit>=1.3', 'pandas', 'numpy'] },
    })
    await act(async () => (document.querySelector('[data-dependency-repair-retry]') as HTMLButtonElement).click())
    await act(async () => {})
    expect(installMock).toHaveBeenCalledTimes(1)
    expect(byName(en('repairPrepareAndContinue'))).toBeTruthy()
  })

  it('卡片没说要下载、计划却要下载：停在确认页，不执行', async () => {
    // 预读时计划不用下载（卡片据此写那一句、授权），点下去形成的计划却要下载：不执行
    planMock.mockResolvedValueOnce({ plan: { ...MANAGED_PLAN, private_python: null } })
    planMock.mockResolvedValue({ plan: MANAGED_PLAN })
    await render({ ...OFFER, targets: [OFFER.targets[1]] })
    await click(managedButton())
    expect(installMock).not.toHaveBeenCalled()
    expect(byName(en('repairPrepareAndContinue'))).toBeTruthy()
  })

  it('项目环境那条仍然先到确认页（改用户环境要明确确认，ADR 0019 §八）', async () => {
    planMock.mockResolvedValue({ plan: PLAN })
    await render()
    await click(en('repairUseProjectEnv'))
    expect(installMock).not.toHaveBeenCalled()
    expect(byName(en('repairInstallToProject'))).toBeTruthy()
  })
})

describe('失败 / 取消之后就地重试', () => {
  const MANAGED_OFFER: DependencyRepairOffer = {
    ...OFFER,
    targets: [{ ...OFFER.targets[1], private_python: PRIVATE_PYTHON }],
  }
  const retryButton = () => document.querySelector('[data-dependency-repair-retry]') as HTMLButtonElement | null
  const startManaged = async () => {
    planMock.mockResolvedValue({ plan: MANAGED_PLAN })
    installMock.mockResolvedValue({ started: true } as never)
    await render(MANAGED_OFFER)
    await click(en('oneClickRepair'))
    expect(installMock).toHaveBeenCalledTimes(1)
  }
  const finish = (state: string, code: string, extra: Record<string, unknown> = {}) =>
    act(() => {
      useDepRepairStore.getState().onProgress({
        plan_id: 'plan-managed', state, log: '', error: '后端原文', code, target_kind: 'tavotto_managed',
        ...extra,
      } as never)
    })

  it('私有 Python 下载失败（实测那一条）：有「重试」，点了再走一次授权并重新安装', async () => {
    await startManaged()
    await finish('failed', 'private_python_offline')
    expect(text()).toContain(en('repairError.private_python_offline'))
    expect(retryButton(), '失败态只有「知道了」').toBeTruthy()
    expect(byName(en('repairClose'))).toBeTruthy()
    planMock.mockResolvedValue({ plan: { ...MANAGED_PLAN, plan_id: 'plan-again' } })
    await act(async () => retryButton()!.click())
    await act(async () => {})
    expect(planMock).toHaveBeenCalledTimes(3) // 预读 + 第一次授权 + 重试
    expect(installMock).toHaveBeenLastCalledWith('plan-again', 'dg-managed')
  })

  it('私有 Python 的来源在确认之后变了（#743 private_python_source_changed）：与计划过期同类，给「重试」', async () => {
    await startManaged()
    await finish('failed', 'private_python_source_changed')
    expect(document.querySelector('[data-repair-failure]')!.textContent).toBe(
      en('repairErrorShort.private_python_source_changed'),
    )
    expect(retryButton()).toBeTruthy()
  })

  it('取消之后同样可以重试', async () => {
    await startManaged()
    await finish('cancelled', 'dependency_install_cancelled')
    expect(retryButton()).toBeTruthy()
  })

  it('pip 已经装成之后才取消（验证 / 自检期间）：后端说 retryable=false，不给必败的「重试」', async () => {
    await startManaged()
    await finish('cancelled', 'dependency_install_cancelled', { retryable: false })
    expect(retryButton()).toBeNull()
    expect(byName(en('repairClose'))).toBeTruthy()
  })

  it('哈希不符之类重试不会变的失败不给「重试」', async () => {
    await startManaged()
    await finish('failed', 'dependency_hash_mismatch')
    expect(retryButton()).toBeNull()
    expect(byName(en('repairClose'))).toBeTruthy()
  })

  it('重试时计划超出了上次授权（换了一份更大的 Python）：停在确认页', async () => {
    await startManaged()
    await finish('failed', 'private_python_offline')
    planMock.mockResolvedValue({
      plan: { ...MANAGED_PLAN, plan_id: 'plan-bigger', private_python: { ...PRIVATE_PYTHON, id: 'other' } },
    })
    await act(async () => retryButton()!.click())
    await act(async () => {})
    expect(installMock).toHaveBeenCalledTimes(1)
    expect(byName(en('repairPrepareAndContinue'))).toBeTruthy()
  })

  it('项目环境的重试回到确认页，不直接改用户环境', async () => {
    planMock.mockResolvedValue({ plan: PLAN })
    installMock.mockResolvedValue({ started: true } as never)
    await render()
    await click(en('repairUseProjectEnv'))
    await click(en('repairInstallToProject'))
    await act(() => {
      useDepRepairStore.getState().onProgress({
        plan_id: 'plan-abc', state: 'failed', log: '', error: '', code: 'dependency_network_unavailable',
        target_kind: 'project_venv',
      } as never)
    })
    await act(async () => retryButton()!.click())
    await act(async () => {})
    expect(planMock).toHaveBeenCalledTimes(3) // 预读 + 形成计划 + 重试
    expect(installMock).toHaveBeenCalledTimes(1)
    expect(byName(en('repairInstallToProject'))).toBeTruthy()
  })
})

describe('安装进度', () => {
  const progress = (state: string, extra: Record<string, unknown> = {}) =>
    act(() => {
      own()
      useDepRepairStore.getState().onProgress({
        plan_id: 'plan-abc', state, log: '', error: null, code: '',
        distribution: 'lmfit', ...extra,
      } as never)
    })

  it('别的标签页 / 项目的计划不认：不是自己发起的 plan_id，进度不进 store', async () => {
    await render()
    await act(() => {
      useDepRepairStore.getState().onProgress({
        plan_id: 'plan-someone-else', state: 'done', log: '', error: null, code: '',
        distribution: 'lmfit', result: { version: '1.3.2' },
      } as never)
    })
    expect(useDepRepairStore.getState().progress).toBeNull()
  })

  it('进度只有一行：「正在下载 Python… 12 / 48 MB」，下载完换一句，进了下一步字节数就不再出现', async () => {
    const card = () => document.querySelector('[data-repair-line]')!.closest('.shadow-card')!
    const line = () => document.querySelector('[data-repair-line]')!.textContent
    await render()
    await progress('downloading_python', {
      target_kind: 'tavotto_managed',
      result: { download: { stage: 'downloading', done_bytes: 12 * 1048576, total_bytes: 48 * 1048576 } },
    })
    expect(line()).toBe(
      `${en('dependencyPrepareState_downloading_python')} ${en('repairDownloadBytes', { done: 12, total: 48 })}`,
    )
    expect(document.querySelector('[data-repair-download]')!.getAttribute('aria-valuenow')).toBe('25')
    // 进行中默认可见：一行进度 + 「取消」，没有别的说明
    // 进行中默认可见：一行进度 + 「取消」+ 折叠的「详情」（四个阶段与日志在里面）
    expect(visibleBlocks(card()).map((b) => b.tag)).toEqual(['p', 'button', 'summary'])
    const stages = document.querySelector('[data-repair-stages]')!
    expect(stages.closest('details')!.open).toBe(false)
    expect(
      [...stages.querySelectorAll('[data-repair-stage]')].map(
        (li) => `${li.getAttribute('data-repair-stage')}:${li.getAttribute('data-stage-state')}`,
      ),
    ).toEqual(['python:active', 'env:pending', 'packages:pending', 'rerun:pending'])
    // 下载完、在解压：不再是字节数
    await progress('downloading_python', {
      target_kind: 'tavotto_managed',
      result: { download: { stage: 'extracting', done_bytes: 48 * 1048576, total_bytes: 48 * 1048576 } },
    })
    expect(line()).toBe(en('repairDownloadUnpacking'))
    // 进了创建环境：后端沿用上一条 result，下载那段还挂在进度上——不许再说字节数、画进度条
    await progress('creating_env', {
      target_kind: 'tavotto_managed',
      result: { download: { stage: 'committed', done_bytes: 48 * 1048576, total_bytes: 48 * 1048576 } },
    })
    expect(line()).toBe(`${en('repairCreatingEnv')}${en('repairStep', { n: 2, total: 4 })}`)
    await progress('installing', { target_kind: 'tavotto_managed' })
    expect(line()).toBe(`${en('repairInstalling', { module: 'lmfit' })}${en('repairStep', { n: 3, total: 4 })}`)
    // 计划里装的不止一个包：进度行说整组（真正在装的），不只是用户点的那一个
    await progress('installing', { target_kind: 'tavotto_managed', requirements: ['pandas', 'lmfit>=1.3'] })
    expect(line()).toBe(`${en('repairInstalling', { module: 'pandas 和 lmfit' })}${en('repairStep', { n: 3, total: 4 })}`)
    await progress('installing', { target_kind: 'tavotto_managed', requirements: ['numpy', 'pandas', 'lmfit'] })
    expect(line()).toBe(`${en('repairInstalling', { module: 'numpy 等 3 个包' })}${en('repairStep', { n: 3, total: 4 })}`)
    // 装进项目自己的环境只有两步
    await progress('installing', { target_kind: 'project_venv' })
    expect(line()).toBe(`${en('repairInstalling', { module: 'lmfit' })}${en('repairStep', { n: 1, total: 2 })}`)
    expect(document.querySelector('[data-repair-download]')).toBeNull()
  })

  it('项目环境的乐观进度（SSE 还没来）就带着目标：只有两步，不显示「准备 Python」（Codex #742）', async () => {
    planMock.mockResolvedValue({ plan: PLAN })
    installMock.mockImplementation(() => new Promise(() => {})) // 安装请求一直挂着：此刻只有乐观的那一条
    await render()
    await click(en('repairUseProjectEnv'))
    await click(en('repairInstallToProject'))
    const p = useDepRepairStore.getState().progress!
    expect(p.state).toBe('preparing')
    expect(p.target_kind).toBe('project_venv')
    expect(document.querySelector('[data-repair-line]')!.textContent).toBe(
      `${en('repairPreparing')}${en('repairStep', { n: 1, total: 2 })}`,
    )
    expect([...document.querySelectorAll('[data-repair-stage]')].map((li) => li.getAttribute('data-repair-stage')))
      .toEqual(['packages', 'rerun'])
    // 之后某一条快照没带目标：沿用上一条的，不在两步与四步之间跳
    await act(() => {
      useDepRepairStore.getState().onProgress({ plan_id: 'plan-abc', state: 'installing', log: '', error: null, code: '' } as never)
    })
    expect(useDepRepairStore.getState().progress!.target_kind).toBe('project_venv')
    expect(document.querySelector('[data-repair-line]')!.textContent).toContain(en('repairStep', { n: 1, total: 2 }))
  })

  it('私有 Python 的子阶段按 #743 的真实形状各说各的；自带归档不说「下载」；认不出的降级成通用一句', async () => {
    // #743（feat/one-click-python-backend @ 6399168b0）`deprepair._provision_private_base` 发的进度：
    // state=downloading_python，result = {download: {stage, done_bytes, total_bytes}, private_python: {…, origin}}；
    // stage 取自 `privatepython.STAGE_*` 闭集（downloading / verifying / extracting / launching / committed）
    const shape = (stage: string, origin = 'download', done = 25 * 1048576) => ({
      target_kind: 'tavotto_managed',
      result: {
        download: { stage, done_bytes: done, total_bytes: 25 * 1048576 },
        private_python: { id: 'pbs', version: '3.13.15', target: 'darwin-arm64', download_bytes: 25 * 1048576, source_host: 'github.com', origin },
      },
    })
    const line = () => document.querySelector('[data-repair-line]')!.textContent
    await render()
    for (const [stage, key] of [
      ['verifying', 'repairPythonVerifying'],
      ['extracting', 'repairDownloadUnpacking'],
      ['launching', 'repairPythonLaunching'],
      ['committed', 'repairPythonPreparing'],
      ['stage_from_the_future', 'repairPythonPreparing'],
    ] as const) {
      await progress('downloading_python', shape(stage))
      expect(line(), stage).toBe(en(key))
    }
    // 安装包自带的归档：后端同样先发一条 0 字节的 downloading——不许说「正在下载… 0 / 25 MB」
    await progress('downloading_python', shape('downloading', 'bundled', 0))
    expect(line()).toBe(en('repairPythonPreparing'))
    expect(line()).not.toContain('MB')
    // 真在下载：字节数照说
    await progress('downloading_python', shape('downloading', 'download', 10 * 1048576))
    expect(line()).toContain('10 / 25 MB')
  })

  it('换用了 PyPI 镜像（#743 的真实形状）：只在「详情」里说一句；之后的快照照样带着', async () => {
    // 照抄后端 #743（feat/one-click-python-backend @ 6399168b0）`deprepair._note_mirror` 写进进度记录的形状：
    // 顶层字符串字段 `pypi_mirror`，值是 `PYPI_MIRROR_URL`；日志里同时有那一行说明。此后每个快照（含终态）都带
    const mirror = 'https://pypi.tuna.tsinghua.edu.cn/simple'
    const snapshot = {
      log: `Collecting openpyxl\nERROR: Could not find a version\n\n连不上默认的 Python 包源，改用 PyPI 镜像 ${mirror} 重试一次\n`,
      plan_id: 'plan-abc',
      state: 'installing',
      code: '',
      error: null,
      result: null,
      import_name: 'lmfit',
      distribution: 'lmfit',
      target_kind: 'tavotto_managed',
      script: 'figure.py',
      pypi_mirror: mirror,
    }
    await render()
    await act(() => {
      own()
      useDepRepairStore.getState().onProgress(snapshot as never)
    })
    const note = document.querySelector('[data-repair-pypi-mirror]')!
    expect(note.closest('details')!.open).toBe(false)
    expect(note.textContent).toBe(en('repairPypiMirror', { mirror }))
    await act(() => {
      useDepRepairStore.getState().onProgress({ ...snapshot, state: 'verifying' } as never)
    })
    expect(document.querySelectorAll('[data-repair-pypi-mirror]')).toHaveLength(1)
  })

  it('没用镜像（进度里没有 pypi_mirror 这个键）：一个字都不说，也不报错', async () => {
    await render()
    await progress('installing', { log: 'Collecting lmfit' })
    expect(document.querySelector('[data-repair-pypi-mirror]')).toBeNull()
    expect(text()).not.toContain(en('repairPypiMirror', { mirror: '' }).slice(0, 6))
    expect(document.querySelector('[data-repair-line]')).toBeTruthy()
  })

  it('换用了 PyPI 镜像：只在「安装详情」里说一句', async () => {
    await render()
    await progress('installing', { pypi_mirror: 'https://pypi.tuna.tsinghua.edu.cn/simple' })
    const note = document.querySelector('[data-repair-pypi-mirror]')!
    expect(note.closest('details')).toBeTruthy()
    expect(note.textContent).toBe(en('repairPypiMirror', { mirror: 'https://pypi.tuna.tsinghua.edu.cn/simple' }))
  })

  it('四个阶段各一句话，pip 日志折叠在「安装详情」里', async () => {
    await render()
    await progress('installing', { log: 'Collecting lmfit\n'.repeat(50) })
    expect(text()).toContain(en('repairInstalling', { module: 'lmfit' }))
    // 日志在 details 里，不糊在主文案上
    const details = document.querySelector('details')
    expect(details).toBeTruthy()
    expect(details!.textContent).toContain(en('repairDetails'))
    expect(details!.querySelector('pre')?.textContent).toContain('Collecting lmfit')
    await progress('verifying')
    expect(text()).toContain(en('repairVerifying'))
  })

  it('安装中可以取消', async () => {
    cancelMock.mockResolvedValue({ cancelling: true })
    await render()
    await progress('installing')
    await click(en('repairCancel'))
    expect(cancelMock).toHaveBeenCalledWith('plan-abc')
  })

  it('取消用户自己的环境之后不假装完整回滚', async () => {
    await render()
    await progress('cancelled', { target_kind: 'project_venv', code: 'dependency_install_cancelled' })
    expect(text()).toContain(en('repairCancelledProjectEnv'))
    // 受管环境那句是另一种处置，不能混用
    expect(text()).not.toContain(en('repairCancelledManaged', { product: PRODUCT_NAME }))
  })

  it('装完之后把那次失败的渲染重新排上 —— 否则图永远不会自己出来', async () => {
    // Codex 评审 P1：失败那次的 wantPatches 仍等于当前 overrides，同步器
    // 会跳过它。少了这一步，「点一次 → 图出来」这条主路走不完，用户要
    // 改点别的或刷新页面才看得到图。
    useRenderStore.setState({
      byKey: {
        k: {
          ...(useRenderStore.getState().byKey.k ?? ({} as never)),
          fileId: 'Fig1.pdf', status: 'error', code: 'missing_dependency',
          module: 'lmfit', lastPatches: '[]', wantPatches: '[]', stale: false,
        } as never,
      },
      tracked: {},
    })
    await render()
    await progress('done', { result: { version: '1.3.2' } })
    const after = useRenderStore.getState()
    expect(after.byKey.k.stale, '没标过期').toBe(true)
    expect(after.byKey.k.wantPatches, '没清 wantPatches，同步器会跳过它').toBeNull()
    expect(after.tracked['Fig1.pdf'], '文件级跟踪位没打开').toBe(true)
  })

  it('失败时按稳定 code 给出可执行的下一步', async () => {
    await render()
    await progress('failed', { code: 'dependency_requires_build', error: '后端中文原文' })
    expect(text()).toContain(en('repairError.dependency_requires_build'))
    expect(text()).not.toContain('后端中文原文')
  })

  it('只在 backend.* 表里有文案的 code 也按当前语言翻，不漏后端原文', async () => {
    await i18n.changeLanguage('en-US')
    await render()
    await progress('failed', { code: 'environment_in_use_by_native_session', error: '后端中文原文' })
    expect(text()).toContain(t('backend.environment_in_use_by_native_session', { ns: 'errors' }))
    expect(text()).not.toContain('后端中文原文')
  })

  it('没登记文案的 code 退回后端原文，不显示 key', async () => {
    await render()
    await progress('failed', { code: 'something_new_from_the_future', error: '后端原文' })
    expect(text()).toContain('后端原文')
    expect(text()).not.toContain('engine.repairError')
  })
})

describe('英文界面', () => {
  beforeEach(async () => {
    await i18n.changeLanguage('en-US')
  })

  it('关键路径没有中文泄漏', async () => {
    planMock.mockResolvedValue({ plan: PLAN })
    await render()
    expect(text()).toContain('This script is missing lmfit')
    await click('Install into project environment')
    expect(text()).toContain('This modifies the project’s Python environment.')
    // 整张卡片里一个 CJK 字符都不该有
    expect(text()).not.toMatch(/[一-鿿]/)
  })

  it('失败文案也是英文', async () => {
    await render()
    await act(() => {
      own()
      useDepRepairStore.getState().onProgress({
        plan_id: 'plan-abc', state: 'failed', log: '', error: null,
        code: 'pip_unavailable', distribution: 'lmfit',
      } as never)
    })
    expect(text()).not.toMatch(/[一-鿿]/)
  })
})
