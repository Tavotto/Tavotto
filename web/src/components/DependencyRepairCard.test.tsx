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
}))

import {
  cancelDependencyPlan,
  createDependencyPlan,
  fetchEngineEnvironment,
  installDependencyPlan,
  type DependencyRepairOffer,
  type DependencyRepairPlan,
} from '@/lib/api'
import { DependencyRepairCard } from '@/components/DependencyRepairCard'
import { i18n, t } from '@/i18n'
import { useDepRepairStore } from '@/store/depRepairStore'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const planMock = vi.mocked(createDependencyPlan)
const installMock = vi.mocked(installDependencyPlan)
const cancelMock = vi.mocked(cancelDependencyPlan)
const envMock = vi.mocked(fetchEngineEnvironment)

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
}

const PLAN: DependencyRepairPlan = {
  plan_id: 'plan-abc',
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
  planMock.mockReset()
  installMock.mockReset()
  cancelMock.mockReset()
  envMock.mockReset()
  envMock.mockResolvedValue({} as never)
  useDepRepairStore.getState().reset()
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
    expect(text()).toContain(en('repairTitle', { module: 'lmfit' }))
    for (const jargon of ['pip', 'site-packages', 'virtualenv', 'venv activate']) {
      expect(text().toLowerCase()).not.toContain(jargon)
    }
  })

  it('两个目标都列出来：装进项目环境 / 建一个 Tavotto 环境', async () => {
    await render()
    expect(byName(en('repairUseProjectEnv'))).toBeTruthy()
    expect(byName(en('repairCreateManaged'))).toBeTruthy()
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

  it('Tavotto 隔离环境的文案说明不会动用户已有的环境', async () => {
    planMock.mockResolvedValue({
      plan: { ...PLAN, target_kind: 'tavotto_managed', python: '',
              creates_environment: true, modifies_user_environment: false },
    })
    await render()
    await click(en('repairCreateManaged'))
    expect(text()).toContain(en('repairConfirmManaged'))
    expect(text()).not.toContain(en('repairModifiesEnv'))
    expect(byName(en('repairPrepareAndContinue'))).toBeTruthy()
  })

  it('确认之后只发 plan_id —— 前端不自己拼包名', async () => {
    planMock.mockResolvedValue({ plan: PLAN })
    installMock.mockResolvedValue({ started: true } as never)
    await render()
    await click(en('repairUseProjectEnv'))
    await click(en('repairInstallToProject'))
    expect(installMock).toHaveBeenCalledWith('plan-abc')
    expect(installMock.mock.calls[0]).toHaveLength(1)
  })

  it('解析不出包名时不给一键安装，只给「指定安装包」', async () => {
    await render({ ...OFFER, requirement: null, targets: [], code: 'dependency_unresolved' })
    expect(text()).toContain(en('repairUnresolved', { module: 'lmfit' }))
    expect(byName(en('repairUseProjectEnv'))).toBeUndefined()
    expect(byName(en('repairCreateManaged'))).toBeUndefined()
    expect(text()).toContain(en('repairSpecifyPackage'))
  })

  it('即使后端给了目标，没有可信包名也不给一键安装', async () => {
    // `requirement` 与 `targets` 是两件事：解析不出包名时后端本来就不该给
    // 目标，但**前端不靠这条约定**——一键安装的前提是「知道要装什么」，
    // 而不是「有地方可以装」。这一条守的正是那个前提。
    await render({ ...OFFER, requirement: null, code: 'dependency_unresolved' })
    expect(byName(en('repairUseProjectEnv'))).toBeUndefined()
    expect(byName(en('repairCreateManaged'))).toBeUndefined()
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
    expect(byName(en('repairCreateManaged'))).toBeUndefined()
    expect(byName(en('repairUseProjectEnv'))).toBeTruthy()
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

describe('安装进度', () => {
  const progress = (state: string, extra: Record<string, unknown> = {}) =>
    act(() => {
      useDepRepairStore.getState().onProgress({
        plan_id: 'plan-abc', state, log: '', error: null, code: '',
        distribution: 'lmfit', ...extra,
      } as never)
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
    expect(text()).not.toContain(en('repairCancelledManaged'))
  })

  it('失败时按稳定 code 给出可执行的下一步', async () => {
    await render()
    await progress('failed', { code: 'dependency_requires_build', error: '后端中文原文' })
    expect(text()).toContain(en('repairError.dependency_requires_build'))
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
    expect(text()).toContain('This project is missing lmfit')
    await click('Install into project environment')
    expect(text()).toContain('This modifies this project’s existing Python environment.')
    // 整张卡片里一个 CJK 字符都不该有
    expect(text()).not.toMatch(/[一-鿿]/)
  })

  it('失败文案也是英文', async () => {
    await render()
    await act(() => {
      useDepRepairStore.getState().onProgress({
        plan_id: 'plan-abc', state: 'failed', log: '', error: null,
        code: 'pip_unavailable', distribution: 'lmfit',
      } as never)
    })
    expect(text()).not.toMatch(/[一-鿿]/)
  })
})
