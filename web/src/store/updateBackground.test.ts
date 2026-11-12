/**
 * 桌面「后台自动下载」的状态流（ADR 0121 修订 2026-11-04）。
 *
 * 主语：认 store 的结局字段——`desktopReady`（下好了）、`desktopError`（有没有吭声）、`desktopPhase`、
 * 以及 desktop 模块里哪个函数被调了几次；不认界面文案。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/desktop', () => ({
  isDesktop: () => true,
  checkDesktopUpdate: vi.fn(),
  downloadDesktopUpdate: vi.fn(),
  installDesktopUpdate: vi.fn(),
  relaunchDesktop: vi.fn(),
  runHostIntegration: vi.fn().mockResolvedValue('{}'),
}))

import {
  checkDesktopUpdate,
  downloadDesktopUpdate,
  installDesktopUpdate,
  relaunchDesktop,
  runHostIntegration,
} from '@/lib/desktop'
import { resetHostSyncForTest } from '@/lib/hostSync'
import { pendingUpdateNotice } from '@/lib/updateNotice'
import { autoDownloadOn, checkUpdateOnStartup, fetchNewerDesktopBuild, useUpdateStore } from './updateStore'

const INFO = { version: '9.9.9', notes: 'n', source: 'mirror' as const }
const DESKTOP_STATUS = {
  desktop: true,
  current: '0.20.0',
  auto_check: false,
  repo_url: 'r',
  releases_url: 'u',
}

/** /api/update/check（桌面停用响应）与 /api/update/settings 的假后端 */
function stubBackend(autoDownload: boolean | null) {
  const calls: Array<{ url: string; init?: RequestInit }> = []
  vi.stubGlobal(
    'fetch',
    vi.fn((url: string, init?: RequestInit) => {
      calls.push({ url, init })
      const body = url.includes('/settings')
        ? { auto_check: false, auto_download: JSON.parse(String(init?.body)).auto_download }
        : { ...DESKTOP_STATUS, ...(autoDownload === null ? {} : { auto_download: autoDownload }) }
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) } as Response)
    }),
  )
  return calls
}

const settle = async () => {
  for (let i = 0; i < 20; i++) await Promise.resolve()
  await new Promise((r) => setTimeout(r, 0))
}

beforeEach(() => {
  useUpdateStore.setState(useUpdateStore.getInitialState(), true)
  vi.unstubAllGlobals()
  vi.mocked(checkDesktopUpdate).mockReset()
  vi.mocked(downloadDesktopUpdate).mockReset()
  vi.mocked(installDesktopUpdate).mockReset()
  vi.mocked(relaunchDesktop).mockReset()
  vi.mocked(runHostIntegration).mockReset()
  vi.mocked(runHostIntegration).mockResolvedValue('{}')
  resetHostSyncForTest()
})

