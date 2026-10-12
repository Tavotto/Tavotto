/**
 * 桌面适配层的浏览器回退契约：非 Tauri 环境下每个能力都必须安全降级，
 * 且不触碰任何 @tauri-apps 动态 import（那些包在浏览器里根本不存在）。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  DesktopUpdateError,
  __resetDesktopUpdate,
  bootstrapDesktopSession,
  canRevealInFileManager,
  checkDesktopUpdate,
  armDesktopCloseGuard,
  fileManagerKind,
  hasDesktopShell,
  installDesktopUpdate,
  isDesktop,
  isRemoteEngineWindow,
  onDesktopMenu,
  pickDirectory,
  nativeFileDropAvailable,
  onNativeFileDrop,
  pickScriptFile,
  relaunchDesktop,
  revealExportedFile,
  revealProjectFolder,
  runCodexIntegration,
} from './desktop'

// 远程实例窗口那一组用：壳在，但本机文件类能力一条都不许碰
const tauri = vi.hoisted(() => ({
  listen: vi.fn(async () => () => {}),
  invoke: vi.fn(async (..._a: unknown[]): Promise<unknown> => undefined),
  open: vi.fn(async () => '/local/path'),
}))
vi.mock('@tauri-apps/api/event', () => ({ listen: tauri.listen }))
// Channel：测试里把壳往它身上发的事件直接塞给 onmessage
const channels = vi.hoisted(() => ({ last: null as null | { onmessage?: (e: unknown) => void } }))
vi.mock('@tauri-apps/api/core', () => ({
  invoke: tauri.invoke,
  Channel: class {
    onmessage?: (e: unknown) => void
    constructor() {
      channels.last = this
    }
  },
}))
vi.mock('@tauri-apps/plugin-dialog', () => ({ open: tauri.open }))

afterEach(() => {
  vi.unstubAllGlobals()
  history.replaceState(null, '', '/')
})

describe('isDesktop', () => {
  it('jsdom（浏览器）里为 false', () => {
    expect(isDesktop()).toBe(false)
    expect(hasDesktopShell()).toBe(false)
    expect(isRemoteEngineWindow()).toBe(false)
  })
})

describe('远程实例窗口（ADR 0105）', () => {
  const w = window as unknown as Record<string, unknown>
  afterEach(() => {
    delete w.__TAURI_INTERNALS__
    delete w.__TAVOTTO_REMOTE_ENGINE__
    vi.clearAllMocks()
  })

  it('本机主窗口：壳在、引擎在本机', () => {
    w.__TAURI_INTERNALS__ = {}
    expect(hasDesktopShell()).toBe(true)
    expect(isRemoteEngineWindow()).toBe(false)
    expect(isDesktop()).toBe(true)
  })

  it('远程窗口：壳在，但不算「本机桌面」', () => {
    w.__TAURI_INTERNALS__ = {}
    w.__TAVOTTO_REMOTE_ENGINE__ = true
    expect(hasDesktopShell()).toBe(true)
    expect(isRemoteEngineWindow()).toBe(true)
    expect(isDesktop()).toBe(false)
  })

  it('只有标记没有壳（普通浏览器里有人设了这个全局）不算远程窗口', () => {
    w.__TAVOTTO_REMOTE_ENGINE__ = true
    expect(isRemoteEngineWindow()).toBe(false)
  })

  it('远程窗口里本机文件类能力全部走浏览器回退，一次 IPC 都不发', async () => {
    w.__TAURI_INTERNALS__ = {}
    w.__TAVOTTO_REMOTE_ENGINE__ = true
    expect(await pickDirectory()).toBeNull()
    expect(await pickScriptFile()).toBeNull()
    expect(await revealExportedFile('/srv/figs/exports', 'a.pdf')).toBe(false)
    expect(canRevealInFileManager()).toBe(false)
    expect(await revealProjectFolder('/srv/figs')).toBe(false)
    expect(await nativeFileDropAvailable()).toBe(false)
    expect(await checkDesktopUpdate()).toBeNull()
    await expect(runCodexIntegration('doctor')).rejects.toMatchObject({ code: 'not_desktop' })
    expect(tauri.open).not.toHaveBeenCalled()
    expect(tauri.invoke).not.toHaveBeenCalled()
  })

  it('远程窗口照样收菜单、照样问关窗', async () => {
    w.__TAURI_INTERNALS__ = {}
    w.__TAVOTTO_REMOTE_ENGINE__ = true
    await onDesktopMenu(() => {})
    expect(tauri.listen).toHaveBeenCalledWith('tavotto:menu', expect.any(Function))
    expect(await armDesktopCloseGuard()).toBe(true)
    expect(tauri.invoke).toHaveBeenCalledWith('arm_close_guard')
  })
})

describe('bootstrapDesktopSession', () => {
  it('无 fragment：ping 通（未启用认证）→ skipped', async () => {
    const fetchSpy = vi.fn().mockResolvedValue({ ok: true, status: 200 })
    vi.stubGlobal('fetch', fetchSpy)
    expect(await bootstrapDesktopSession()).toBe('skipped')
    expect(fetchSpy).toHaveBeenCalledWith('/api/session/ping')
  })

  it('无 fragment 且 ping 401（认证开着但没有会话）→ unauthenticated', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 401 }))
    expect(await bootstrapDesktopSession()).toBe('unauthenticated')
  })

  it('无 fragment、ping 网络异常 → skipped（交给正常错误路径）', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('boom')))
    expect(await bootstrapDesktopSession()).toBe('skipped')
  })

  it('带 nonce fragment：先清 fragment 再 POST，成功返回 ok', async () => {
    history.replaceState(null, '', '/#dnonce=abc-123_XY')
    const fetchSpy = vi.fn().mockResolvedValue({ ok: true, status: 200 })
    vi.stubGlobal('fetch', fetchSpy)

    expect(await bootstrapDesktopSession()).toBe('ok')
    expect(window.location.hash).toBe('')
    expect(fetchSpy).toHaveBeenCalledWith(
      '/api/session/bootstrap',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ nonce: 'abc-123_XY' }),
      }),
    )
  })

  it('后端拒绝且无既有会话 → failed', async () => {
    history.replaceState(null, '', '/#dnonce=stale')
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 403 }))
    expect(await bootstrapDesktopSession()).toBe('failed')
  })

  it('nonce 已用过但同浏览器已持有 cookie（重复点终端链接）→ ok', async () => {
    history.replaceState(null, '', '/#dnonce=used-twice')
    const fetchSpy = vi
      .fn()
      .mockResolvedValueOnce({ ok: false, status: 403 }) // bootstrap 被拒
      .mockResolvedValueOnce({ ok: true, status: 200 }) // ping：cookie 还在
    vi.stubGlobal('fetch', fetchSpy)
    expect(await bootstrapDesktopSession()).toBe('ok')
  })

  it('网络异常 → failed 而不是抛出', async () => {
    history.replaceState(null, '', '/#dnonce=x')
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('boom')))
    expect(await bootstrapDesktopSession()).toBe('failed')
  })
})

describe('浏览器回退', () => {
  it('onDesktopMenu 返回空订阅', async () => {
    const un = await onDesktopMenu(() => {
      throw new Error('浏览器模式不该有菜单事件')
    })
    expect(typeof un).toBe('function')
    un()
  })

  it('pickDirectory 返回 null（调用方回退服务器端目录浏览器）', async () => {
    expect(await pickDirectory()).toBeNull()
  })

  it('系统拖放：浏览器里拿不到路径、订阅是空的', async () => {
    expect(await nativeFileDropAvailable()).toBe(false)
    const off = await onNativeFileDrop(() => {})
    expect(typeof off).toBe('function')
    off()
  })

  it('pickScriptFile 返回 null（调用方回退服务器端目录浏览器）', async () => {
    expect(await pickScriptFile()).toBeNull()
  })

  it('revealExportedFile 返回 false（调用方保留 <a> 行为）', async () => {
    expect(await revealExportedFile('/tmp', 'a.pdf')).toBe(false)
  })

  it('项目文件夹 reveal：入口不摆、调用返回 false', async () => {
    expect(canRevealInFileManager()).toBe(false)
    expect(await revealProjectFolder('/tmp')).toBe(false)
  })
})

describe('fileManagerKind', () => {
  it('按 UA 认三种文件管理器', () => {
    expect(
      fileManagerKind('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15'),
    ).toBe('finder')
    expect(
      fileManagerKind('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Edg/129.0'),
    ).toBe('explorer')
    expect(fileManagerKind('Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/605.1.15')).toBe('files')
  })
})

describe('应用内更新的浏览器回退', () => {
  it('checkDesktopUpdate 返回 null —— 浏览器那条走 Python updater，两条不能同时插手', async () => {
    expect(await checkDesktopUpdate()).toBeNull()
  })

  it('没查过就装：抛错而不是偷偷补一次 check', async () => {
    __resetDesktopUpdate()
    await expect(installDesktopUpdate()).rejects.toThrow('请先检查更新')
  })

  it('relaunchDesktop 退化成刷新页面', async () => {
    const reload = vi.fn()
    vi.stubGlobal('location', { ...window.location, reload })
    await relaunchDesktop()
    expect(reload).toHaveBeenCalled()
  })
})

describe('应用内更新·经壳的自有命令（ADR 0121）', () => {
  const w = window as unknown as Record<string, unknown>
  const found = { version: '9.9.9', notes: 'n', date: '2026-10-12T00:00:00Z', source: 'mirror' as const }
  afterEach(() => {
    delete w.__TAURI_INTERNALS__
    tauri.invoke.mockReset()
    tauri.invoke.mockImplementation(async () => undefined)
    __resetDesktopUpdate()
    channels.last = null
  })

  it('检查：调 desktop_update_check，带上界面语言，原样交出版本与来源', async () => {
    w.__TAURI_INTERNALS__ = {}
    tauri.invoke.mockResolvedValueOnce(found)
    const info = await checkDesktopUpdate()
    expect(tauri.invoke).toHaveBeenCalledWith('desktop_update_check', { uiLang: expect.any(String) })
    expect(info).toMatchObject({ version: '9.9.9', source: 'mirror', date: '2026-10-12T00:00:00Z' })
  })

  it('检查：没有新版 → null，且之后装会被拒（句柄不残留）', async () => {
    w.__TAURI_INTERNALS__ = {}
    tauri.invoke.mockResolvedValueOnce(found)
    await checkDesktopUpdate()
    tauri.invoke.mockResolvedValueOnce(null)
    expect(await checkDesktopUpdate()).toBeNull()
    await expect(installDesktopUpdate()).rejects.toThrow('请先检查更新')
  })

  it('检查失败：抛 DesktopUpdateError，句子是通用的，code/attempts 单独留着，不把壳回的原文当句子', async () => {
    w.__TAURI_INTERNALS__ = {}
    const attempts = [
      { source: 'mirror', stage: 'check', kind: 'timeout' },
      { source: 'github', stage: 'check', kind: 'http' },
    ]
    tauri.invoke.mockRejectedValueOnce({ code: 'all_sources_failed', attempts })
    const err = await checkDesktopUpdate().catch((e) => e)
    expect(err).toBeInstanceOf(DesktopUpdateError)
    expect(err.code).toBe('all_sources_failed')
    expect(err.attempts).toEqual(attempts)
    expect(err.message).toBe('检查更新失败')
    // ACL 拒绝 / 老壳没有这个命令：回的是裸字符串
    tauri.invoke.mockRejectedValueOnce('command desktop_update_check not allowed by ACL')
    const err2 = await checkDesktopUpdate().catch((e) => e)
    expect(err2.message).toBe('检查更新失败')
    expect(err2.message).not.toContain('ACL')
  })

  it('安装：进度随 Started/Progress/Finished 走；换来源重下时 Started 让进度归零；返回实际来源', async () => {
    w.__TAURI_INTERNALS__ = {}
    tauri.invoke.mockResolvedValueOnce(found)
    await checkDesktopUpdate()
    const seen: Array<number | null> = []
    tauri.invoke.mockImplementationOnce(async (cmd: unknown, args: unknown) => {
      expect(cmd).toBe('desktop_update_install')
      expect((args as { onEvent: unknown }).onEvent).toBe(channels.last)
      const emit = channels.last!.onmessage!
      emit({ event: 'Started', data: { contentLength: 100 } })
      emit({ event: 'Progress', data: { chunkLength: 40 } })
      // 镜像下到一半断了，壳换 GitHub 重下
      emit({ event: 'Started', data: { contentLength: 100 } })
      emit({ event: 'Progress', data: { chunkLength: 100 } })
      emit({ event: 'Finished' })
      return 'github'
    })
    const used = await installDesktopUpdate((f) => seen.push(f))
    expect(seen).toEqual([0, 0.4, 0, 1, 1])
    expect(used).toBe('github')
  })

  it('安装：没给 contentLength → 进度是 null（不确定态）', async () => {
    w.__TAURI_INTERNALS__ = {}
    tauri.invoke.mockResolvedValueOnce(found)
    await checkDesktopUpdate()
    const seen: Array<number | null> = []
    tauri.invoke.mockImplementationOnce(async () => {
      channels.last!.onmessage!({ event: 'Started', data: { contentLength: null } })
      channels.last!.onmessage!({ event: 'Progress', data: { chunkLength: 10 } })
      return 'mirror'
    })
    await installDesktopUpdate((f) => seen.push(f))
    expect(seen).toEqual([null, null])
  })

  it('安装失败：逐来源记录带出来，句子是「下载或安装失败」；失败后句柄还在，可以再按一次', async () => {
    w.__TAURI_INTERNALS__ = {}
    tauri.invoke.mockResolvedValueOnce(found)
    await checkDesktopUpdate()
    const attempts = [
      { source: 'mirror', stage: 'download', kind: 'signature' },
      { source: 'github', stage: 'download', kind: 'stalled' },
    ]
    tauri.invoke.mockRejectedValueOnce({ code: 'all_sources_failed', attempts })
    const err = await installDesktopUpdate().catch((e) => e)
    expect(err).toBeInstanceOf(DesktopUpdateError)
    expect(err.attempts).toEqual(attempts)
    expect(err.message).toBe('下载或安装失败')
    tauri.invoke.mockImplementationOnce(async () => 'mirror')
    await expect(installDesktopUpdate()).resolves.toBe('mirror')
  })

  it('壳说 busy / no_pending_update：各有自己的一句话', async () => {
    w.__TAURI_INTERNALS__ = {}
    tauri.invoke.mockResolvedValueOnce(found)
    await checkDesktopUpdate()
    tauri.invoke.mockRejectedValueOnce({ code: 'busy', attempts: [] })
    expect((await installDesktopUpdate().catch((e) => e)).message).toBe(
      '当前正在更新或操作中，请稍候再试',
    )
    tauri.invoke.mockRejectedValueOnce({ code: 'no_pending_update', attempts: [] })
    expect((await installDesktopUpdate().catch((e) => e)).message).toBe(
      '没有待安装的更新，请先检查更新',
    )
  })
})

describe('Codex 集成的浏览器回退', () => {
  it('没有壳时抛稳定 code，而不是悄悄回一个「成功」', async () => {
    await expect(runCodexIntegration('install')).rejects.toMatchObject({
      name: 'CodexShellError',
      code: 'not_desktop',
    })
  })
})
