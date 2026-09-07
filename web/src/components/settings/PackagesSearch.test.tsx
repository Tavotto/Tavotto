/**
 * 设置 → 包管理的「查找」（ADR 0038 的 2026-09-07 修订）。
 *
 * 盯的是**界面合同**（后端判据在 `tests/test_package_lookup.py`）：
 *
 *   ① 第一层过滤是纯前端的，**打字不出网**——这是这一页最要紧的一条性质；
 *   ② 出网只发生在用户点「在 PyPI 查找」那一下，且按 PEP 503 归一后的名字查；
 *   ③ 四种失败各说各的下一步，找不到时还要说清「不做模糊匹配」；
 *   ④ 结果卡上的安装走**既有**的 plan → run，不是第二条安装路径；
 *   ⑤ 选最新版交给 pip 的是裸包名，选了具体版本才钉 `==`；
 *   ⑥ 两次查找重叠时只有最后一次的答案能落地。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  fetchManagedPackages: vi.fn(),
  planPackageJob: vi.fn(),
  runPackageJob: vi.fn(),
  cancelPackageJob: vi.fn(),
  fetchPackageJob: vi.fn(),
  lookupPackage: vi.fn(),
  rebuildManagedEnvironment: vi.fn(),
}))

import {
  ApiError,
  fetchManagedPackages,
  lookupPackage,
  planPackageJob,
  runPackageJob,
  type ManagedPackages,
  type PackageJob,
  type PackageLookup,
} from '@/lib/api'
import { t } from '@/i18n'
import { PackagesSettings } from '@/components/settings/PackagesSettings'
import { searchTerm, usePackageStore } from '@/store/packageStore'
import { useUiStore } from '@/store/uiStore'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const listMock = vi.mocked(fetchManagedPackages)
const lookupMock = vi.mocked(lookupPackage)
const planMock = vi.mocked(planPackageJob)
const runMock = vi.mocked(runPackageJob)

const pk = (key: string, v?: Record<string, unknown>) =>
  t(`settings.packages.${key}`, { ns: 'dialogs', ...(v ?? {}) })
const err = (code: string) => t(`engine.repairError.${code}`, { ns: 'errors' })

const LISTING: ManagedPackages = {
  capability: { available: true, reason: '' },
  environment: {
    exists: true,
    state: 'ready',
    python_version: '3.12.4',
    created_at: 1,
    installed: [],
    in_use: true,
  },
  builtin: [
    { name: 'matplotlib', version: '3.10.0', status: 'installed' },
    { name: 'numpy', version: '2.1.0', status: 'installed' },
    { name: 'scikit-learn', version: '1.5.0', status: 'installed' },
  ],
  builtin_source: 'managed_env',
  user: [
    {
      distribution: 'lmfit',
      requested_specifier: '>=1.3',
      installed_version: '1.3.2',
      recorded_version: '1.3.2',
      reason: 'user_requested',
      status: 'installed',
      protected: false,
      required_by: [],
      installed_at: 1_756_000_000,
    },
    {
      distribution: 'mylab',
      requested_specifier: '',
      installed_version: '0.1',
      recorded_version: '0.1',
      reason: 'user_requested',
      status: 'installed',
      protected: false,
      required_by: [],
      installed_at: 1_756_000_100,
    },
  ],
  busy: false,
  network: { proxy: false, custom_index: false },
  snapshots: 1,
  rollback: 'snapshot_only',
}

const FOUND: PackageLookup = {
  name: 'lmfit',
  versions: ['1.3.4', '1.3.3', '1.3.2'],
  latest: '1.3.4',
  installed: '1.3.2',
  source: 'pypi',
}

const job = (over: Partial<PackageJob> = {}): PackageJob => ({
  job_id: 'job-1',
  op: 'install',
  distribution: 'lmfit',
  requirement: 'lmfit',
  creates_environment: false,
  dependents: [],
  network_required: true,
  expires_at: 9_999_999_999,
  ...over,
})

let host: HTMLDivElement
let root: Root

async function mount(listing: ManagedPackages = LISTING) {
  listMock.mockResolvedValue(listing)
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => {
    root.render(<PackagesSettings />)
  })
  await act(async () => {})
}

const textOf = () => document.body.textContent ?? ''
const buttons = () => [...document.querySelectorAll('button')] as HTMLButtonElement[]
const byName = (name: string) =>
  buttons().find((b) => (b.getAttribute('aria-label') ?? b.textContent ?? '').trim() === name)
const input = () =>
  document.querySelector<HTMLInputElement>(`input[aria-label="${pk('specAria')}"]`)!
const lookupButton = () => document.querySelector<HTMLButtonElement>('[data-packages-lookup]')!
const panel = () => document.querySelector('[data-packages-lookup-panel]')
const rowNames = (table: string) =>
  [...document.querySelectorAll(`table[aria-label="${table}"] tbody tr td:first-child`)].map((td) =>
    (td.textContent ?? '').trim(),
  )

async function type(value: string) {
  await act(async () => {
    const el = input()
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
    setter.call(el, value)
    el.dispatchEvent(new Event('input', { bubbles: true }))
  })
}

const apiError = (code: string, status = 503) =>
  new ApiError('boom', status, { code, error: '后端原文' })

beforeAll(() => {
  // Radix Select 在 jsdom 里要这几个 DOM API；缺了它们弹层根本打不开，
  // 而「打不开」会被误读成「选版本这条路走不通」
  Element.prototype.hasPointerCapture = () => false
  Element.prototype.setPointerCapture = () => {}
  Element.prototype.releasePointerCapture = () => {}
  Element.prototype.scrollIntoView = () => {}
})

beforeEach(() => {
  listMock.mockReset()
  lookupMock.mockReset()
  planMock.mockReset()
  runMock.mockReset()
  usePackageStore.setState({
    data: null,
    loading: false,
    loadError: '',
    progress: null,
    busy: false,
    errorCode: '',
    errorText: '',
    lookup: { query: '', status: 'idle', result: null, code: '', text: '' },
  })
  useUiStore.setState({ confirm: null })
})

afterEach(() => {
  act(() => root.unmount())
  host.remove()
  document.body.innerHTML = ''
})

// ===========================================================================
// 一、第一层：纯前端过滤
// ===========================================================================
describe('本地过滤', () => {
  it('打字即时过滤两份清单，且一个网络请求都不发', async () => {
    await mount()
    expect(rowNames(pk('userTitle'))).toHaveLength(2)

    await type('lmfit')
    expect(rowNames(pk('userTitle')).join(' ')).toContain('lmfit')
    expect(rowNames(pk('userTitle')).join(' ')).not.toContain('mylab')
    // **这一条是这一页最要紧的性质**：在设置页里打字不该悄悄变成对外发送
    expect(lookupMock).not.toHaveBeenCalled()
    expect(listMock).toHaveBeenCalledTimes(1)
  })

  it('按 PEP 503 归一匹配：Scikit_Learn 找得到 scikit-learn', async () => {
    await mount()
    await type('Scikit_Learn')
    // 内置那份折起来时行不在 DOM 上，标题上的匹配数是它此刻唯一的出口
    expect(textOf()).toContain(pk('search.builtinTitleMatch', { count: 1, total: 3 }))
  })

  it('带版本约束时按名字过滤（约束不是搜索词）', async () => {
    await mount()
    await type('lmfit>=1.3')
    expect(rowNames(pk('userTitle')).join(' ')).toContain('lmfit')
    expect(searchTerm('lmfit>=1.3')).toBe('lmfit')
  })

  it('一个都匹配不上时说清是「没有匹配」，不是「一个包都没装」', async () => {
    await mount()
    await type('zzz-nothing')
    expect(textOf()).toContain(pk('search.noMatch', { term: 'zzz-nothing' }))
    expect(textOf()).not.toContain(pk('userEmpty'))
  })

  it('清空输入就恢复整份清单', async () => {
    await mount()
    await type('lmfit')
    await type('')
    expect(rowNames(pk('userTitle'))).toHaveLength(2)
  })

  it('首屏只说「点了会出网」，「打字不出网」是技术详情里的一段', async () => {
    await mount()
    // 首屏那句是短的：这一页首屏只允许一段长文（`settingsDisclosure.test.tsx`
    // 按 30 字数段数），而那一段已经归「装坏了可以重建」
    expect(textOf()).toContain(pk('search.networkNote'))
    expect(textOf()).not.toContain(pk('search.privacyDetail'))

    const tech = buttons().find(
      (b) => b.textContent?.trim() === pk('techTitle') && b.getAttribute('aria-expanded') !== null,
    )!
    await act(async () => tech.click())
    expect(textOf()).toContain(pk('search.privacyDetail'))
  })
})

// ===========================================================================
// 二、第二层：出网只由用户点击触发
// ===========================================================================
describe('远端查找的状态机', () => {
  it('输入中：还没点之前面板不存在，按钮可用', async () => {
    await mount()
    await type('lmfit')
    expect(panel()).toBeNull()
    expect(lookupButton().disabled).toBe(false)
  })

  it('输入为空时查找按钮禁用（不发一个空查询）', async () => {
    await mount()
    expect(lookupButton().disabled).toBe(true)
    await type('   ')
    expect(lookupButton().disabled).toBe(true)
  })

  it('点「查找」不会顺带触发安装——它不是表单的 submit 按钮', async () => {
    await mount()
    await type('lmfit')
    lookupMock.mockResolvedValue(FOUND)
    await act(async () => lookupButton().click())
    expect(lookupMock).toHaveBeenCalledTimes(1)
    // 查找按钮若是 submit，一次点击 = 查找 + 安装：用户点「看看这个包」，
    // 结果东西已经装进去了
    expect(planMock).not.toHaveBeenCalled()
    expect(lookupButton().type).toBe('button')
  })

  it('提交表单仍然是安装：查找没有抢走这个表单的主动作', async () => {
    await mount()
    await type('lmfit')
    planMock.mockResolvedValue({ job: job() })
    runMock.mockResolvedValue({
      started: true,
      job_id: 'job-1',
      state: 'preparing',
      log: '',
      error: null,
      code: '',
    })
    const form = input().closest('form')!
    await act(async () => {
      form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
    })
    expect(planMock).toHaveBeenCalledWith('install', 'lmfit')
    expect(lookupMock).not.toHaveBeenCalled()
  })

  it('查找中：先出一个「正在查找」，回来之后是结果卡', async () => {
    await mount()
    await type('lmfit')
    let release: (v: PackageLookup) => void = () => {}
    lookupMock.mockReturnValue(new Promise<PackageLookup>((r) => (release = r)))
    await act(async () => lookupButton().click())
    expect(panel()!.getAttribute('data-packages-lookup-panel')).toBe('loading')
    expect(textOf()).toContain(pk('search.loading', { name: 'lmfit' }))

    await act(async () => {
      release(FOUND)
    })
    expect(panel()!.getAttribute('data-packages-lookup-panel')).toBe('found')
  })

  it('查的是切掉约束之后的名字', async () => {
    await mount()
    await type('lmfit>=1.3')
    lookupMock.mockResolvedValue(FOUND)
    await act(async () => lookupButton().click())
    // 切约束这件事只有 `searchTerm()` 一处判据：组件把原串原样交给 store，
    // store 切完再问后端。组件里再切一次就是第二个会漂的出处。
    expect(lookupMock).toHaveBeenCalledWith('lmfit')
    expect(usePackageStore.getState().lookup.query).toBe('lmfit')
  })

  it('结果卡说清名字、最新版、答案来自哪种源、以及这个环境里已有的版本', async () => {
    await mount()
    await type('lmfit')
    lookupMock.mockResolvedValue(FOUND)
    await act(async () => lookupButton().click())
    const body = textOf()
    expect(body).toContain('lmfit')
    expect(body).toContain(pk('search.latest', { version: '1.3.4' }))
    expect(body).toContain(pk('search.source.pypi'))
    expect(body).toContain(pk('search.alreadyInstalled', { version: '1.3.2' }))
  })

  it('镜像源与「问不出来」是两句不同的话', async () => {
    await mount()
    await type('lmfit')
    lookupMock.mockResolvedValue({ ...FOUND, source: 'custom_index' })
    await act(async () => lookupButton().click())
    expect(textOf()).toContain(pk('search.source.custom_index'))

    usePackageStore.getState().clearLookup()
    lookupMock.mockResolvedValue({ ...FOUND, source: 'unknown' })
    await act(async () => lookupButton().click())
    expect(textOf()).toContain(pk('search.source.unknown'))
    expect(textOf()).not.toContain(pk('search.source.pypi'))
  })

  it('关掉结果卡就回到什么都不显示', async () => {
    await mount()
    await type('lmfit')
    lookupMock.mockResolvedValue(FOUND)
    await act(async () => lookupButton().click())
    await act(async () => byName(pk('job.dismiss'))!.click())
    expect(panel()).toBeNull()
  })
})

describe('查找失败：四档各说各的下一步', () => {
  const cases = [
    'package_lookup_not_found',
    'package_lookup_offline',
    'package_lookup_timeout',
    'package_lookup_failed',
  ] as const

  it.each(cases)('%s 按 code 给下一步，不只显示退出码', async (code) => {
    await mount()
    await type('lmfit')
    lookupMock.mockRejectedValue(apiError(code))
    await act(async () => lookupButton().click())
    expect(panel()!.getAttribute('data-packages-lookup-panel')).toBe('error')
    expect(textOf()).toContain(err(code))
    expect(textOf()).toContain(pk('search.failed', { name: 'lmfit' }))
  })

  it('只有「没有这个名字」才补一句「不做模糊匹配」', async () => {
    await mount()
    await type('lmfit')
    lookupMock.mockRejectedValue(apiError('package_lookup_not_found'))
    await act(async () => lookupButton().click())
    expect(textOf()).toContain(pk('search.exactOnly'))

    usePackageStore.getState().clearLookup()
    lookupMock.mockRejectedValue(apiError('package_lookup_offline'))
    await act(async () => lookupButton().click())
    expect(textOf()).not.toContain(pk('search.exactOnly'))
  })

  it('没有对应文案时退回后端原文，不是一片空白', async () => {
    await mount()
    await type('lmfit')
    lookupMock.mockRejectedValue(apiError('brand_new_code', 500))
    await act(async () => lookupButton().click())
    expect(textOf()).toContain('后端原文')
  })
})

// ===========================================================================
// 三、从结果卡进入既有的安装流程
// ===========================================================================
describe('结果卡上的安装', () => {
  const openResult = async (lookup: PackageLookup = FOUND) => {
    await mount()
    await type('lmfit')
    lookupMock.mockResolvedValue(lookup)
    await act(async () => lookupButton().click())
  }

  it('默认「最新版」→ 交给 pip 的是裸包名（让它挑有轮子的那一版）', async () => {
    await openResult()
    planMock.mockResolvedValue({ job: job() })
    runMock.mockResolvedValue({ started: true, job_id: 'job-1', state: 'preparing', log: '', error: null, code: '' })
    await act(async () => byName(pk('search.installHere'))!.click())
    expect(planMock).toHaveBeenCalledWith('install', 'lmfit')
    expect(runMock).toHaveBeenCalledWith('job-1')
  })

  it('选了具体版本 → 钉 `名字==版本`', async () => {
    await openResult()
    const trigger = document.querySelector('[role="combobox"]') as HTMLElement
    await act(async () => {
      trigger.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }))
    })
    const option = [...document.querySelectorAll('[role="option"]')].find(
      (o) => (o.textContent ?? '').trim() === '1.3.3',
    ) as HTMLElement
    expect(option, 'Radix 弹层没打开，版本选不了').toBeTruthy()
    await act(async () => {
      option.click()
    })
    planMock.mockResolvedValue({ job: job({ requirement: 'lmfit==1.3.3' }) })
    runMock.mockResolvedValue({ started: true, job_id: 'job-1', state: 'preparing', log: '', error: null, code: '' })
    await act(async () => byName(pk('search.installHere'))!.click())
    expect(planMock).toHaveBeenCalledWith('install', 'lmfit==1.3.3')
  })

  it('装成功之后结果卡收起（清单会自己刷新，卡片留着只会过期）', async () => {
    await openResult()
    planMock.mockResolvedValue({ job: job() })
    runMock.mockResolvedValue({ started: true, job_id: 'job-1', state: 'preparing', log: '', error: null, code: '' })
    await act(async () => byName(pk('search.installHere'))!.click())
    expect(panel()).toBeNull()
  })

  it('plan 就失败时结果卡留着（用户还要看得见自己查到的东西）', async () => {
    await openResult()
    planMock.mockRejectedValue(apiError('package_disk_low'))
    await act(async () => byName(pk('search.installHere'))!.click())
    expect(runMock).not.toHaveBeenCalled()
    expect(panel()!.getAttribute('data-packages-lookup-panel')).toBe('found')
    expect(textOf()).toContain(err('package_disk_low'))
  })

  it('换了一个包，版本选择回到「最新版」', async () => {
    await openResult()
    const trigger = document.querySelector('[role="combobox"]') as HTMLElement
    await act(async () => {
      trigger.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }))
    })
    const option = [...document.querySelectorAll('[role="option"]')].find(
      (o) => (o.textContent ?? '').trim() === '1.3.3',
    ) as HTMLElement
    await act(async () => option.click())

    lookupMock.mockResolvedValue({
      name: 'uncertainties',
      versions: ['3.2.2', '3.2.1'],
      latest: '3.2.2',
      installed: '',
      source: 'pypi',
    })
    await type('uncertainties')
    await act(async () => lookupButton().click())
    planMock.mockResolvedValue({ job: job({ distribution: 'uncertainties' }) })
    runMock.mockResolvedValue({ started: true, job_id: 'job-1', state: 'preparing', log: '', error: null, code: '' })
    await act(async () => byName(pk('search.installHere'))!.click())
    // 上一个包选的 1.3.3 若留着，这里会变成 `uncertainties==1.3.3`
    expect(planMock).toHaveBeenLastCalledWith('install', 'uncertainties')
  })
})

// ===========================================================================
// 四、store：两次查找重叠
// ===========================================================================
describe('packageStore.runLookup', () => {
  beforeEach(() => {
    usePackageStore.setState({
      lookup: { query: '', status: 'idle', result: null, code: '', text: '' },
    })
  })

  it('先发的回来得晚也不许覆盖后发的答案', async () => {
    let releaseSlow: (v: PackageLookup) => void = () => {}
    lookupMock.mockReturnValueOnce(new Promise<PackageLookup>((r) => (releaseSlow = r)))
    lookupMock.mockResolvedValueOnce({ ...FOUND, name: 'uncertainties', latest: '3.2.2' })

    const slow = usePackageStore.getState().runLookup('lmfit')
    const fast = usePackageStore.getState().runLookup('uncertainties')
    await fast
    expect(usePackageStore.getState().lookup.result?.name).toBe('uncertainties')

    releaseSlow(FOUND)
    await slow
    expect(usePackageStore.getState().lookup.result?.name).toBe('uncertainties')
  })

  it('清掉之后，正在飞的那次回来也不许把结果放回去', async () => {
    let release: (v: PackageLookup) => void = () => {}
    lookupMock.mockReturnValue(new Promise<PackageLookup>((r) => (release = r)))
    const pending = usePackageStore.getState().runLookup('lmfit')
    usePackageStore.getState().clearLookup()
    release(FOUND)
    await pending
    expect(usePackageStore.getState().lookup.status).toBe('idle')
  })

  it('空名字不发请求', async () => {
    await usePackageStore.getState().runLookup('   ')
    expect(lookupMock).not.toHaveBeenCalled()
  })
})