describe('启动', () => {
  it('开着：查到新版 → 不弹询问框、后台下载、成功 → desktopReady；从不自己安装', async () => {
    stubBackend(true)
    vi.mocked(checkDesktopUpdate).mockResolvedValue(INFO)
    vi.mocked(downloadDesktopUpdate).mockResolvedValue({ version: '9.9.9', source: 'github' })
    checkUpdateOnStartup()
    await settle()
    const s = useUpdateStore.getState()
    expect(downloadDesktopUpdate).toHaveBeenCalledOnce()
    expect(installDesktopUpdate).not.toHaveBeenCalled()
    expect(s.desktopReady).toEqual({ version: '9.9.9', source: 'github' })
    expect(s.desktopPhase).toBe('idle')
    expect(s.desktopError).toBeNull()
    // 询问框的判据在整个过程中都是 null
    expect(
      pendingUpdateNotice({ ...s, desktopAutoDownload: autoDownloadOn(s.status) }),
    ).toBeNull()
  })

  it('开着但下载失败：完全静默——没有错误、没有就绪、没有询问框，手动入口（desktopUpdate）还在', async () => {
    stubBackend(true)
    vi.mocked(checkDesktopUpdate).mockResolvedValue(INFO)
    const attempts = [{ source: 'mirror', stage: 'download', kind: 'network' }]
    vi.mocked(downloadDesktopUpdate).mockRejectedValue(Object.assign(new Error('x'), { attempts }))
    checkUpdateOnStartup()
    await settle()
    const s = useUpdateStore.getState()
    expect(s.desktopError).toBeNull()
    expect(s.desktopReady).toBeNull()
    expect(s.desktopPhase).toBe('idle')
    expect(s.desktopUpdate?.version).toBe('9.9.9')
    expect(s.desktopAttempts).toEqual(attempts)
    expect(pendingUpdateNotice({ ...s, desktopAutoDownload: autoDownloadOn(s.status) })).toBeNull()
  })

  it('关着：旧行为——弹询问框、不下载', async () => {
    stubBackend(false)
    vi.mocked(checkDesktopUpdate).mockResolvedValue(INFO)
    checkUpdateOnStartup()
    await settle()
    const s = useUpdateStore.getState()
    expect(downloadDesktopUpdate).not.toHaveBeenCalled()
    expect(s.desktopReady).toBeNull()
    expect(
      pendingUpdateNotice({ ...s, desktopAutoDownload: autoDownloadOn(s.status) })?.kind,
    ).toBe('desktop')
  })

  it('开着但没有新版：不下载', async () => {
    stubBackend(true)
    vi.mocked(checkDesktopUpdate).mockResolvedValue(null)
    checkUpdateOnStartup()
    await settle()
    expect(downloadDesktopUpdate).not.toHaveBeenCalled()
  })

  it('取不到偏好（后端没回这个字段）按关处理，不替用户做没确认过的下载', async () => {
    stubBackend(null)
    vi.mocked(checkDesktopUpdate).mockResolvedValue(INFO)
    checkUpdateOnStartup()
    await settle()
    expect(downloadDesktopUpdate).not.toHaveBeenCalled()
  })

  it('下载中的进度走 desktopProgress；期间设置页「检查更新」被挡在 phase 之外（不会撞 busy）', async () => {
    stubBackend(true)
    vi.mocked(checkDesktopUpdate).mockResolvedValue(INFO)
    let finish!: (v: { version: string; source: 'mirror' }) => void
    vi.mocked(downloadDesktopUpdate).mockImplementation((onProgress) => {
      onProgress?.(0.4)
      return new Promise((r) => (finish = r))
    })
    checkUpdateOnStartup()
    await settle()
    expect(useUpdateStore.getState().desktopPhase).toBe('downloading')
    expect(useUpdateStore.getState().desktopProgress).toBe(0.4)
    vi.mocked(checkDesktopUpdate).mockClear()
    await useUpdateStore.getState().checkDesktop() // 设置页的按钮
    expect(checkDesktopUpdate).not.toHaveBeenCalled()
    finish({ version: '9.9.9', source: 'mirror' })
    await settle()
    expect(useUpdateStore.getState().desktopReady?.version).toBe('9.9.9')
  })
})

