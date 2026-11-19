/**
 * 设置 › 关于与更新里的授权行（ADR 0193 §八）：入口没开整块不渲染；未激活 → 输入框 → 激活；失败一句话 + 一颗按钮。
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
    await act(async () => q('[data-license-action="enter"]')!.click())
    const input = q('[data-license-input]') as HTMLInputElement
    expect(input).not.toBeNull()
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
    await act(async () => {
      setter.call(input, 'TVK-AAAA-BBBB-CCCC')
      input.dispatchEvent(new Event('input', { bubbles: true }))
    })
    await act(async () => q('[data-license-action="activate"]')!.click())
    const call = fetchMock.mock.calls.find((c) => String(c[0]).includes('/api/license/activate'))!
    expect(JSON.parse((call[1] as RequestInit).body as string)).toEqual({ code: 'TVK-AAAA-BBBB-CCCC' })
    expect(q('[data-license-state]')?.dataset.licenseState).toBe('active')
    expect(q('[data-license-line]')?.textContent).toContain('m***@gmail.com')
    expect(q('[data-license-action="release"]')).not.toBeNull()
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

  it('device_limit：一句话，输入态按钮保持「激活」，不弹窗', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn((u: string) => (u.includes('/activate') ? json({ ok: false, error: 'device_limit' }, 409) : json(NONE))),
    )
    await render()
    await act(async () => q('[data-license-action="enter"]')!.click())
    const input = q('[data-license-input]') as HTMLInputElement
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
    await act(async () => {
      setter.call(input, 'TVK-AAAA-BBBB-CCCC')
      input.dispatchEvent(new Event('input', { bubbles: true }))
    })
    await act(async () => q('[data-license-action="activate"]')!.click())
    expect(q('[data-license-state]')?.dataset.licenseState).toBe('failed:device_limit')
    expect(q('[data-license-action="activate"]')).not.toBeNull()
    expect(document.querySelector('[role="dialog"]')).toBeNull()
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
