/**
 * Pro 页：四种状态（未激活 / 已激活 / 创始 / 已过期）各自该有什么、不该有什么。
 * 主语：`data-pro-*` 稳定锚点，不认文案；数据由 `/api/license/{status,usage}` 的假响应给。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { TooltipProvider } from '@/components/ui/Tooltip'
import { useLicenseStore } from '@/store/licenseStore'
import { useUiStore } from '@/store/uiStore'
import { ProLinkRow } from '../settings/ProLinkRow'
import { ProDialog } from './ProDialog'
import { ProPage } from './ProPage'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

let root: Root
const EXP = Math.floor(Date.now() / 1000) + 200 * 86400
const BASE = { enabled: true, licensed: false, state: 'none', features: [], exp: null, email_hint: '', needs_refresh: false }
const ACTIVE = {
  ...BASE,
  licensed: true,
  state: 'active',
  features: ['hosted-ai', 'review-plus'],
  exp: EXP,
  email_hint: 'l***@qq.com',
}
const ACCOUNT = { ok: true, tier: { key: 'year', term_days: 365 }, devices: { used: 1, limit: 2 }, paid_at: 1_760_000_000, exp: EXP }
const FOUNDING = { no: 37, since: 1_763_000_000, name: '' }

const json = (body: unknown, status = 200) =>
  Promise.resolve({ ok: status < 400, status, json: () => Promise.resolve(body), headers: new Headers() })

/** `usage`：`{agent_used, review_new_left}` 或 null（取不到）；`account`：账户详情或 null（取不到） */
function mockApi(status: unknown, usage: { agent_used: number | null; review_new_left: number | null } | null, account: unknown | null = ACCOUNT) {
  vi.stubGlobal(
    'fetch',
    vi.fn((u: string) => {
      const path = new URL(u, 'http://x').pathname
      if (path.endsWith('/license/usage')) return usage ? json({ agent_used: usage.agent_used }) : json({ error: 'x' }, 404)
      if (path.endsWith('/review/quota')) return usage ? json({ new_left: usage.review_new_left, update_left: null }) : json({ error: 'x' }, 404)
      if (path.endsWith('/license/account')) return account ? json(account) : json({ ok: false })
      return json(status)
    }),
  )
}
async function render(ui = <ProPage />) {
  const host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => root.render(<TooltipProvider>{ui}</TooltipProvider>))
}
const q = (sel: string) => document.querySelector<HTMLElement>(sel)

beforeEach(() => {
  document.body.innerHTML = ''
  useLicenseStore.setState(useLicenseStore.getInitialState())
  useUiStore.setState({ proOpen: false })
})
afterEach(async () => {
  await act(async () => root?.unmount())
  vi.unstubAllGlobals()
  document.body.innerHTML = ''
})

