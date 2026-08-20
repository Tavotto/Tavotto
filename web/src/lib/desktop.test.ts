/**
 * 桌面适配层的浏览器回退契约：非 Tauri 环境下每个能力都必须安全降级，
 * 且不触碰任何 @tauri-apps 动态 import（那些包在浏览器里根本不存在）。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  __resetDesktopUpdate,
  bootstrapDesktopSession,
  checkDesktopUpdate,
  installDesktopUpdate,
  isDesktop,
  onDesktopMenu,
  pickDirectory,
  relaunchDesktop,
  revealExportedFile,
} from './desktop'

afterEach(() => {
  vi.unstubAllGlobals()
  history.replaceState(null, '', '/')
})

describe('isDesktop', () => {
  it('jsdom（浏览器）里为 false', () => {
    expect(isDesktop()).toBe(false)
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

  it('revealExportedFile 返回 false（调用方保留 <a> 行为）', async () => {
    expect(await revealExportedFile('/tmp', 'a.pdf')).toBe(false)
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