describe('现在重启', () => {
  const ready = () =>
    useUpdateStore.setState({
      desktopUpdate: INFO,
      desktopReady: { version: '9.9.9', source: 'mirror' },
      status: { ...DESKTOP_STATUS, auto_download: true },
    })

  it('下好之后离线再查一次失败：仍是就绪、「现在重启」照样能装（Codex #1730）', async () => {
    ready()
    vi.mocked(checkDesktopUpdate).mockRejectedValue(new Error('离线'))
    await useUpdateStore.getState().checkDesktop()
    // 壳与 desktop.ts 都把候选留成已下好的那一版：store 收到的就绪版本照样传下去
    expect(checkDesktopUpdate).toHaveBeenCalledWith('9.9.9')
    const s = useUpdateStore.getState()
    expect(s.desktopUpdate?.version).toBe('9.9.9')
    expect(s.desktopReady?.version).toBe('9.9.9')
    expect(s.desktopError).toBeTruthy()
    vi.mocked(installDesktopUpdate).mockResolvedValue('mirror')
    vi.mocked(relaunchDesktop).mockResolvedValue()
    await useUpdateStore.getState().restartNow()
    expect(installDesktopUpdate).toHaveBeenCalledOnce()
    expect(relaunchDesktop).toHaveBeenCalledOnce()
  })

  it('没有下好的包时查失败：照旧撤掉候选（不留只会回 no_pending_update 的按钮）', async () => {
    useUpdateStore.setState({ desktopUpdate: INFO, status: { ...DESKTOP_STATUS, auto_download: true } })
    vi.mocked(checkDesktopUpdate).mockRejectedValue(new Error('离线'))
    await useUpdateStore.getState().checkDesktop()
    expect(useUpdateStore.getState().desktopUpdate).toBeNull()
  })

  it('装 + 重启各一次；装的是已下好的包（store 不再触发下载）', async () => {
    ready()
    vi.mocked(installDesktopUpdate).mockResolvedValue('mirror')
    vi.mocked(relaunchDesktop).mockResolvedValue()
    await useUpdateStore.getState().restartNow()
    expect(installDesktopUpdate).toHaveBeenCalledOnce()
    expect(downloadDesktopUpdate).not.toHaveBeenCalled()
    expect(relaunchDesktop).toHaveBeenCalledOnce()
    expect(useUpdateStore.getState().desktopReady).toBeNull()
  })

  it('装失败：不重启，不再声称就绪，错误交给设置页', async () => {
    ready()
    vi.mocked(installDesktopUpdate).mockRejectedValue(new Error('下载或安装失败'))
    await useUpdateStore.getState().restartNow()
    expect(relaunchDesktop).not.toHaveBeenCalled()
    const s = useUpdateStore.getState()
    expect(s.desktopReady).toBeNull()
    expect(s.desktopError).toBe('下载或安装失败')
  })

  it('候选与就绪的版本不是同一个：什么都不做（用户看到的版本 == 装上的版本）', async () => {
    ready()
    useUpdateStore.setState({ desktopUpdate: { ...INFO, version: '9.9.10' } })
    await useUpdateStore.getState().restartNow()
    expect(installDesktopUpdate).not.toHaveBeenCalled()
  })
})

describe('重新检查', () => {
  it('查到别的版本 / 没有新版：就绪标记作废（壳也同步丢了旧包）', async () => {
    useUpdateStore.setState({
      desktopUpdate: INFO,
      desktopReady: { version: '9.9.9', source: 'mirror' },
    })
    vi.mocked(checkDesktopUpdate).mockResolvedValueOnce({ ...INFO, version: '9.9.10' })
    await useUpdateStore.getState().checkDesktop()
    expect(useUpdateStore.getState().desktopReady).toBeNull()

    useUpdateStore.setState({ desktopReady: { version: '9.9.9', source: 'mirror' } })
    vi.mocked(checkDesktopUpdate).mockResolvedValueOnce({ ...INFO })
    await useUpdateStore.getState().checkDesktop()
    expect(useUpdateStore.getState().desktopReady?.version).toBe('9.9.9')

    vi.mocked(checkDesktopUpdate).mockResolvedValueOnce(null)
    await useUpdateStore.getState().checkDesktop()
    expect(useUpdateStore.getState().desktopReady).toBeNull()
  })
})

