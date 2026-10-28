/** 新手首屏两条路 + 命令行 AI 的「一句话一按钮」+ 在别处连好自动关。后端用假 fetch。 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { TooltipProvider } from '@/components/ui/Tooltip'
import { setLocale } from '@/i18n'
import { useAiStore } from '@/store/aiStore'
import { resetAgentInstallStore } from '@/store/agentInstallStore'
import { agentCaps, connection, connectCapsOf, VENDORS } from '../testCaps'
import { ConnectAiDialog } from './ConnectAiDialog'
import { useCapsWatch } from './useCapsWatch'

Element.prototype.scrollIntoView ??= function scrollIntoView() {}
HTMLCanvasElement.prototype.getContext = (() => null) as never
declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

let calls: { url: string; method: string }[]
let serverCaps: ReturnType<typeof connectCapsOf>
let install: Array<Record<string, unknown>>
let host: HTMLDivElement
let root: Root

const withAgent = (state: 'not_installed' | 'broken' | 'needs_auth' | 'disabled', over = {}) =>
  connectCapsOf([], {
    vendors: VENDORS.map((v) => (v.id === 'chatgpt' ? { ...v, available: state !== 'not_installed' && state !== 'broken' && state !== 'disabled' } : v)),
    agents: [agentCaps({ id: 'codex', state, installed: state !== 'not_installed', usable: false, ...over })],
  })

beforeEach(() => {
  resetAgentInstallStore()
  calls = []
  install = []
  serverCaps = withAgent('not_installed')
  globalThis.fetch = (async (url: unknown, init?: RequestInit) => {
    const u = String(url)
    const method = (init?.method ?? 'GET').toUpperCase()
    calls.push({ url: u, method })
    if (u.startsWith('/api/ai/capabilities')) return json(serverCaps)
    if (u === '/api/ai/agents/codex/install') return json(method === 'POST' ? { status: 'running' } : (install.shift() ?? { status: 'running' }))
    if (u === '/api/ai/agents/codex/login') return json(method === 'POST' ? { started: true, mode: 'background' } : { status: 'running' })
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

async function render(node: React.ReactNode) {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => root.render(<TooltipProvider>{node}</TooltipProvider>))
  await act(async () => {})
}
const q = (sel: string) => document.body.querySelector<HTMLElement>(sel)
const click = async (el: Element | null) => {
  await act(async () => (el as HTMLElement).click())
  await act(async () => {})
}

/** 真实应用里 `ConnectAiHost` 在对话框开着时挂的 3 秒刷新 */
function Watch() {
  useCapsWatch(true)
  return null
}

describe('新手首屏', () => {
  it('没有任何连接：只有两行 + 「其他方式」；点「没有订阅」才展开 Key 类（国内在前），Claude 收进其他方式', async () => {
    await render(<ConnectAiDialog open onClose={() => {}} />)
    expect(document.body.querySelectorAll('[data-connect-starter]').length).toBe(2)
    expect(q('[data-connect-vendor]')).toBeNull()
    expect(q('[data-connect-starter="subscription"]')!.textContent).toContain('ChatGPT')
    await click(q('[data-connect-starter-key]'))
    const ids = [...document.body.querySelectorAll<HTMLElement>('[data-connect-vendor]')].map((r) => r.dataset.connectVendor)
    expect(ids.slice(0, 4)).toEqual(['deepseek', 'kimi', 'qwen', 'zhipu'])
    expect(ids).not.toContain('claude')
    await click(q('[data-connect-starter-other]'))
    expect(q('[data-connect-starter]')).toBeNull()
    expect(q('[data-connect-more]')).not.toBeNull()
  })

  it('已有连接的用户维持现有列表', async () => {
    serverCaps = connectCapsOf([connection('deepseek')])
    useAiStore.setState({ caps: serverCaps })
    await render(<ConnectAiDialog open onClose={() => {}} />)
    expect(q('[data-connect-starter]')).toBeNull()
    expect(q('[data-connect-vendor="deepseek"]')).not.toBeNull()
  })

  it('reason=convert：标题与一句话带上脚本名', async () => {
    await render(<ConnectAiDialog open reason="convert" name="plot.py" onClose={() => {}} />)
    expect(document.body.textContent).toContain('把 plot.py 转成可编辑版本需要一个 AI。连上后会自动开始。')
  })
})

