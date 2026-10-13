/**
 * 「确认包名」（Import Origin PR6，维护者裁决 C1：预填 + 用户确认）。判据的主语：
 * ① 输入框只出现在后端带了预填（`suggested_distribution`）的条目上，初始值就是预填，其它来源未定的 import 只解释；
 * ② 提交 = `createJointDependencyPlan({ script, user_specified })`，一份计划一次授权；
 * ③ 确认页那一句**必须**说清「用户指定、未经可信来源核实」（读 `plan.impact.user_specified[].trust`），读不到披露就不让继续；
 *    `impact_digest` 是这份计划自己的、原样交给 `/prepare`；
 * ④ 每个状态 × 每个语种：可见的句子 1 句、主按钮至多 1 个，解释 / 列表 / 原因都在默认折叠的「详情」里；
 * ⑤ 语法 / 资格被拒、装后核验没过，各一句人话；⑥ 切项目清掉旧的预填与输入，晚到的响应不落进新项目。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  createJointDependencyPlan: vi.fn(),
  prepareJointDependencies: vi.fn(),
  cancelJointDependencies: vi.fn(),
  fetchEngineEnvironment: vi.fn(),
}))

import {
  createJointDependencyPlan,
  DEPENDENCY_PREPARATION_CODE,
  prepareJointDependencies,
  fetchEngineEnvironment,
  type DependencyPreparationOffer,
  type JointDependencyRepairPlan,
  type JointOrigin,
} from '@/lib/api'
import { ImportOriginDialog } from '@/components/ImportOriginDialog'
import { i18n, resources, t } from '@/i18n'
import { visibleBlocks, visiblePrimaryButtons, visibleSentenceCount } from '@/test/visibleBlocks'
import { setCurrentProjectId } from '@/lib/session'
import { __resetDepRepairParkingForTests, useDepRepairStore } from '@/store/depRepairStore'
import { useEnvStore } from '@/store/envStore'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const LOCALES = Object.keys(resources)
const planMock = vi.mocked(createJointDependencyPlan)
const prepareMock = vi.mocked(prepareJointDependencies)
const envMock = vi.mocked(fetchEngineEnvironment)
const ot = (key: string, values?: Record<string, unknown>) => t(`prep.origin.${key}`, { ns: 'workspace', ...values })

const origin = (name: string, over: Partial<JointOrigin> = {}): JointOrigin => ({
  import_name: name,
  bucket: 'unknown',
  distribution: '',
  reason: 'distribution_unresolved',
  resolution_status: 'unresolved',
  distribution_status: '',
  origin_kind: 'unresolved',
  provenance: '',
  candidates: [],
  coverage: '',
  ...over,
})

const LAB = origin('lab_tools', { suggested_distribution: 'lab-tools' })
const PEAK = origin('peak_finder', { suggested_distribution: 'peak-finder' })
const CV = origin('cv2', { reason: 'module_origin_ambiguous', resolution_status: 'ambiguous', distribution_status: 'module_origin_ambiguous', candidates: ['opencv-python', 'opencv-python-headless'] })
const DEV = origin('mylib', { bucket: 'third_party', reason: 'editable_dependency_not_reproducible', provenance: 'editable' })

const offer = (origins: JointOrigin[] = [LAB, CV, DEV], over: Partial<DependencyPreparationOffer> = {}): DependencyPreparationOffer =>
  ({
    code: DEPENDENCY_PREPARATION_CODE,
    script: 'figure.py',
    plan: {
      plan_version: 1,
      status: 'nothing_needed',
      target_kind: 'tavotto_managed',
      script: 'figure.py',
      needed: [],
      missing: [],
      satisfied: [],
      unknown: origins.map((o) => o.import_name),
      origins,
      possible: [],
      requirements: [],
      constraints: [],
      require_hashes: false,
      adapter: [],
      blocked: [],
      selection: { selected_groups: [], available_groups: [], unselected_groups: [], skipped_marker: [] },
      identity: 'abc',
    },
    target_kind: 'tavotto_managed',
    targets: [],
    rounds_remaining: 3,
    skipped: false,
    ...over,
  }) as DependencyPreparationOffer

const bound = (over: Partial<JointDependencyRepairPlan> = {}, specified = [{ import_name: 'lab_tools', requirement: 'lab-tools>=1.2', trust: 'user_specified_unverified' as const }]): JointDependencyRepairPlan =>
  ({
    plan_id: 'jp-origin',
    script: 'figure.py',
    target_kind: 'tavotto_managed',
    python: '',
    requirements: specified.map((s) => s.requirement),
    constraints: [],
    require_hashes: false,
    adapter: [],
    identity: 'abc',
    needed_imports: ['lab_tools'],
    groups: [],
    modifies_user_environment: false,
    creates_environment: true,
    network_required: true,
    expires_at: 0,
    joint: offer().plan,
    impact_digest: 'dg-bound',
    impact: {
      impact_version: 2,
      target_kind: 'tavotto_managed',
      scope: 'managed_generation',
      installs: specified.map((s) => s.requirement),
      constraints: [],
      adapter: [],
      require_hashes: false,
      groups: [],
      creates_environment: true,
      modifies_user_environment: false,
      private_python: null,
      network_required: true,
      writes: [],
      rollback: 'generation_atomic',
      scope_policy: '',
      drops: [],
      changes: [],
      user_specified: specified,
      environment_ref: 'env-ref',
    },
    ...over,
  }) as JointDependencyRepairPlan

let host: HTMLDivElement
let root: Root
async function render() {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => root.render(<ImportOriginDialog />))
  await act(async () => {})
}
const dialog = () => document.querySelector('[data-dialog="import-origin"]') as HTMLElement | null
const text = () => dialog()?.textContent ?? ''
const input = (name: string) => document.querySelector(`[data-origin-input="${name}"]`) as HTMLInputElement | null
const review = () => document.querySelector('[data-origin-primary="review"]') as HTMLButtonElement | null
const confirmBtn = () => document.querySelector('[data-origin-primary="confirm"]') as HTMLButtonElement | null
const open = (o = offer()) =>
  act(async () => {
    useDepRepairStore.getState().openOrigin(o)
  })
async function typeInto(name: string, value: string) {
  const el = input(name)!
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(el, value)
    el.dispatchEvent(new Event('input', { bubbles: true }))
  })
}
const openDetails = () =>
  act(async () => {
    const d = dialog()!.querySelector('[data-origin-details]') as HTMLDetailsElement
    d.open = true
  })
const rejection = (code: string) => Object.assign(new Error('rejected'), { body: { code, error: 'raw backend text' } })

beforeEach(() => {
  __resetDepRepairParkingForTests()
  planMock.mockReset()
  prepareMock.mockReset()
  envMock.mockReset()
  envMock.mockResolvedValue({ ok: true, project: { open: true } } as never)
  setCurrentProjectId('p1')
  useEnvStore.setState({ originConfirmation: null, dependencyPreparation: null })
  useDepRepairStore.getState().reset()
})
afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
  document.body.innerHTML = ''
  await i18n.changeLanguage('zh-CN')
})

describe('输入框只给带预填的条目', () => {
  it('后端带了 suggested_distribution 的才有输入框，初始值就是预填；别的来源未定的只在详情里解释', async () => {
    await render()
    await open(offer([LAB, PEAK, CV, DEV]))
    expect(Object.keys(useDepRepairStore.getState().originDraft!.values)).toEqual(['lab_tools', 'peak_finder'])
    expect(input('lab_tools')!.value).toBe('lab-tools')
    expect(input('peak_finder')!.value).toBe('peak-finder')
    expect(input('cv2')).toBeNull()
    expect(input('mylib')).toBeNull()
    expect(dialog()!.querySelectorAll('input')).toHaveLength(2)
    // 其它来源未定的 import 在折叠详情里，各一句人话
    const details = dialog()!.querySelector('[data-origin-details]')!
    expect(details.querySelector('[data-origin-entry="cv2"]')?.textContent).toContain('好几个包都能提供它')
    expect(details.querySelector('[data-origin-entry="mylib"]')?.textContent).toContain('正在开发的库')
    expect(dialog()!.querySelector('[data-origin-inputs]')!.textContent).not.toContain('cv2')
  })

  it('一条带预填的都没有（过期的载荷）：不给输入框、不给主按钮，说一句现在没有要确认的', async () => {
    await render()
    await open(offer([CV, DEV]))
    expect(dialog()!.querySelectorAll('input')).toHaveLength(0)
    expect(review()).toBeNull()
    expect(visiblePrimaryButtons(dialog()!)).toBe(0)
    expect(text()).toContain(ot('dialog.nothing'))
  })

  it('预填不被改写：原样进输入框；改了之后草稿跟着变，别的行不受影响', async () => {
    await render()
    await open(offer([LAB, PEAK]))
    await typeInto('lab_tools', 'lab-tools>=1.2')
    expect(useDepRepairStore.getState().originDraft!.values).toEqual({ lab_tools: 'lab-tools>=1.2', peak_finder: 'peak-finder' })
  })

  it('前端不替后端判：同样的字段、换一堆名字，输入框的有无完全一样', async () => {
    await render()
    await open(offer([origin('aaa', { suggested_distribution: 'x1' }), origin('bbb'), origin('ccc', { suggested_distribution: 'x3' })]))
    expect(useDepRepairStore.getState().originDraft!.names).toEqual(['aaa', 'ccc'])
    expect(dialog()!.querySelectorAll('input')).toHaveLength(2)
  })
})

describe('每个状态 × 每个语种：一句话 + 至多一个主按钮', () => {
  for (const lng of LOCALES) {
    it(`${lng} · 包名这一步：固定标题、那一句、每个预填一行（名字 + 输入框）、「详情」、稍后、核对包名`, async () => {
      await i18n.changeLanguage(lng)
      await render()
      await open(offer([LAB, PEAK, CV, DEV]))
      const d = dialog()!
      expect(visibleBlocks(d).map((b) => b.text)).toEqual([
        ot('dialog.heading'),
        ot('dialog.names'),
        'lab_tools',
        'peak_finder',
        t('engine.repairDetails', { ns: 'errors' }),
        t('prep.btn.later', { ns: 'workspace' }),
        ot('btn.review'),
      ])
      expect(visibleSentenceCount(d, lng)).toBe(1)
      expect(visiblePrimaryButtons(d)).toBe(1)
      expect(d.getAttribute('data-origin-view') ?? d.querySelector('[data-origin-view]')?.getAttribute('data-origin-view')).toBe('needs_confirm')
    })

    it(`${lng} · 确认页：那一句说清「用户指定、未经可信来源核实」；包的清单是只读输入、徽标与位置在详情里`, async () => {
      await i18n.changeLanguage(lng)
      await render()
      planMock.mockResolvedValue({ plan: bound() })
      await open()
      await act(async () => review()!.click())
      const d = dialog()!
      expect(visibleBlocks(d).map((b) => b.text)).toEqual([
        ot('dialog.heading'),
        ot('dialog.confirmUnverified', { count: 1 }),
        'lab_tools',
        t('engine.repairDetails', { ns: 'errors' }),
        ot('btn.back'),
        t('prep.btn.later', { ns: 'workspace' }),
        ot('btn.confirmInstall'),
      ])
      expect(visibleSentenceCount(d, lng)).toBe(1)
      expect(visiblePrimaryButtons(d)).toBe(1)
      // 徽标只在折叠的详情里
      expect(d.querySelector('[data-origin-details] [data-origin-trust="user_specified_unverified"]')).not.toBeNull()
      expect(visibleBlocks(d).map((b) => b.text)).not.toContain(ot('detail.userSpecifiedBadge'))
    })

    it(`${lng} · 失败：那一句换成一条危险提示（不叠第二句）`, async () => {
      await i18n.changeLanguage(lng)
      await render()
      planMock.mockRejectedValue(rejection('package_requirement_invalid'))
      await open()
      await act(async () => review()!.click())
      const d = dialog()!
      expect(visibleSentenceCount(d, lng)).toBe(1)
      expect(visiblePrimaryButtons(d)).toBe(1)
      expect(d.querySelector('[data-origin-error="package_requirement_invalid"]')?.textContent).toBe(ot('error.package_requirement_invalid'))
      expect(d.querySelector('[data-origin-sentence]')).toBeNull()
    })
  }
})

describe('提交与确认', () => {
  it('提交 = createJointDependencyPlan({ script, user_specified })：一次、一份计划；清空的输入不提交；不带 target', async () => {
    await render()
    planMock.mockResolvedValue({ plan: bound() })
    await open(offer([LAB, PEAK, CV]))
    await typeInto('lab_tools', '  lab-tools>=1.2 ')
    await typeInto('peak_finder', '')
    await act(async () => review()!.click())
    expect(planMock).toHaveBeenCalledTimes(1)
    expect(planMock).toHaveBeenCalledWith({ script: 'figure.py', user_specified: { lab_tools: 'lab-tools>=1.2' } })
    expect(useDepRepairStore.getState().originPlan?.plan_id).toBe('jp-origin')
    expect(prepareMock).not.toHaveBeenCalled()
  })

  it('所有输入都清空：「核对包名」置灰，不发请求', async () => {
    await render()
    await open(offer([LAB]))
    await typeInto('lab_tools', '')
    expect(review()!.disabled).toBe(true)
    await act(async () => review()!.click())
    expect(planMock).not.toHaveBeenCalled()
  })

  it('确认 = 只发 plan_id + 这份计划自己的 impact_digest（用户在确认页看到的），不是 offer 的', async () => {
    await render()
    planMock.mockResolvedValue({ plan: bound() })
    prepareMock.mockResolvedValue({ started: true, plan_id: 'jp-origin', state: 'preparing', log: '', error: null, code: '' })
    await open(offer([LAB], { impact_digest: 'dg-offer' }))
    await act(async () => review()!.click())
    await act(async () => confirmBtn()!.click())
    expect(prepareMock).toHaveBeenCalledTimes(1)
    expect(prepareMock).toHaveBeenCalledWith('jp-origin', 'dg-bound')
    // 乐观进入准备：转圈的主按钮、取消
    expect(dialog()!.querySelector('[data-origin-running]')).not.toBeNull()
    expect(dialog()!.querySelector('[data-origin-cancel]')).not.toBeNull()
  })

  it('确认页：每个包带「用户指定，未经核实」徽标，详情里说装到哪；回去改保留输入', async () => {
    await render()
    planMock.mockResolvedValue({ plan: bound() })
    await open(offer([LAB]))
    await typeInto('lab_tools', 'lab-tools>=1.2')
    await act(async () => review()!.click())
    await openDetails()
    const row = dialog()!.querySelector('[data-origin-disclosed="lab_tools"]')!
    expect(row.textContent).toContain('lab-tools>=1.2')
    expect(row.querySelector('[data-origin-trust="user_specified_unverified"]')?.textContent).toBe(ot('detail.userSpecifiedBadge'))
    expect(dialog()!.querySelector('[data-origin-place]')?.textContent).toBe(`${t('prep.detail.installTo', { ns: 'workspace' })} · ${t('prep.install.managedPlace', { ns: 'workspace' })}`)
    expect((dialog()!.querySelector('[data-origin-confirmed="lab_tools"]') as HTMLInputElement).readOnly).toBe(true)
    await act(async () => (dialog()!.querySelector('[data-origin-back]') as HTMLButtonElement).click())
    expect(useDepRepairStore.getState().originPlan).toBeNull()
    expect(input('lab_tools')!.value).toBe('lab-tools>=1.2')
  })

  it('两个预填、清空其中一个：确认页详情仍列出被清空的那个（取自绑定的计划，不静默消失）', async () => {
    await render()
    const joint = { ...offer([LAB, PEAK]).plan, origins: [PEAK] }
    planMock.mockResolvedValue({ plan: bound({ joint } as never) })
    await open(offer([LAB, PEAK]))
    await typeInto('peak_finder', '')
    await act(async () => review()!.click())
    await openDetails()
    expect(dialog()!.querySelector('[data-origin-entry="peak_finder"]')).not.toBeNull()
  })

  it('改用户项目 venv 时，详情说清「装到你电脑上已有的那套」', async () => {
    await render()
    planMock.mockResolvedValue({ plan: bound({ modifies_user_environment: true, target_kind: 'project_venv' }) })
    await open(offer([LAB]))
    await act(async () => review()!.click())
    await openDetails()
    expect(dialog()!.querySelector('[data-origin-place]')?.textContent).toContain(t('prep.install.toUserLong', { ns: 'workspace' }))
  })

  it('读不到披露（计划里没有 impact.user_specified）：不让继续，说一句，确认置灰、不发 prepare', async () => {
    await render()
    planMock.mockResolvedValue({ plan: bound({}, []) })
    await open(offer([LAB]))
    await act(async () => review()!.click())
    expect(text()).toContain(ot('dialog.noDisclosure'))
    expect(confirmBtn()!.disabled).toBe(true)
    await act(async () => confirmBtn()!.click())
    expect(prepareMock).not.toHaveBeenCalled()
  })

  it('披露的 trust 不是 user_specified_unverified：不当作已标注，不让继续', async () => {
    await render()
    planMock.mockResolvedValue({ plan: bound({}, [{ import_name: 'lab_tools', requirement: 'lab-tools', trust: 'something_else' as never }]) })
    await open(offer([LAB]))
    await act(async () => review()!.click())
    expect(confirmBtn()!.disabled).toBe(true)
    expect(text()).not.toContain('未经可信来源核实')
  })

  it('计划没有影响摘要（不该发生）：不发空串，明确停下', async () => {
    await render()
    planMock.mockResolvedValue({ plan: bound({ impact_digest: '' }) })
    await open(offer([LAB]))
    await act(async () => review()!.click())
    await act(async () => confirmBtn()!.click())
    expect(prepareMock).not.toHaveBeenCalled()
    expect(useDepRepairStore.getState().errorCode).toBe('dependency_impact_required')
  })

  it('一次授权：多个输入框一份计划、一次 prepare，不逐包弹窗', async () => {
    await render()
    planMock.mockResolvedValue({
      plan: bound({}, [
        { import_name: 'lab_tools', requirement: 'lab-tools', trust: 'user_specified_unverified' },
        { import_name: 'peak_finder', requirement: 'peak-finder', trust: 'user_specified_unverified' },
      ]),
    })
    prepareMock.mockResolvedValue({ started: true, plan_id: 'jp-origin', state: 'preparing', log: '', error: null, code: '' })
    await open(offer([LAB, PEAK]))
    await act(async () => review()!.click())
    expect(text()).toContain(ot('dialog.confirmUnverified', { count: 2 }))
    await act(async () => confirmBtn()!.click())
    expect(planMock).toHaveBeenCalledTimes(1)
    expect(prepareMock).toHaveBeenCalledTimes(1)
  })
})

describe('错误码给人话', () => {
  it('package_requirement_invalid：说格式；dependency_install_not_allowed：说这个库不能按这个名字装；输入保留', async () => {
    await render()
    await open(offer([LAB]))
    planMock.mockRejectedValueOnce(rejection('package_requirement_invalid'))
    await act(async () => review()!.click())
    expect(dialog()!.querySelector('[data-origin-error]')?.textContent).toBe('包名格式不对，只写包名，或「包名>=版本」。')
    expect(input('lab_tools')!.value).toBe('lab-tools')
    planMock.mockRejectedValueOnce(rejection('dependency_install_not_allowed'))
    await act(async () => review()!.click())
    expect(dialog()!.querySelector('[data-origin-error]')?.textContent).toBe('这个库现在不能按你填的包名安装，请返回重新核对。')
    // 后端原文、错误码都不进默认可见区
    expect(text()).not.toContain('raw backend text')
    expect(text()).not.toContain('dependency_install_not_allowed')
  })

  it('别的码走修复卡那一套短句（原样不甩代码）', async () => {
    await render()
    await open(offer([LAB]))
    planMock.mockRejectedValueOnce(rejection('dependency_plan_blocked'))
    await act(async () => review()!.click())
    expect(dialog()!.querySelector('[data-origin-error]')?.textContent).not.toMatch(/dependency_plan_blocked/)
  })
})

describe('装后核验没过：一句话 + 每个原因一句人话（在详情里）', () => {
  async function runAndFail(distReasons: string[] | undefined) {
    planMock.mockResolvedValue({ plan: bound() })
    prepareMock.mockResolvedValue({ started: true, plan_id: 'jp-origin', state: 'preparing', log: '', error: null, code: '' })
    await open(offer([LAB]))
    await act(async () => review()!.click())
    await act(async () => confirmBtn()!.click())
    await act(async () =>
      useDepRepairStore.getState().onProgress({
        plan_id: 'jp-origin',
        state: 'failed',
        log: '',
        error: 'lab_tools: distribution_does_not_provide_import',
        code: 'dependency_import_still_failed',
        flow: 'joint',
        ...(distReasons ? { dist_reasons: distReasons as never } : {}),
      }),
    )
  }

  it('带 dist_reasons：那一句说「装上的包和计划不一致」，详情按原因码各一句；不解析 error 文案', async () => {
    await render()
    await runAndFail(['distribution_does_not_provide_import', 'distribution_version_mismatch'])
    const d = dialog()!
    expect(d.querySelector('[data-origin-error]')?.textContent).toBe('装完后核对发现，装上的包和你确认的计划不一致。')
    expect(visibleSentenceCount(d)).toBe(1)
    expect(visiblePrimaryButtons(d)).toBe(1)
    await openDetails()
    const rows = [...d.querySelectorAll('[data-origin-dist-reason]')]
    expect(rows.map((r) => r.getAttribute('data-origin-dist-reason'))).toEqual(['distribution_does_not_provide_import', 'distribution_version_mismatch'])
    expect(rows[0].textContent).toContain('包名可能填错了')
    expect(rows[1].textContent).toContain('版本不符合要求')
    expect(d.textContent).not.toContain('lab_tools: distribution')
  })

  it('不带 dist_reasons（普通的导入失败）：照旧用修复卡那一句短句', async () => {
    await render()
    await runAndFail(undefined)
    expect(dialog()!.querySelector('[data-origin-error]')?.textContent).toBe('装好了但还是用不了，完整输出在详情里。')
    expect(dialog()!.querySelector('[data-origin-dist-reasons]')).toBeNull()
  })

  it('失败之后「核对包名」还在：回到包名这一步重新核对，输入保留', async () => {
    await render()
    await runAndFail(['distribution_not_installed'])
    expect(review()).not.toBeNull()
    expect(input('lab_tools')!.value).toBe('lab-tools')
  })
})

describe('进度与收尾', () => {
  it('装完（done）关框并丢掉草稿与绑定的计划；别的计划的进度不认', async () => {
    await render()
    planMock.mockResolvedValue({ plan: bound() })
    prepareMock.mockResolvedValue({ started: true, plan_id: 'jp-origin', state: 'preparing', log: '', error: null, code: '' })
    await open(offer([LAB]))
    await act(async () => review()!.click())
    await act(async () => confirmBtn()!.click())
    await act(async () =>
      useDepRepairStore.getState().onProgress({ plan_id: 'someone-else', state: 'done', log: '', error: null, code: '', flow: 'joint' }),
    )
    expect(dialog()).not.toBeNull()
    await act(async () =>
      useDepRepairStore.getState().onProgress({ plan_id: 'jp-origin', state: 'installing', log: '', error: null, code: '', flow: 'joint', requirements: ['lab-tools>=1.2'] }),
    )
    expect(text()).toContain('lab-tools')
    await act(async () =>
      useDepRepairStore.getState().onProgress({ plan_id: 'jp-origin', state: 'done', log: '', error: null, code: '', flow: 'joint', committed: true }),
    )
    expect(dialog()).toBeNull()
    expect(useEnvStore.getState().originConfirmation).toBeNull()
    expect(useDepRepairStore.getState().originDraft).toBeNull()
    expect(useDepRepairStore.getState().originPlan).toBeNull()
  })

  it('「稍后」只关框，草稿与载荷都丢掉', async () => {
    await render()
    await open(offer([LAB]))
    await act(async () => (dialog()!.querySelector('[data-origin-later]') as HTMLButtonElement).click())
    expect(useEnvStore.getState().originConfirmation).toBeNull()
    expect(useDepRepairStore.getState().originDraft).toBeNull()
  })

  it('同一时刻只开一份：再开一次不重置用户正在改的输入', async () => {
    await render()
    await open(offer([LAB]))
    await typeInto('lab_tools', 'my-own-name')
    await open(offer([LAB]))
    expect(input('lab_tools')!.value).toBe('my-own-name')
  })
})

describe('切项目：旧的预填与输入不带过去', () => {
  it('换项目清掉草稿、绑定的计划与载荷；新项目的框只有新项目自己的预填', async () => {
    await render()
    await open(offer([LAB]))
    await typeInto('lab_tools', 'typed-in-A')
    setCurrentProjectId('p2')
    await act(async () => {
      useEnvStore.getState().resetProject()
      useDepRepairStore.getState().clear()
    })
    expect(dialog()).toBeNull()
    expect(useDepRepairStore.getState().originDraft).toBeNull()
    expect(useEnvStore.getState().originConfirmation).toBeNull()
    await open(offer([PEAK], { script: 'other.py' }))
    expect(input('peak_finder')!.value).toBe('peak-finder')
    expect(input('lab_tools')).toBeNull()
    expect(document.body.textContent).not.toContain('typed-in-A')
  })

  it('核对包名挂起期间换了项目：晚到的计划不落进新项目的确认页', async () => {
    await render()
    let resolve!: (v: { plan: JointDependencyRepairPlan }) => void
    planMock.mockReturnValue(new Promise((r) => (resolve = r)))
    await open(offer([LAB]))
    const click = act(async () => review()!.click())
    setCurrentProjectId('p2')
    await act(async () => {
      useEnvStore.getState().resetProject()
      useDepRepairStore.getState().clear()
    })
    await act(async () => resolve({ plan: bound() }))
    await click
    expect(useDepRepairStore.getState().originPlan).toBeNull()
    expect(useDepRepairStore.getState().busy).toBe(false)
  })

  it('A 项目的载荷不弹进 B（带了项目 id 的请求）', async () => {
    await render()
    await act(async () => useEnvStore.getState().requestOriginConfirmation(offer([LAB]), 'p0'))
    expect(dialog()).toBeNull()
  })
})

describe('不吓小白', () => {
  for (const lng of LOCALES) {
    it(`${lng} · 默认可见区和展开后的详情都没有「隔离环境 / site-packages / editable / dist-info」这类词`, async () => {
      await i18n.changeLanguage(lng)
      await render()
      planMock.mockResolvedValue({ plan: bound() })
      await open(offer([LAB, CV, DEV, origin('conda_pkg', { reason: 'conda_package_not_pypi' })]))
      await openDetails()
      expect(text()).not.toMatch(/隔离环境|site-packages|editable|dist-info|importlib|virtualenv|wheel/i)
      await act(async () => review()!.click())
      await openDetails()
      expect(text()).not.toMatch(/隔离环境|site-packages|editable|dist-info|importlib|virtualenv|wheel/i)
    })
  }
})
