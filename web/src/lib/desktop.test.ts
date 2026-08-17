/**
 * 桌面适配层的浏览器回退契约：非 Tauri 环境下每个能力都必须安全降级，
 * 且不触碰任何 @tauri-apps 动态 import（那些包在浏览器里根本不存在）。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  bootstrapDesktopSession,
  isDesktop,
  onDesktopMenu,
  pickDirectory,
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
  it('无 fragment：skipped，且完全不发请求', async () => {
    const fetchSpy = vi.fn()
    vi.stubGlobal('fetch', fetchSpy)
    expect(await bootstrapDesktopSession()).toBe('skipped')
    expect(fetchSpy).not.toHaveBeenCalled()
  })

  it('带 nonce fragment：先清 fragment 再 POST，成功返回 ok', async () => {
    history.replaceState(null, '', '/#dnonce=abc-123_XY')
    const fetchSpy = vi.fn().mockResolvedValue({ ok: true })
    vi.stubGlobal('fetch', fetchSpy)

    expect(await bootstrapDesktopSession()).toBe('ok')
    expect(window.location.hash).toBe('')
    expect(fetchSpy).toHaveBeenCalledWith(
      '/api/desktop/bootstrap',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ nonce: 'abc-123_XY' }),
      }),
    )
  })

  it('后端拒绝（重放/伪造）→ failed', async () => {
    history.replaceState(null, '', '/#dnonce=stale')
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false }))
    expect(await bootstrapDesktopSession()).toBe('failed')
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
