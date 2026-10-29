/** Claude Code 没装时也走应用内「安装并登录」（与 Codex 同一套安装流）：装好后自动发起 `claude auth login`。后端用假 fetch。 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { TooltipProvider } from '@/components/ui/Tooltip'
import { setLocale } from '@/i18n'
import type { AiVendor } from '@/lib/api'
import { resetAgentInstallStore } from '@/store/agentInstallStore'
import { useAiStore } from '@/store/aiStore'
import { claudeCaps, connectCapsOf, vendor } from '../testCaps'
import { MethodList } from './MethodList'

HTMLCanvasElement.prototype.getContext = (() => null) as never
declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const json = (body: unknown) => new Response(JSON.stringify(body), { headers: { 'Content-Type': 'application/json' } })

const CLAUDE: AiVendor = vendor('anthropic', {
  label: 'Claude',
  region: 'global',
  connect: 'login',
  methods: [
    { id: 'sub', kind: 'login', label_key: 'method.subscription', agent: 'claude', available: false },
    { id: 'api', kind: 'key', label_key: 'method.apiKey', catalog_id: 'anthropic', available: true },
  ],
})
const caps = (state: 'not_installed' | 'needs_auth') =>
  connectCapsOf([], {
    vendors: [CLAUDE],
    agents: [claudeCaps({ state, installed: state !== 'not_installed', usable: false })],
  })

let calls: { url: string; method: string }[]
let serverCaps: ReturnType<typeof connectCapsOf>
let install: Array<Record<string, unknown>>
let host: HTMLDivElement
let root: Root

beforeEach(() => {
  resetAgentInstallStore()
  calls = []
  install = []
  serverCaps = caps('not_installed')
  globalThis.fetch = (async (url: unknown, init?: RequestInit) => {
    const u = String(url)
    const method = (init?.method ?? 'GET').toUpperCase()
    calls.push({ url: u, method })
    if (u.startsWith('/api/ai/capabilities')) return json(serverCaps)
    if (u === '/api/ai/agents/claude/install') return json(method === 'POST' ? { status: 'running' } : (install.shift() ?? { status: 'running' }))
    if (u === '/api/ai/agents/claude/login') return json(method === 'POST' ? { started: true, mode: 'background' } : { status: 'running' })
    return json({})
  }) as typeof fetch
  useAiStore.setState({ caps: serverCaps, agent: null, connection: null })
  vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval', 'setTimeout', 'clearTimeout'] })
})
afterEach(async () => {
  await act(async () => root?.unmount())
  host?.remove()
  document.body.innerHTML = ''
  vi.useRealTimers()
  await setLocale('zh-CN')
  useAiStore.setState({ caps: null })
})

const q = (sel: string) => document.body.querySelector<HTMLElement>(sel)
const click = async (el: Element | null) => {
  await act(async () => (el as HTMLElement).click())
  await act(async () => {})
}
async function render() {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () =>
    root.render(
      <TooltipProvider>
        <MethodList vendor={CLAUDE} onConnected={() => {}} />
      </TooltipProvider>,
    ),
  )
  await act(async () => {})
}

describe('Claude Code 应用内安装并登录', () => {
  it('没装：订阅登录卡不置灰，点开是「没装 Claude Code」+「安装并登录」；点了 POST /agents/claude/install，不先登录', async () => {
    await render()
    const card = q('[data-connect-method="sub"]') as HTMLButtonElement
    expect(card.disabled).toBe(false)
    await click(card)
    expect(q('[data-connect-step-text]')!.textContent).toBe('没装 Claude Code')
    const btn = q('[data-connect-action="install"]')!
    expect(btn.textContent).toBe('安装并登录')
    expect(calls.some((c) => c.url === '/api/ai/agents/claude/login')).toBe(false)
    await click(btn)
    expect(calls.some((c) => c.url === '/api/ai/agents/claude/install' && c.method === 'POST')).toBe(true)
  })

  it('装好后自动发起 claude 的登录', async () => {
    await render()
    await click(q('[data-connect-method="sub"]'))
    install = [{ status: 'running', progress: 0.5 }, { status: 'done' }]
    serverCaps = caps('needs_auth')
    await click(q('[data-connect-action="install"]'))
    await act(async () => {
      await vi.advanceTimersByTimeAsync(4100)
    })
    await act(async () => {})
    expect(calls.some((c) => c.url === '/api/ai/agents/claude/login' && c.method === 'POST')).toBe(true)
  })

  it('装失败：一句话写的是 Claude Code，不是 Codex', async () => {
    await render()
    await click(q('[data-connect-method="sub"]'))
    install = [{ status: 'error', code: 'managed_cli_offline' }]
    await click(q('[data-connect-action="install"]'))
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2100)
    })
    await act(async () => {})
    expect(q('[role="alert"]')!.textContent).toBe('无法下载 Claude Code。检查网络后重试；已有的 Claude Code 不受影响。')
  })

  it('后端没报这个 agent（老后端）：仍置灰，不画安装按钮', async () => {
    serverCaps = connectCapsOf([], { vendors: [CLAUDE], agents: [] })
    useAiStore.setState({ caps: serverCaps })
    await render()
    expect((q('[data-connect-method="sub"]') as HTMLButtonElement).disabled).toBe(true)
  })
})
