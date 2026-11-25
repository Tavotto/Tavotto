/**
 * 「Continue with ChatGPT」（Sign in with ChatGPT，ADR 0209）在「连接 AI」里的那一种方式。
 *
 * 判据的主语：**发出去的请求**（POST /api/ai/siwc/login、轮询、DELETE 连接）、**打开的浏览器地址**、
 * **界面上看得到的那一句 / 那张卡**。后端用假的 fetch，不联网、不真登录。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { TooltipProvider } from '@/components/ui/Tooltip'
import { t } from '@/i18n'
import type { AiConnection, AiVendor } from '@/lib/api'
import { useAiStore } from '@/store/aiStore'
import { useUiStore } from '@/store/uiStore'
import { capsOf, connection } from '../testCaps'
import { MethodList } from './MethodList'
import { chatgptVendorOf } from './StarterChoices'
import { connectionForMethod } from './vendors'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const cx = (k: string, v?: Record<string, unknown>) => t(`settings.connectAi.${k}`, { ns: 'dialogs', ...(v ?? {}) })
const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

const OPENAI: AiVendor = {
  id: 'openai',
  label: 'ChatGPT',
  logo: 'openai',
  region: 'global',
  connect: 'siwc',
  available: true,
  methods: [
    { id: 'openai:siwc', kind: 'siwc', label_key: 'method.chatgptPlan', connection_id: 'siwc:chatgpt', available: true },
    { id: 'openai:key', kind: 'key', catalog_id: 'openai', available: true },
  ],
}
const AUTH_URL = 'https://auth.openai.com/api/accounts/authorize?x=1'
const SIWC_READY = connection('siwc:chatgpt', {
  vendor: 'openai',
  label: 'ChatGPT',
  logo: 'openai',
  models: ['gpt-a'],
  default_model: 'gpt-a',
  method: 'openai:siwc',
  account_email: 'me@example.com',
})

interface Call {
  url: string
  method: string
}
let calls: Call[]
let flowStates: string[]
let connected: AiConnection[]
let opened: string[]

beforeEach(() => {
  calls = []
  flowStates = ['waiting', 'signed_in']
  connected = []
  opened = []
  vi.spyOn(window, 'open').mockImplementation(((u: string) => {
    opened.push(u)
    return null
  }) as typeof window.open)
  globalThis.fetch = (async (url: unknown, init?: RequestInit) => {
    const u = String(url)
    const method = (init?.method ?? 'GET').toUpperCase()
    calls.push({ url: u, method })
    if (u === '/api/ai/siwc/login') return json({ ok: true, flow_id: 'f1', flow: { flow_id: 'f1', state: 'waiting', authorize_url: AUTH_URL } })
    if (u.startsWith('/api/ai/siwc/flows/f1/cancel')) return json({ flow_id: 'f1', state: 'cancelled', authorize_url: AUTH_URL })
    if (u.startsWith('/api/ai/siwc/flows/f1')) {
      const state = flowStates.length > 1 ? flowStates.shift()! : flowStates[0]
      return json({ flow_id: 'f1', state, authorize_url: AUTH_URL })
    }
    if (u.startsWith('/api/ai/capabilities')) {
      const done = flowStates[0] === 'signed_in'
      return json(capsOf([], { vendors: [OPENAI], connections: done ? [SIWC_READY] : [] }))
    }
    if (method === 'DELETE') return json({ ok: true, revoked: true })
    return json({})
  }) as typeof fetch
  useAiStore.setState({ caps: capsOf([], { vendors: [OPENAI], connections: [] }) })
})

let host: HTMLDivElement
let root: Root
afterEach(async () => {
  await act(async () => root?.unmount())
  host?.remove()
  document.body.innerHTML = ''
  vi.useRealTimers()
  vi.restoreAllMocks()
  useAiStore.setState({ caps: null })
  useUiStore.setState({ settingsOpen: false, settingsSection: null })
})

async function render(connections: AiConnection[] = []) {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () =>
    root.render(
      <TooltipProvider>
        <MethodList vendor={OPENAI} connections={connections} onConnected={(c) => connected.push(c)} />
      </TooltipProvider>,
    ),
  )
}
const q = (sel: string) => host.querySelector<HTMLElement>(sel)
const click = async (el: HTMLElement | null) => {
  expect(el).not.toBeNull()
  await act(async () => el!.click())
}

describe('Continue with ChatGPT', () => {
  it('卡片上的按钮 → 发起登录、开浏览器、轮询到 signed_in 后报连上', async () => {
    vi.useFakeTimers()
    await render()
    const card = q('[data-connect-method="openai:siwc"]')
    expect(card?.textContent).toContain(cx('method.subscription'))
    expect(card?.textContent).toContain(cx('method.hintChatgpt'))
    const btn = card!.querySelector<HTMLElement>('[data-connect-action="siwc"]') // 按钮就在卡片里，不用先点开
    expect(btn?.textContent).toBe('Continue with ChatGPT')
    await click(btn)
    expect(calls.some((c) => c.url === '/api/ai/siwc/login' && c.method === 'POST')).toBe(true)
    expect(opened).toEqual([AUTH_URL])
    expect(q('[data-connect-login-hint]')?.textContent).toContain(cx('siwc.browserHint'))
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000) // waiting
      await vi.advanceTimersByTimeAsync(1000) // signed_in
    })
    expect(connected.map((c) => c.id)).toEqual(['siwc:chatgpt'])
  })

  it('登录时没允许订阅额度：说一句，不算连上', async () => {
    vi.useFakeTimers()
    flowStates = ['plan_disabled']
    await render()
    await click(q('[data-connect-action="siwc"]'))
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000)
    })
    expect(q('[role="alert"]')?.textContent).toBe(cx('siwc.planDisabled'))
    expect(connected).toEqual([])
  })

  it('取消：发 cancel，回到按钮，不报错', async () => {
    await render()
    await click(q('[data-connect-action="siwc"]'))
    await click(q('[data-connect-action="siwc-cancel"]'))
    expect(calls.some((c) => c.url === '/api/ai/siwc/flows/f1/cancel')).toBe(true)
    expect(q('[data-connect-action="siwc"]')).not.toBeNull()
    expect(q('[role="alert"]')).toBeNull()
  })

  it('已连：第二行是账号邮箱；展开只有默认型号与断开（没有换 Key）', async () => {
    await render([SIWC_READY])
    const card = q('[data-connect-method="openai:siwc"]')
    expect(card?.dataset.connectMethodState).toBe('ready')
    expect(card?.textContent).toContain('me@example.com')
    await click(card)
    expect(q('[data-connect-manage="siwc:chatgpt"]')).not.toBeNull()
    expect(q('[data-connect-change-key]')).toBeNull()
    expect(q('[data-connect-disconnect]')).not.toBeNull()
  })

  it('没允许订阅额度的连接：第二行说原因，按钮仍在卡片上可以重新登录', async () => {
    await render([{ ...SIWC_READY, state: 'error', error: 'plan_usage_disabled' }])
    const card = q('[data-connect-method="openai:siwc"]')
    expect(card?.textContent).toContain(cx('err.planUsageDisabled'))
    expect(card?.querySelector('[data-connect-action="siwc"]')).not.toBeNull()
  })

  it('API Key 那张卡不会把 ChatGPT 登录的连接认成自己的', () => {
    const key = OPENAI.methods!.find((m) => m.kind === 'key')!
    expect(connectionForMethod([SIWC_READY], OPENAI, key)).toBeUndefined()
    const siwc = OPENAI.methods!.find((m) => m.kind === 'siwc')!
    expect(connectionForMethod([SIWC_READY], OPENAI, siwc)?.id).toBe('siwc:chatgpt')
  })

  it('新手首屏的「有 ChatGPT 订阅」优先找 Continue with ChatGPT 那一家', () => {
    const codexOnly: AiVendor = { ...OPENAI, id: 'old', methods: [{ id: 'old:login', kind: 'login', agent: 'codex', connection_id: 'sub:codex', available: true }] }
    expect(chatgptVendorOf([codexOnly, OPENAI])?.id).toBe('openai')
    expect(chatgptVendorOf([codexOnly])?.id).toBe('old')
  })
})
