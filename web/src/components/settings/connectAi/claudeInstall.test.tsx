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
  it('没装：订阅登录卡的行右直接是「安装并登录」，不用先点卡片；点了 POST /agents/claude/install，不先登录', async () => {
    await render()
    const card = q('[data-connect-method="sub"]')!
    expect(card.tagName).not.toBe('BUTTON')
    expect(card.textContent).toContain('安装 Claude Code CLI 以启用')
    expect(card.textContent).not.toContain('没装 Claude Code')
    const btn = card.querySelector('[data-connect-action="install"]')!
    expect(btn.textContent).toContain('安装并登录')
    expect(calls.some((c) => c.url === '/api/ai/agents/claude/login')).toBe(false)
    await click(btn)
    expect(calls.some((c) => c.url === '/api/ai/agents/claude/install' && c.method === 'POST')).toBe(true)
    expect(q('[data-connect-method="sub"]')!.textContent).toContain('正在安装')
  })

  it('安装中：说明行不变，按钮自己是进度条（文字含百分比、aria-valuenow、aria-disabled）', async () => {
    await render()
    install = [{ status: 'running', progress: 0.5 }]
    await click(q('[data-connect-action="install"]'))
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2100)
    })
    await act(async () => {})
    const row = q('[data-connect-method="sub"]')!
    expect(row.textContent).toContain('安装 Claude Code CLI 以启用')
    const bar = row.querySelector('[data-connect-install-progress]')!
    expect(bar.getAttribute('role')).toBe('progressbar')
    expect(bar.getAttribute('aria-valuenow')).toBe('50')
    expect(bar.textContent).toContain('50%')
    expect(row.querySelector('[data-connect-action="install"]')!.getAttribute('aria-disabled')).toBe('true')
  })

  it('装好后自动发起 claude 的登录，登录提示摆在这一行下面', async () => {
    await render()
    install = [{ status: 'running', progress: 0.5 }, { status: 'done' }]
    serverCaps = caps('needs_auth')
    await click(q('[data-connect-action="install"]'))
    await act(async () => {
      await vi.advanceTimersByTimeAsync(4100)
    })
    await act(async () => {})
    expect(calls.some((c) => c.url === '/api/ai/agents/claude/login' && c.method === 'POST')).toBe(true)
    expect(q('[data-connect-login]')).toBeTruthy()
  })

  it('打不开 → 行右「重新安装」；被关了 → 行右「打开」；都不用先点卡片', async () => {
    for (const [state, action] of [
      ['broken', 'reinstall'],
      ['disabled', 'enable'],
    ] as const) {
      serverCaps = connectCapsOf([], { vendors: [CLAUDE], agents: [claudeCaps({ state, installed: true, usable: false })] })
      useAiStore.setState({ caps: serverCaps })
      await render()
      expect(q('[data-connect-method="sub"]')!.querySelector(`[data-connect-action="${action}"]`)).toBeTruthy()
      await act(async () => root.unmount())
      host.remove()
    }
  })

  it('装失败：一句话写的是 Claude Code，不是 Codex', async () => {
    await render()
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
