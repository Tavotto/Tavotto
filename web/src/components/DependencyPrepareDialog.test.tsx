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
} from '@/lib/api'
import { DependencyPrepareDialog } from '@/components/DependencyPrepareDialog'
import { DependencyPrepareButton } from '@/components/WorkdirRow'
import { i18n, t } from '@/i18n'
import { setCurrentProjectId } from '@/lib/session'
import { useDepRepairStore } from '@/store/depRepairStore'
import { useEnvStore } from '@/store/envStore'
import { useRenderStore } from '@/store/renderStore'

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
    expect(text()).toContain(en('dependencyPrepareTitle', { count: 2 }))
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
    await act(async () => button(en('dependencyPrepareRun'))!.click())
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
    await act(async () => button(en('dependencyPrepareRun'))!.click())
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
    await act(async () => button(en('dependencyPrepareRun'))!.click())
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
    await act(async () => button(en('dependencyPrepareRun'))!.click())
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
    expect(text()).toContain(en('dependencyPreparePrivatePython', { version: '3.13.15', mb: 24 }))
    expect(radio('tavotto_managed')!.disabled).toBe(false)
    // 同一时刻只开一份：换载荷要先关掉这一份
    await act(async () => useEnvStore.setState({ dependencyPreparation: null }))
    await act(async () =>
      useEnvStore.getState().requestDependencyPreparation(
        clean({ cached: true, download_bytes: 0, network_required: false }),
      ),
    )
    expect(text()).toContain(en('dependencyPreparePrivatePythonCached', { version: '3.13.15' }))
    expect(text()).not.toContain('MB')
    // 别的项目已经把私有 Python 供应好了（required=false、零字节）：本项目照样要建自己的一代，来源照样说出口
    await act(async () => useEnvStore.setState({ dependencyPreparation: null }))
    await act(async () =>
      useEnvStore.getState().requestDependencyPreparation(
        clean({ required: false, cached: true, download_bytes: 0, network_required: false }),
      ),
    )
    expect(text()).toContain(en('dependencyPreparePrivatePythonCached', { version: '3.13.15' }))
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
    expect(text()).not.toContain(en('dependencyPrepareBody', { script: 'figure.py' }))
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

  it('一个都没装齐：说出口、安装目标预选；没装齐的收在折叠里只说还缺什么（不可选）', async () => {
    await render(<DependencyPrepareDialog />)
    await act(async () =>
      useEnvStore.getState().requestDependencyPreparation(
        offer({ user_environments: [userEnv({ missing: ['tabulate', 'six'], satisfies: false })] }),
      ),
    )
    expect(text()).toContain(en('userEnvNone'))
    expect(text()).toContain(en('dependencyPrepareBody', { script: 'figure.py' }))
    expect(radio('tavotto_managed')!.checked).toBe(true)
    expect(envRadio('conda')).toBeNull()
    const partial = document.querySelector('[data-user-env-partial]')!
    expect(partial.textContent).toContain(en('userEnvMissing', { packages: 'tabulate, six' }))
    expect(button(en('dependencyPrepareRun'))).toBeDefined()
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
