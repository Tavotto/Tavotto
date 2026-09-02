/**
 * 设置 → 包管理（ADR 0038）。
 *
 * 盯的是**界面合同**（后端判据在 `tests/test_package_management.py`）：
 *   ① 没项目 / 没能力时给原因、控件禁用；② 内置只读、用户包有升级 / 卸载，
 *   被保护的用户包只读；③ 安装 = plan → run 两步，明显不合形状的规范不发请求；
 *   ④ 卸载先问一句，账上有依赖它的包时把它们列出来，取消就不 run；
 *   ⑤ 作业进行中相关按钮禁用但界面不冻结（进度 + 取消 + 日志可复制）；
 *   ⑥ 错误按 code 给下一步，不只显示退出码；⑦ 没有回滚这句话常驻。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  fetchManagedPackages: vi.fn(),
  planPackageJob: vi.fn(),
  runPackageJob: vi.fn(),
  cancelPackageJob: vi.fn(),
  fetchPackageJob: vi.fn(),
  rebuildManagedEnvironment: vi.fn(),
}))

import {
  ApiError,
  cancelPackageJob,
  fetchManagedPackages,
  planPackageJob,
  runPackageJob,
  type ManagedPackages,
  type PackageJob,
} from '@/lib/api'
import { t } from '@/i18n'
import { PackagesSettings } from '@/components/settings/PackagesSettings'
import { usePackageStore } from '@/store/packageStore'
import { useUiStore } from '@/store/uiStore'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const listMock = vi.mocked(fetchManagedPackages)
const planMock = vi.mocked(planPackageJob)
const runMock = vi.mocked(runPackageJob)
const cancelMock = vi.mocked(cancelPackageJob)

const pk = (key: string, v?: Record<string, unknown>) =>
  t(`settings.packages.${key}`, { ns: 'dialogs', ...(v ?? {}) })

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
    { name: 'pip', version: '25.0', status: 'installed' },
  ],
  builtin_source: 'managed_env',
  user: [
    {
      distribution: 'lmfit',
      requested_specifier: '>=1.3',
      installed_version: '1.3.2',
      recorded_version: '1.3.2',
      reason: 'missing_dependency',
      status: 'installed',
      protected: false,
      required_by: ['mylab'],
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
    {
      distribution: 'numpy',
      requested_specifier: '',
      installed_version: '2.1.0',
      recorded_version: '2.1.0',
      reason: 'user_requested',
      status: 'installed',
      protected: true,
      required_by: [],
      installed_at: 0,
    },
  ],
  busy: false,
  network: { proxy: false, custom_index: false },
  snapshots: 4,
  rollback: 'snapshot_only',
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

const text = () => document.body.textContent ?? ''
const buttons = () => [...document.querySelectorAll('button')] as HTMLButtonElement[]
const byName = (name: string) =>
  buttons().find((b) => (b.getAttribute('aria-label') ?? b.textContent ?? '').trim() === name)
const input = () => document.querySelector<HTMLInputElement>(`input[aria-label="${pk('specAria')}"]`)!
const rows = (table: string) => [...document.querySelectorAll(`table[aria-label="${table}"] tbody tr`)]

async function type(value: string) {
  await act(async () => {
    const el = input()
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
    setter.call(el, value)
    el.dispatchEvent(new Event('input', { bubbles: true }))
  })
}

beforeEach(() => {
  listMock.mockReset()
  planMock.mockReset()
  runMock.mockReset()
  cancelMock.mockReset()
  usePackageStore.setState({
    data: null,
    loading: false,
    loadError: '',
    progress: null,
    busy: false,
    errorCode: '',
    errorText: '',
  })
  useUiStore.setState({ confirm: null })
})

afterEach(() => {
  act(() => root.unmount())
  host.remove()
  document.body.innerHTML = ''
})

describe('能力与禁用原因', () => {
  it('没打开项目：说明原因，输入框与安装禁用，不显示环境行', async () => {
    await mount({
      capability: { available: false, reason: 'no_project' },
      environment: null,
      builtin: [],
      builtin_source: '',
      user: [],
      busy: false,
    })
    expect(text()).toContain(pk('disabled.noProject', { product: 'Tavotto' }))
    expect(input().disabled).toBe(true)
    expect(byName(pk('install'))!.disabled).toBe(true)
    expect(document.querySelector('[data-packages-env]')).toBeNull()
  })

  it('这台机器建不了环境：说清要装 Python，而不是一片空表', async () => {
    await mount({
      ...LISTING,
      capability: { available: false, reason: 'managed_env_unavailable' },
      environment: { ...LISTING.environment!, exists: false, in_use: false },
      builtin_source: 'planned',
      user: [],
    })
    expect(text()).toContain(pk('disabled.noBasePython'))
    expect(input().disabled).toBe(true)
  })

  it('环境正在被别的操作改动（busy）：按钮禁用，清单照常显示', async () => {
    await mount({ ...LISTING, busy: true })
    expect(text()).toContain('lmfit')
    expect(input().disabled).toBe(true)
    expect(byName(pk('uninstallAria', { name: 'lmfit' }))!.disabled).toBe(true)
  })
})

describe('两份清单', () => {
  it('内置只读、用户包有升级与卸载；被保护的用户包只读', async () => {
    await mount()
    expect(text()).toContain(pk('builtinTitle'))
    expect(text()).toContain(pk('userTitle'))
    const builtin = rows(pk('builtinTitle'))
    expect(builtin.map((r) => r.textContent)).toEqual(
      expect.arrayContaining([expect.stringContaining('matplotlib'), expect.stringContaining('numpy')]),
    )
    for (const r of builtin) expect(r.textContent).toContain(pk('readOnly'))
    expect(byName(pk('updateAria', { name: 'lmfit' }))).toBeTruthy()
    expect(byName(pk('uninstallAria', { name: 'lmfit' }))).toBeTruthy()
    // numpy 在账上是用户装的，但在基础栈闭包里：没有卸载按钮，标只读
    expect(byName(pk('uninstallAria', { name: 'numpy' }))).toBeUndefined()
    const numpyRow = rows(pk('userTitle')).find((r) => r.textContent?.includes('numpy'))!
    expect(numpyRow.textContent).toContain(pk('protected'))
  })

  it('用户包说明来源（缺包修复 / 手动）与请求的规范；版本变了时说安装时是多少', async () => {
    await mount({
      ...LISTING,
      user: [
        { ...LISTING.user[0], status: 'changed', installed_version: '1.4.0', recorded_version: '1.3.2' },
        LISTING.user[1],
      ],
    })
    expect(text()).toContain(pk('reason.repair'))
    expect(text()).toContain(pk('reason.user'))
    expect(text()).toContain('lmfit>=1.3')
    expect(text()).toContain(pk('status.changed'))
    expect(text()).toContain(pk('status.changedDetail', { recorded: '1.3.2' }))
  })

  it('环境行：Python 版本、就绪、本项目在用、重建入口', async () => {
    await mount()
    const env = document.querySelector('[data-packages-env]')!
    expect(env.textContent).toContain('3.12.4')
    expect(env.textContent).toContain(pk('env.ready'))
    expect(env.textContent).toContain(pk('env.inUse'))
    expect(byName(pk('env.rebuild'))).toBeTruthy()
  })

  it('没有回滚这句话常驻，并带快照份数', async () => {
    await mount()
    expect(text()).toContain(pk('rollbackNote', { count: 4 }))
  })

  it('清单里没有任何路径', async () => {
    await mount()
    expect(text()).not.toMatch(/\/[A-Za-z]+\/[A-Za-z]+\//)
  })
})

describe('安装', () => {
  it('明显不合形状的规范不发请求，就地说明只接受什么写法', async () => {
    await mount()
    for (const bad of ['-r evil.txt', 'lmfit --index-url x', 'https://evil/x.whl', 'a/b']) {
      await type(bad)
      await act(async () => byName(pk('install'))!.click())
      expect(text()).toContain(pk('specInvalid'))
    }
    expect(planMock).not.toHaveBeenCalled()
  })

  it('合法规范：先形成作业再执行，请求里只有 job_id；进度出现、输入清空', async () => {
    planMock.mockResolvedValue({ job: job() })
    runMock.mockResolvedValue({ started: true, job_id: 'job-1', state: 'preparing', log: '', error: null, code: '' })
    await mount()
    await type('lmfit>=1.3')
    await act(async () => byName(pk('install'))!.click())
    expect(planMock).toHaveBeenCalledWith('install', 'lmfit>=1.3')
    expect(runMock).toHaveBeenCalledWith('job-1')
    expect(input().value).toBe('')
    const panel = document.querySelector('[data-packages-job]')!
    expect(panel.getAttribute('aria-live')).toBe('polite')
    expect(panel.querySelector('[role="progressbar"]')).toBeTruthy()
    expect(byName(pk('job.cancel'))).toBeTruthy()
    // 作业期间相关按钮禁用，但页面还在（清单仍显示）
    expect(input().disabled).toBe(true)
    expect(text()).toContain('matplotlib')
  })

  it('形成作业失败：按 code 给下一步，不是一句退出码', async () => {
    planMock.mockRejectedValue(
      new ApiError('磁盘剩余空间不足', 400, { code: 'package_disk_low', error: '磁盘剩余空间不足' }),
    )
    await mount()
    await type('lmfit')
    await act(async () => byName(pk('install'))!.click())
    expect(runMock).not.toHaveBeenCalled()
    expect(text()).toContain(t('engine.repairError.package_disk_low', { ns: 'errors' }))
  })

  it('进度到终态：日志折叠可复制、成功后重新读清单', async () => {
    planMock.mockResolvedValue({ job: job() })
    runMock.mockResolvedValue({ started: true, job_id: 'job-1', state: 'installing', log: '', error: null, code: '' })
    await mount()
    listMock.mockClear()
    await type('lmfit')
    await act(async () => byName(pk('install'))!.click())
    await act(async () => {
      usePackageStore.getState().onProgress({
        job_id: 'job-1',
        state: 'installing',
        log: 'Collecting lmfit\nDownloading…\n',
        error: null,
        code: '',
        op: 'install',
        distribution: 'lmfit',
      })
    })
    expect(byName(pk('job.log'))).toBeTruthy()
    expect(byName(pk('job.copyLog'))).toBeTruthy()
    await act(async () => {
      usePackageStore.getState().onProgress({
        job_id: 'job-1',
        state: 'done',
        log: 'Collecting lmfit\n',
        error: null,
        code: '',
        op: 'install',
        distribution: 'lmfit',
        result: { distribution: 'lmfit', version: '1.3.2' },
      })
    })
    expect(text()).toContain(pk('job.done', { op: pk('op.install'), name: 'lmfit', version: '1.3.2' }))
    expect(listMock).toHaveBeenCalled()
    expect(input().disabled).toBe(false)
  })

  it('空闲标签页不认领别的项目的作业：进度为空时收到陌生 job_id 也不显示、不刷清单', async () => {
    await mount()
    listMock.mockClear()
    await act(async () => {
      usePackageStore.getState().onProgress({ job_id: 'someone-elses', state: 'installing', log: 'x', error: null, code: '' })
    })
    expect(usePackageStore.getState().progress).toBeNull()
    await act(async () => {
      usePackageStore.getState().onProgress({ job_id: 'someone-elses', state: 'done', log: 'x', error: null, code: '' })
    })
    expect(usePackageStore.getState().progress).toBeNull()
    expect(listMock).not.toHaveBeenCalled()
  })

  it('别的作业的进度事件不会盖掉自己的', async () => {
    planMock.mockResolvedValue({ job: job() })
    runMock.mockResolvedValue({ started: true, job_id: 'job-1', state: 'installing', log: '', error: null, code: '' })
    await mount()
    await type('lmfit')
    await act(async () => byName(pk('install'))!.click())
    await act(async () => {
      usePackageStore.getState().onProgress({ job_id: 'other', state: 'done', log: '', error: null, code: '' })
    })
    expect(usePackageStore.getState().progress?.job_id).toBe('job-1')
  })

  it('取消按钮真的发取消', async () => {
    planMock.mockResolvedValue({ job: job() })
    runMock.mockResolvedValue({ started: true, job_id: 'job-1', state: 'installing', log: '', error: null, code: '' })
    cancelMock.mockResolvedValue({ cancelling: true })
    await mount()
    await type('lmfit')
    await act(async () => byName(pk('install'))!.click())
    await act(async () => byName(pk('job.cancel'))!.click())
    expect(cancelMock).toHaveBeenCalledWith('job-1')
  })
})

describe('卸载', () => {
  it('先问一句；有依赖它的包就把它们列出来；取消则不执行', async () => {
    planMock.mockResolvedValue({ job: job({ op: 'uninstall', dependents: ['mylab'] }) })
    await mount()
    await act(async () => byName(pk('uninstallAria', { name: 'lmfit' }))!.click())
    const confirm = useUiStore.getState().confirm!
    expect(confirm).toBeTruthy()
    expect(confirm.danger).toBe(true)
    expect(JSON.stringify(confirm.body)).toContain('mylab')
    await act(async () => confirm.resolve(false))
    expect(runMock).not.toHaveBeenCalled()
  })

  it('确认之后才执行；没有依赖时用普通那句', async () => {
    planMock.mockResolvedValue({ job: job({ op: 'uninstall', distribution: 'mylab', requirement: 'mylab' }) })
    runMock.mockResolvedValue({ started: true, job_id: 'job-1', state: 'installing', log: '', error: null, code: '' })
    await mount()
    await act(async () => byName(pk('uninstallAria', { name: 'mylab' }))!.click())
    const confirm = useUiStore.getState().confirm!
    expect(JSON.stringify(confirm.body)).toContain('uninstallBody"')
    await act(async () => confirm.resolve(true))
    await act(async () => {})
    expect(planMock).toHaveBeenCalledWith('uninstall', 'mylab')
    expect(runMock).toHaveBeenCalledWith('job-1')
  })

  it('后端拒绝卸内置：按 code 说清为什么', async () => {
    planMock.mockRejectedValue(new ApiError('x', 400, { code: 'package_protected', error: 'x' }))
    await mount()
    await act(async () => byName(pk('uninstallAria', { name: 'lmfit' }))!.click())
    expect(useUiStore.getState().confirm).toBeNull()
    expect(text()).toContain(t('engine.repairError.package_protected', { ns: 'errors' }))
  })
})

describe('升级', () => {
  it('升级 = plan(update, 包名) → run', async () => {
    planMock.mockResolvedValue({ job: job({ op: 'update' }) })
    runMock.mockResolvedValue({ started: true, job_id: 'job-1', state: 'installing', log: '', error: null, code: '' })
    await mount()
    await act(async () => byName(pk('updateAria', { name: 'lmfit' }))!.click())
    expect(planMock).toHaveBeenCalledWith('update', 'lmfit')
    expect(runMock).toHaveBeenCalledWith('job-1')
    expect(useUiStore.getState().confirm).toBeNull() // 升级不问
  })
})