describe('命令行 AI 一句话一按钮', () => {
  it('没装：「没装 Codex」+「安装并登录」；点了先 POST install，装好后自动发起登录', async () => {
    await render(<ConnectAiDialog open onClose={() => {}} />)
    expect(q('[data-connect-step-text]')!.textContent).toBe('没装 Codex')
    const btn = q('[data-connect-action="install"]')!
    expect(btn.textContent).toBe('安装并登录')
    serverCaps = withAgent('needs_auth')
    install = [{ status: 'done' }]
    await click(btn)
    expect(calls.some((c) => c.url === '/api/ai/agents/codex/install' && c.method === 'POST')).toBe(true)
    expect(q('[data-connect-step-text]')!.textContent).toBe('正在安装…')
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2100)
    })
    await act(async () => {})
    expect(calls.some((c) => c.url === '/api/ai/agents/codex/login' && c.method === 'POST')).toBe(true)
  })

  it('竞争：3 秒的 caps 刷新先报「已装好」卸掉安装按钮，安装 done 后仍自动发起登录', async () => {
    await render(
      <>
        <Watch />
        <ConnectAiDialog open onClose={() => {}} />
      </>,
    )
    install = [{ status: 'running' }, { status: 'running' }, { status: 'done' }]
    await click(q('[data-connect-action="install"]'))
    // 2s 一轮：仍在装；3s：caps 刷新发现已装好 → 按钮被卸载，行里变回「登录」
    serverCaps = withAgent('needs_auth')
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3100)
    })
    await act(async () => {})
    expect(q('[data-connect-action="install"]')).toBeNull()
    expect(q('[data-connect-action="login"]')).not.toBeNull()
    expect(calls.some((c) => c.url === '/api/ai/agents/codex/login' && c.method === 'POST')).toBe(false)
    // 4s / 6s：安装状态轮询走到 done → 自动登录，不用用户再点
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000)
    })
    await act(async () => {})
    expect(calls.some((c) => c.url === '/api/ai/agents/codex/login' && c.method === 'POST')).toBe(true)
  })

  it('安装进度带 progress 时显示百分比；失败码给一句话', async () => {
    await render(<ConnectAiDialog open onClose={() => {}} />)
    install = [{ status: 'running', progress: 0.42 }, { status: 'error', code: 'managed_cli_offline' }]
    await click(q('[data-connect-action="install"]'))
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2100)
    })
    expect(q('[data-connect-step-text]')!.textContent).toBe('正在安装… 42%')
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2100)
    })
    await act(async () => {})
    expect(q('[role="alert"]')!.textContent).toBe('无法下载 Codex。检查网络后重试；已有的 Codex 不受影响。')
  })

  it('broken：「找到了 Codex，但打不开」+「重新安装」', async () => {
    serverCaps = withAgent('broken')
    useAiStore.setState({ caps: serverCaps })
    await render(<ConnectAiDialog open list="all" onClose={() => {}} />)
    const r = q('[data-connect-vendor="chatgpt"]')!
    expect(r.textContent).toContain('找到了 Codex，但打不开')
    expect(r.querySelector('[data-connect-action="reinstall"]')!.textContent).toBe('重新安装')
  })
})

describe('在别处连好', () => {
  it('轮询到新的 ready 连接：回调一次并关闭', async () => {
    const onClose = vi.fn()
    const onConnected = vi.fn()
    await render(<ConnectAiDialog open onClose={onClose} onConnected={onConnected} />)
    await act(async () => useAiStore.setState({ caps: connectCapsOf([connection('deepseek')]) }))
    await act(async () => useAiStore.setState({ caps: connectCapsOf([connection('deepseek'), connection('kimi')]) }))
    expect(onConnected).toHaveBeenCalledTimes(1)
    expect(onClose).toHaveBeenCalledTimes(1)
  })
})
