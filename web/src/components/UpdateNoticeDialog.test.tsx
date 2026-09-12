/**
 * 「有新版本」弹窗。
 *
 * 它是启动检查查到新版之后用户看到的**唯一**主动提示（「⋯」上那个圆点没人
 * 看得见），所以两头都要守住：**有新版就弹并给出口**（立即更新 / 稍后），
 * **说过稍后的那一版下次启动一个字不说**。三条通道各有各的主按钮，按下去
 * 必须落到 store 里既有的那个 action 上——这里不许长出第二套升级逻辑。
 * 还要让位：遥测同意 / `tavotto run` 交接确认在问的时候，它不出现。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/desktop', () => ({
  isDesktop: () => false,
  checkDesktopUpdate: vi.fn(),
  installDesktopUpdate: vi.fn(),
  relaunchDesktop: vi.fn(),
}))
vi.mock('@/lib/telemetry', () => ({ captureTelemetry: vi.fn() }))

import { UpdateNoticeDialog } from '@/components/UpdateNoticeDialog'
import { installDesktopUpdate, relaunchDesktop } from '@/lib/desktop'
import { readDismissedUpdate } from '@/lib/updateNotice'
import { useNativeSessionStore } from '@/store/nativeSessionStore'
import { useTelemetryStore } from '@/store/telemetryStore'
import { useUpdateStore } from '@/store/updateStore'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const RELEASE = 'https://example.test/releases/v0.15.0'
const RELEASES = 'https://example.test/releases'

const pipStatus = (extra: Record<string, unknown> = {}) => ({
  current: '0.14.0',
  latest: '0.15.0',
  update_available: true,
  notes: '## 新功能\n- 导出 TIFF',
  can_self_update: true,
  method: 'pip' as const,
  upgrade_command: 'pip install --upgrade tavotto',
  html_url: RELEASE,
  releases_url: RELEASES,
  repo_url: 'https://example.test',
  auto_check: true,
  ...extra,
})

let host: HTMLElement
let root: Root

async function mount() {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => {
    root.render(<UpdateNoticeDialog />)
  })
  await act(async () => {})
}

// Radix 把对话框 portal 到 body 下，所以在 document 上找
const dialog = () => document.querySelector('[data-dialog="update-notice"]')
const buttons = () => [...(dialog()?.querySelectorAll('button') ?? [])] as HTMLButtonElement[]
const buttonNamed = (name: string) => buttons().find((b) => b.textContent?.trim() === name)
const footerLabels = () =>
  buttons()
    .map((b) => b.textContent?.trim())
    .filter((s) => s) // 右上角 × 没有文字

beforeEach(() => {
  localStorage.clear()
  useUpdateStore.setState({
    status: null,
    checking: false,
    applying: false,
    restartRequired: false,
    applyLog: null,
    applyFailed: false,
    dismissedVersion: null,
    checkError: null,
    desktopPhase: 'idle',
    desktopUpdate: null,
    desktopProgress: null,
    desktopError: null,
  })
  useTelemetryStore.setState({ askOpen: false })
  useNativeSessionStore.setState({ pendingQueue: [] })
  vi.mocked(installDesktopUpdate).mockReset()
  vi.mocked(relaunchDesktop).mockReset()
  vi.unstubAllGlobals()
})

afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
})

describe('什么时候弹', () => {
  it('还没查到 / 没有新版：不弹', async () => {
    await mount()
    expect(dialog()).toBeNull()
    await act(async () => useUpdateStore.setState({ status: pipStatus({ update_available: false }) }))
    expect(dialog()).toBeNull()
  })

  it('有新版：标题带版本号，说当前版本、列发行说明、给「稍后」与「立即更新」', async () => {
    useUpdateStore.setState({ status: pipStatus() })
    await mount()
    const text = dialog()?.textContent ?? ''
    expect(text).toContain('0.15.0')
    expect(text).toContain('0.14.0')
    expect(text).toContain('导出 TIFF')
    expect(dialog()?.querySelector('a')?.getAttribute('href')).toBe(RELEASE)
    expect(footerLabels()).toEqual(['稍后', '立即更新'])
  })

  it('这一版说过稍后（上次启动记下的）：不弹；出了更新的版本照弹', async () => {
    useUpdateStore.setState({ status: pipStatus(), dismissedVersion: '0.15.0' })
    await mount()
    expect(dialog()).toBeNull()
    await act(async () => useUpdateStore.setState({ status: pipStatus({ latest: '0.16.0' }) }))
    expect(dialog()?.textContent).toContain('0.16.0')
  })

  it('遥测同意还在问：让位，答完再弹', async () => {
    useUpdateStore.setState({ status: pipStatus() })
    useTelemetryStore.setState({ askOpen: true })
    await mount()
    expect(dialog()).toBeNull()
    await act(async () => useTelemetryStore.setState({ askOpen: false }))
    expect(dialog()).not.toBeNull()
  })

  it('tavotto run 的交接确认在问：让位', async () => {
    useUpdateStore.setState({ status: pipStatus() })
    useNativeSessionStore.setState({
      pendingQueue: [{ native_id: 'sess-1', info: null, loading: true, submitting: false, error: null }],
    })
    await mount()
    expect(dialog()).toBeNull()
  })
})

describe('「稍后」', () => {
  it('按钮：关掉、版本号落盘——下次启动读回来的就是它', async () => {
    useUpdateStore.setState({ status: pipStatus() })
    await mount()
    await act(async () => buttonNamed('稍后')!.click())
    expect(dialog()).toBeNull()
    expect(useUpdateStore.getState().dismissedVersion).toBe('0.15.0')
    expect(readDismissedUpdate()).toBe('0.15.0')
  })

  it('右上角 × 也算稍后：同一版本不再问', async () => {
    useUpdateStore.setState({ status: pipStatus() })
    await mount()
    const close = buttons().find((b) => !b.textContent?.trim())
    await act(async () => close!.click())
    expect(dialog()).toBeNull()
    expect(readDismissedUpdate()).toBe('0.15.0')
  })
})

describe('pip / pipx 通道', () => {
  const stubApply = (body: Record<string, unknown>, status = 200) => {
    const fetchMock = vi.fn((_input: RequestInfo | URL, _init?: RequestInit) =>
      Promise.resolve({ ok: status < 400, status, json: () => Promise.resolve(body) } as Response),
    )
    vi.stubGlobal('fetch', fetchMock)
    return fetchMock
  }

  it('「立即更新」落到 store.apply()；升完改口说重启，只剩「知道了」', async () => {
    const fetchMock = stubApply({ ok: true, command: 'pip', log: 'ok', restart_required: true })
    useUpdateStore.setState({ status: pipStatus() })
    await mount()
    await act(async () => buttonNamed('立即更新')!.click())
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(String(fetchMock.mock.calls[0]![0])).toContain('/api/update/apply')
    expect(useUpdateStore.getState().restartRequired).toBe(true)
    expect(dialog()?.textContent).toContain('重启')
    expect(footerLabels()).toEqual(['知道了'])
    await act(async () => buttonNamed('知道了')!.click())
    expect(dialog()).toBeNull()
    expect(readDismissedUpdate()).toBe('0.15.0')
  })

  it('升级失败：看得出是失败、pip 说的那句话原样在、能再试一次', async () => {
    stubApply({ error: '', log: 'ERROR: No matching distribution found' }, 500)
    useUpdateStore.setState({ status: pipStatus() })
    await mount()
    await act(async () => buttonNamed('立即更新')!.click())
    expect(dialog()?.querySelector('[role="alert"]')).not.toBeNull()
    expect(dialog()?.textContent).toContain('No matching distribution')
    expect(footerLabels()).toEqual(['稍后', '再试一次'])
  })
})

describe('源码检出：不代劳', () => {
  it('没有「立即更新」，把后端给的命令写在框里', async () => {
    useUpdateStore.setState({
      status: pipStatus({ can_self_update: false, method: 'source', upgrade_command: 'git pull' }),
    })
    await mount()
    expect(footerLabels()).toEqual(['稍后'])
    expect(dialog()?.querySelector('code')?.textContent).toBe('git pull')
  })
})

describe('桌面通道', () => {
  it('status 还是 null 也弹得出来（后端 updater 在桌面模式是关着的），说明取桌面那份', async () => {
    useUpdateStore.setState({ desktopUpdate: { version: '0.15.0', notes: '桌面版说明' } })
    await mount()
    expect(dialog()?.textContent).toContain('0.15.0')
    expect(dialog()?.textContent).toContain('桌面版说明')
    expect(footerLabels()).toEqual(['稍后', '立即更新'])
  })

  it('「立即更新」落到 installDesktopUpdate；装完给「立即重启」，按下去 relaunch', async () => {
    vi.mocked(installDesktopUpdate).mockResolvedValue(undefined)
    vi.mocked(relaunchDesktop).mockResolvedValue(undefined)
    useUpdateStore.setState({ desktopUpdate: { version: '0.15.0' } })
    await mount()
    await act(async () => buttonNamed('立即更新')!.click())
    expect(installDesktopUpdate).toHaveBeenCalledTimes(1)
    expect(useUpdateStore.getState().desktopPhase).toBe('installed')
    expect(footerLabels()).toEqual(['稍后重启', '立即重启'])
    await act(async () => buttonNamed('立即重启')!.click())
    expect(relaunchDesktop).toHaveBeenCalledTimes(1)
  })

  it('下载中：真实进度、没有按钮、关不掉；拿不到 Content-Length 就不报百分比', async () => {
    useUpdateStore.setState({
      desktopUpdate: { version: '0.15.0' },
      desktopPhase: 'downloading',
      desktopProgress: 0.42,
    })
    await mount()
    expect(dialog()?.querySelector('[role="progressbar"]')?.getAttribute('aria-valuenow')).toBe('42')
    expect(dialog()?.textContent).toContain('42%')
    expect(footerLabels()).toEqual([])
    expect(dialog()?.getAttribute('aria-busy')).toBe('true')
    await act(async () => useUpdateStore.setState({ desktopProgress: null }))
    expect(dialog()?.textContent).not.toContain('%')
  })

  it('下载失败：更新器说的那句话在，能再试一次', async () => {
    useUpdateStore.setState({
      desktopUpdate: { version: '0.15.0' },
      desktopError: 'signature verification failed',
    })
    await mount()
    expect(dialog()?.textContent).toContain('signature verification failed')
    expect(footerLabels()).toEqual(['稍后', '再试一次'])
  })
})