describe('偏好开关', () => {
  it('写到后端用户配置（PATCH /api/update/settings），确认后才翻转；不碰 localStorage', async () => {
    localStorage.clear()
    const calls = stubBackend(true)
    useUpdateStore.setState({ status: { ...DESKTOP_STATUS, auto_download: true } })
    await useUpdateStore.getState().setAutoDownload(false)
    const patch = calls.find((c) => c.url.includes('/api/update/settings'))!
    expect(patch.init?.method).toBe('PATCH')
    expect(JSON.parse(String(patch.init?.body))).toEqual({ auto_download: false })
    expect(autoDownloadOn(useUpdateStore.getState().status)).toBe(false)
    expect(localStorage.length).toBe(0)
  })

  it('保存失败：保持原值并留一句失败', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.reject(new Error('offline'))))
    useUpdateStore.setState({ status: { ...DESKTOP_STATUS, auto_download: true } })
    await useUpdateStore.getState().setAutoDownload(false)
    const s = useUpdateStore.getState()
    expect(autoDownloadOn(s.status)).toBe(true)
    expect(s.autoDownloadFailure).not.toBeNull()
  })
})

describe('启动同步已接入编辑器（ADR 0170）', () => {
  it('后台自动下载开着：发 sync_plugins', async () => {
    stubBackend(true)
    vi.mocked(checkDesktopUpdate).mockResolvedValue(null)
    checkUpdateOnStartup()
    await settle()
    expect(runHostIntegration).toHaveBeenCalledOnce()
    expect(runHostIntegration).toHaveBeenCalledWith('sync_plugins')
  })

  it('关着：只发 sync，绝不带插件更新', async () => {
    stubBackend(false)
    vi.mocked(checkDesktopUpdate).mockResolvedValue(null)
    checkUpdateOnStartup()
    await settle()
    expect(runHostIntegration).toHaveBeenCalledOnce()
    expect(runHostIntegration).toHaveBeenCalledWith('sync')
  })

  it('取不到偏好：按 sync', async () => {
    stubBackend(null)
    vi.mocked(checkDesktopUpdate).mockResolvedValue(null)
    checkUpdateOnStartup()
    await settle()
    expect(runHostIntegration).toHaveBeenCalledWith('sync')
  })

  it('同步失败不影响更新流程', async () => {
    stubBackend(true)
    vi.mocked(runHostIntegration).mockRejectedValue(new Error('x'))
    vi.mocked(checkDesktopUpdate).mockResolvedValue(INFO)
    vi.mocked(downloadDesktopUpdate).mockResolvedValue({ version: '9.9.9', source: 'github' })
    checkUpdateOnStartup()
    await settle()
    expect(useUpdateStore.getState().desktopReady).not.toBeNull()
    expect(useUpdateStore.getState().desktopError).toBeNull()
  })
})

describe('fetchNewerDesktopBuild（「这份排版来自更新的版本」横幅的「检查更新」）', () => {
  it('查到新版 → 顺手下载 → ready；不自己安装', async () => {
    vi.mocked(checkDesktopUpdate).mockResolvedValue(INFO)
    vi.mocked(downloadDesktopUpdate).mockResolvedValue({ version: '9.9.9', source: 'github' })
    expect(await fetchNewerDesktopBuild()).toBe('ready')
    expect(downloadDesktopUpdate).toHaveBeenCalledOnce()
    expect(installDesktopUpdate).not.toHaveBeenCalled()
  })
  it('查到新版但下载没成：found（调用方给「下载并更新」）', async () => {
    vi.mocked(checkDesktopUpdate).mockResolvedValue(INFO)
    vi.mocked(downloadDesktopUpdate).mockRejectedValue(new Error('x'))
    expect(await fetchNewerDesktopBuild()).toBe('found')
  })
  it('已是最新：none，不下载', async () => {
    vi.mocked(checkDesktopUpdate).mockResolvedValue(null)
    expect(await fetchNewerDesktopBuild()).toBe('none')
    expect(downloadDesktopUpdate).not.toHaveBeenCalled()
  })
  it('查询失败：error', async () => {
    vi.mocked(checkDesktopUpdate).mockRejectedValue(new Error('offline'))
    expect(await fetchNewerDesktopBuild()).toBe('error')
  })
})
