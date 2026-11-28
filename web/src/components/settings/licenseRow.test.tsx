/**
 * 设置 › 关于与更新里的授权行（ADR 0193 §八）：入口没开整块不渲染；未激活 → 激活码胶囊（2026-11-28）→ 激活；失败一句话 + 一颗按钮。
 * 主语：`[data-license-state]` / `[data-license-action]` / `[data-license-input]`，不认文案。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { PremiumLock } from '@/components/license/PremiumLock'
import { LicenseRow } from '@/components/settings/LicenseRow'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { useLicenseStore } from '@/store/licenseStore'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

let root: Root
const NONE = { enabled: true, licensed: false, state: 'none', features: [], exp: null, email_hint: '', needs_refresh: false }
const ACTIVE = { ...NONE, licensed: true, state: 'active', features: ['review-plus'], exp: 1_800_000_000, email_hint: 'm***@gmail.com' }

const json = (body: unknown, status = 200) =>
  Promise.resolve({ ok: status < 400, status, json: () => Promise.resolve(body), headers: new Headers() })

async function render(ui = <LicenseRow />) {
  const host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => root.render(<TooltipProvider>{ui}</TooltipProvider>))
}
const q = (sel: string) => document.querySelector<HTMLElement>(sel)
const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
async function typeCode(raw: string) {
  const input = q('[data-license-input]') as HTMLInputElement
  await act(async () => {
    setter.call(input, raw)
    input.dispatchEvent(new Event('input', { bubbles: true }))
  })
}
async function pressEnter() {
  await act(async () => {
    q('[data-license-input]')!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))
  })
}

beforeEach(() => {
  document.body.innerHTML = ''
  useLicenseStore.setState(useLicenseStore.getInitialState())
})
afterEach(async () => {
  await act(async () => root?.unmount())
  vi.unstubAllGlobals()
  document.body.innerHTML = ''
})

describe('授权行', () => {
  it('入口没开：整块不渲染', async () => {
    vi.stubGlobal('fetch', vi.fn(() => json({ enabled: false })))
    await render()
    expect(q('[data-license-state]')).toBeNull()
  })

  it('未激活 → 输入激活码 → 激活成功变成已激活行', async () => {
    const fetchMock = vi.fn((u: string, _init?: RequestInit) => (u.includes('/activate') ? json(ACTIVE) : json(NONE)))
    vi.stubGlobal('fetch', fetchMock)
    await render()
    expect(q('[data-license-state]')?.dataset.licenseState).toBe('none')
    expect(q('[data-license-input]')).toBeNull()
    await act(async () => q('[data-license-action="enter"]')!.click())
    expect(q('[data-license-input]')).not.toBeNull()
    // 粘贴进来带前缀、短横、小写都认；交给 activate() 的是标准写法
    await typeCode(' tvk-aaaa-bbbb-cccc ')
    expect((q('[data-license-input]') as HTMLInputElement).value).toBe('AAAABBBBCCCC')
    await pressEnter()
    const call = fetchMock.mock.calls.find((c) => String(c[0]).includes('/api/license/activate'))!
    expect(JSON.parse((call[1] as RequestInit).body as string)).toEqual({ code: 'TVK-AAAA-BBBB-CCCC' })
    expect(q('[data-license-state]')?.dataset.licenseState).toBe('active')
    expect(q('[data-license-line]')?.textContent).toContain('m***@gmail.com')
    expect(q('[data-license-action="release"]')).not.toBeNull()
    // 这一次激活成功：胶囊停在「已激活」上（随后收起）
    expect(q('[data-license-capsule]')?.dataset.licenseCapsule).toBe('done')
  })

  it('填满 12 位自动提交一次；没改码不重复提交', async () => {
    vi.useFakeTimers()
    try {
      const fetchMock = vi.fn((u: string) => (u.includes('/activate') ? json({ ok: false, error: 'invalid_code' }, 400) : json(NONE)))
      vi.stubGlobal('fetch', fetchMock)
      await render()
      await act(async () => q('[data-license-action="enter"]')!.click())
      await typeCode('AAAABBBBCCC')
      await act(async () => vi.advanceTimersByTime(1000))
      const activations = () => fetchMock.mock.calls.filter((c) => String(c[0]).includes('/activate')).length
      expect(activations()).toBe(0)
      await typeCode('AAAABBBBCCCD')
      await act(async () => vi.advanceTimersByTime(1000))
      expect(activations()).toBe(1)
      expect(q('[data-license-state]')?.dataset.licenseState).toBe('failed:invalid_code')
      // 码不对：格子染危险色，回到输入态，不自动再提交
      expect(q('[data-license-capsule]')?.dataset.licenseCapsule).toBe('entry')
      expect(q('[data-license-capsule]')?.hasAttribute('data-bad')).toBe(true)
      await act(async () => vi.advanceTimersByTime(2000))
      expect(activations()).toBe(1)
    } finally {
      vi.useRealTimers()
    }
  })

  it('autoEnter（从 Pro 入口进来）：没激活就直接展开格子；已激活不展开', async () => {
    vi.useFakeTimers()
    try {
      vi.stubGlobal('fetch', vi.fn(() => json(NONE)))
      await render(<LicenseRow autoEnter />)
      expect(q('[data-license-input]')).toBeNull()
      await act(async () => vi.advanceTimersByTime(400))
      expect(q('[data-license-input]')).not.toBeNull()
      await act(async () => root.unmount())
      useLicenseStore.setState(useLicenseStore.getInitialState())
      vi.stubGlobal('fetch', vi.fn(() => json(ACTIVE)))
      await render(<LicenseRow autoEnter />)
      await act(async () => vi.advanceTimersByTime(400))
      expect(q('[data-license-capsule]')).toBeNull()
    } finally {
      vi.useRealTimers()
    }
  })

  it('购买：只在系统浏览器打开官网价格页，不调 checkout', async () => {
    const fetchMock = vi.fn((_u: string) => json(NONE))
    const open = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    vi.stubGlobal('open', open)
    await render()
    await act(async () => q('[data-license-action="buy"]')!.click())
    expect(open).toHaveBeenCalledTimes(1)
    expect(String(open.mock.calls[0][0])).toMatch(/\/premium\/$/)
    expect(fetchMock.mock.calls.some((c) => String(c[0]).includes('/checkout'))).toBe(false)
  })

  it('device_limit：一句话 + 一颗「再试一次」（用格子里的码重提），不弹窗', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn((u: string) => (u.includes('/activate') ? json({ ok: false, error: 'device_limit' }, 409) : json(NONE))),
    )
    await render()
    await act(async () => q('[data-license-action="enter"]')!.click())
    await typeCode('TVK-AAAA-BBBB-CCCC')
    await pressEnter()
    expect(q('[data-license-state]')?.dataset.licenseState).toBe('failed:device_limit')
    // 不是码的错：不染色，码留着
    expect(q('[data-license-capsule]')?.hasAttribute('data-bad')).toBe(false)
    expect((q('[data-license-input]') as HTMLInputElement).value).toBe('AAAABBBBCCCC')
    expect(q('[data-license-action="retry"]')).not.toBeNull()
    expect(document.querySelector('[role="dialog"]')).toBeNull()
  })

  it('Codex #2071 P2 过期：续费之外还能「刷新」取回续费后的到期日；吊销：能输入新的激活码', async () => {
    const EXPIRED = { ...NONE, state: 'expired', exp: 1_700_000_000 }
    const fetchMock = vi.fn((u: string) => (u.includes('/refresh') ? json(ACTIVE) : json(EXPIRED)))
    vi.stubGlobal('fetch', fetchMock)
    await render()
    expect(q('[data-license-state]')?.dataset.licenseState).toBe('expired')
    expect(q('[data-license-action="renew"]')).not.toBeNull()
    await act(async () => q('[data-license-action="refresh"]')!.click())
    expect(fetchMock.mock.calls.some((c) => String(c[0]).includes('/api/license/refresh'))).toBe(true)
    expect(q('[data-license-state]')?.dataset.licenseState).toBe('active')
    await act(async () => root.unmount())

    useLicenseStore.setState(useLicenseStore.getInitialState())
    vi.stubGlobal('fetch', vi.fn(() => json({ ...NONE, state: 'revoked' })))
    await render()
    expect(q('[data-license-state]')?.dataset.licenseState).toBe('revoked')
    await act(async () => q('[data-license-action="enter"]')!.click())
    expect(q('[data-license-input]')).not.toBeNull()
  })

  it('Codex #2071 P2 解绑失败（网络）后点「重试」：重做解绑，不拿刷新顶替', async () => {
    let down = true
    const fetchMock = vi.fn((u: string) => {
      if (u.includes('/release')) return down ? Promise.reject(new TypeError('offline')) : json(NONE)
      return json(ACTIVE)
    })
    vi.stubGlobal('fetch', fetchMock)
    await render()
    await act(async () => q('[data-license-action="release"]')!.click())
    expect(q('[data-license-state]')?.dataset.licenseState).toMatch(/^failed:/)
    down = false
    const before = fetchMock.mock.calls.length
    await act(async () => q('[data-license-action="retry"]')!.click())
    const after = fetchMock.mock.calls.slice(before).map((c) => String(c[0]))
    expect(after.some((u) => u.includes('/api/license/release'))).toBe(true)
    expect(after.some((u) => u.includes('/api/license/refresh'))).toBe(false)
    expect(q('[data-license-state]')?.dataset.licenseState).toBe('none')
  })
})

describe('付费功能的灰态', () => {
  it('入口没开或已解锁：原样；未解锁：包一层灰态', async () => {
    useLicenseStore.setState({ status: { enabled: false } })
    await render(<PremiumLock feature="review-plus"><button data-x /></PremiumLock>)
    expect(q('[data-premium-lock]')).toBeNull()
    await act(async () => root.unmount())
    document.body.innerHTML = ''
    useLicenseStore.setState({ status: NONE as never })
    await render(<PremiumLock feature="review-plus"><button data-x /></PremiumLock>)
    expect(q('[data-premium-lock="review-plus"]')?.getAttribute('aria-disabled')).toBe('true')
    await act(async () => root.unmount())
    document.body.innerHTML = ''
    useLicenseStore.setState({ status: ACTIVE as never })
    await render(<PremiumLock feature="review-plus"><button data-x /></PremiumLock>)
    expect(q('[data-premium-lock]')).toBeNull()
  })
})


describe('创始会员铭牌', () => {
  const FOUNDER = { ...ACTIVE, founding: { no: 37, since: 1_763_000_000, name: '' } }
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!

  it('非创始会员：什么都不显示', async () => {
    vi.stubGlobal('fetch', vi.fn(() => json(ACTIVE)))
    await render()
    expect(q('[data-founding-plate]')).toBeNull()
  })

  it('创始会员：席位号 NO. 037 / 200，没刻名字时用默认名；过期了铭牌还在', async () => {
    vi.stubGlobal('fetch', vi.fn(() => json({ ...FOUNDER, state: 'expired', licensed: false })))
    await render()
    expect(q('[data-founding-plate]')).not.toBeNull()
    expect(q('[data-founding-seat]')?.textContent).toContain('NO. 037 / 200')
    expect(q('[data-founding-name]')?.hasAttribute('data-default')).toBe(true)
  })

  it('改名：POST 到本机引擎的 plate-name（不碰 localStorage、不碰别的端点）；清空回到默认名', async () => {
    let name = ''
    const fetchMock = vi.fn((u: string, init?: RequestInit) => {
      if (u.includes('/api/license/plate-name')) name = JSON.parse(String(init?.body)).name
      return json({ ...FOUNDER, founding: { ...FOUNDER.founding, name } })
    })
    vi.stubGlobal('fetch', fetchMock)
    window.localStorage.clear()
    await render()
    const typeAndEnter = async (text: string) => {
      await act(async () => q('[data-founding-name]')!.click())
      const input = q('[data-founding-name-input]') as HTMLInputElement
      await act(async () => {
        setter.call(input, text)
        input.dispatchEvent(new Event('input', { bubbles: true }))
      })
      await act(async () => input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true })))
    }
    await typeAndEnter('  林小雨  ')
    expect(q('[data-founding-shown]')?.textContent).toBe('林小雨')
    const posts = fetchMock.mock.calls.filter((c) => (c[1] as RequestInit | undefined)?.method === 'POST')
    expect(posts.map((c) => new URL(String(c[0]), 'http://x').pathname)).toEqual(['/api/license/plate-name'])
    expect(JSON.parse(String((posts[0][1] as RequestInit).body))).toEqual({ name: '林小雨' })
    expect(window.localStorage.length).toBe(0)
    await typeAndEnter('')
    expect(q('[data-founding-name]')?.hasAttribute('data-default')).toBe(true)
  })
})