describe('Pro 页', () => {
  it('未激活：没有铭牌与通行证详情，不画用量；只有一句话加「了解 Pro」链接', async () => {
    mockApi(BASE, null)
    await render()
    expect(q('[data-pro-state="none"]')).not.toBeNull()
    expect(q('[data-founding-plate]')).toBeNull()
    expect(q('[data-pro-row]')).toBeNull()
    expect(q('[role="progressbar"]')).toBeNull()
    expect(q('[data-pro-usage]')).toBeNull()
    const open = vi.fn()
    vi.stubGlobal('open', open)
    await act(async () => q('[data-pro-learn] button')!.click())
    expect(String(open.mock.calls[0][0])).toMatch(/\/premium\/$/)
  })

  it('已激活：类型写在通行证那一行的名字上，下面剩余 / 电脑两行；Agent 用量进度条是已用比例；外部审阅一行字', async () => {
    mockApi(ACTIVE, { agent_used: 0.37, review_new_left: 3 })
    await render()
    expect([...document.querySelectorAll('[data-pro-row]')].map((e) => (e as HTMLElement).dataset.proRow)).toEqual([
      'days',
      'devices',
    ])
    expect(q('[data-license-action="renew"]')).not.toBeNull() // 通行证块里有续费入口
    expect(q('[role="progressbar"]')?.getAttribute('aria-valuenow')).toBe('37')
    expect(q('[data-pro-usage-pct]')?.textContent).toContain('37')
    expect(q('[data-pro-usage="review"]')?.textContent).toContain('3')
    expect(q('[data-founding-plate]')).toBeNull()
  })

  it('用到 80% 起多一行灰字；取不到用量时写「暂时看不到」而不是空进度条', async () => {
    mockApi(ACTIVE, { agent_used: 0.86, review_new_left: 0 })
    await render()
    expect(q('[data-pro-usage="agent"] [data-pro-usage-note]')).not.toBeNull()
    await act(async () => root.unmount())
    document.body.innerHTML = ''
    useLicenseStore.setState(useLicenseStore.getInitialState())
    mockApi(ACTIVE, { agent_used: null, review_new_left: null })
    await render()
    expect(q('[role="progressbar"]')).toBeNull()
    expect(q('[data-pro-usage="agent"] [data-pro-usage-note]')).not.toBeNull()
  })

  it('创始会员：页顶一枚铭牌（只有一枚，授权那一行里不再重复）', async () => {
    mockApi({ ...ACTIVE, founding: FOUNDING }, { agent_used: 0.1, review_new_left: 1 })
    await render()
    expect(document.querySelectorAll('[data-founding-plate]').length).toBe(1)
  })

  it('已过期：铭牌还在；有类型与电脑、没有剩余天数；用量写「已到期」', async () => {
    mockApi({ ...ACTIVE, licensed: false, state: 'expired', founding: FOUNDING }, null)
    await render()
    expect(q('[data-founding-plate]')).not.toBeNull()
    expect([...document.querySelectorAll('[data-pro-row]')].map((e) => (e as HTMLElement).dataset.proRow)).toEqual([
      'devices',
    ])
    expect(q('[data-license-action="renew"]')).not.toBeNull()
    expect(q('[role="progressbar"]')).toBeNull()
    expect(document.querySelectorAll('[data-pro-usage-note]').length).toBe(2)
  })

  it('服务端没给类型 / 设备名额（现在就是这样）：电脑行不出现，不臆造', async () => {
    mockApi(ACTIVE, { agent_used: 0.2, review_new_left: 2 }, null)
    await render()
    expect([...document.querySelectorAll('[data-pro-row]')].map((e) => (e as HTMLElement).dataset.proRow)).toEqual(['days'])
  })

  it('打开时账户接口发现已作废、引擎把本机令牌标了 revoked：页面跟着变成未激活的样子（不再显示有效通行证）', async () => {
    let accountCalled = false
    vi.stubGlobal(
      'fetch',
      vi.fn((u: string) => {
        const path = new URL(u, 'http://x').pathname
        if (path.endsWith('/license/account')) {
          accountCalled = true
          return json({ ok: false })
        }
        if (path.endsWith('/license/usage')) return json({ agent_used: 0.2 })
        if (path.endsWith('/review/quota')) return json({ new_left: 1, update_left: null })
        // 账户接口调过之后，本机令牌已被标作废
        return json(accountCalled ? { ...BASE, state: 'revoked' } : ACTIVE)
      }),
    )
    await render()
    expect(q('[data-pro-state="revoked"]')).not.toBeNull()
    expect(q('[data-pro-row]')).toBeNull()
    expect(q('[role="progressbar"]')).toBeNull()
    expect(q('[data-license-action="enter"]')).not.toBeNull() // 重新输入激活码的口子
  })

  it('入口没开：对话框整个不渲染', async () => {
    mockApi({ enabled: false }, null)
    useUiStore.setState({ proOpen: true })
    await render(<ProDialog />)
    expect(document.querySelector('[role="dialog"]')).toBeNull()
  })
})

describe('设置里的 Pro 一行链接', () => {
  it('入口开着：一行名字 + 现状 + 「打开」，点它收设置、开 Pro 页；没开：不渲染', async () => {
    mockApi(ACTIVE, null)
    useUiStore.setState({ settingsOpen: true, proOpen: false })
    await render(<ProLinkRow />)
    expect(q('[data-license-link]')).not.toBeNull()
    expect(q('[data-license-line]')?.textContent).toContain('20')
    await act(async () => q('[data-license-open]')!.click())
    expect(useUiStore.getState().proOpen).toBe(true)
    expect(useUiStore.getState().settingsOpen).toBe(false)
    await act(async () => root.unmount())
    document.body.innerHTML = ''
    useLicenseStore.setState(useLicenseStore.getInitialState())
    mockApi({ enabled: false }, null)
    await render(<ProLinkRow />)
    expect(q('[data-license-link]')).toBeNull()
  })
})
