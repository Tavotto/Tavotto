/**
 * 账号登录（设备码 OAuth，ADR 0146）的界面：
 *   * 对话框四态——等待（验证码 + 重新打开网页 + 取消）/ 过期 / 拒绝 / 失败，终态都是「一句话 + 再试一次」；
 *   * 轮询：按后端给的间隔问、`signed_in` 通知父级并停止、终态停止、卸载停止并取消还在等的流程；
 *   * 入口：只在后端说 `oauth_available` 的厂商出现；已登录行说「已登录」+「退出」。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  fetchAiCapabilities: vi.fn(),
  startAiOauth: vi.fn(),
  fetchAiOauthFlow: vi.fn(),
  cancelAiOauthFlow: vi.fn(),
  logoutAiEndpoint: vi.fn(),
}))

import {
  cancelAiOauthFlow,
  fetchAiCapabilities,
  fetchAiOauthFlow,
  logoutAiEndpoint,
  startAiOauth,
  type AiCapabilities,
  type AiEndpoint,
  type AiOauthFlow,
} from '@/lib/api'
import { SettingsDialog } from '@/components/SettingsDialog'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { t } from '@/i18n'
import { useAiStore } from '@/store/aiStore'
import { useUiStore } from '@/store/uiStore'
import { OAuthLoginDialog } from './OAuthLoginDialog'
import { agentCaps, capsOf, claudeCaps } from './testCaps'

Element.prototype.scrollIntoView ??= function scrollIntoView() {}

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const startMock = vi.mocked(startAiOauth)
const flowMock = vi.mocked(fetchAiOauthFlow)
const cancelMock = vi.mocked(cancelAiOauthFlow)
const logoutMock = vi.mocked(logoutAiEndpoint)
const capsMock = vi.mocked(fetchAiCapabilities)

const ag = (key: string, v?: Record<string, unknown>) =>
  t(`settings.agents.${key}`, { ns: 'dialogs', ...(v ?? {}) })

const flow = (over: Partial<AiOauthFlow> = {}): AiOauthFlow => ({
  flow_id: 'f1',
  provider: 'solovendor',
  region: 'global',
  state: 'waiting',
  user_code: 'CODE-1234',
  verification_uri_complete: 'https://example.invalid/verify?code=CODE-1234',
  expires_at: 4_000_000_000_000,
  interval: 2,
  ...over,
})

let host: HTMLDivElement
let root: Root
let openSpy: ReturnType<typeof vi.spyOn>

const text = () => document.body.textContent ?? ''
const buttons = () => [...document.querySelectorAll('button')] as HTMLButtonElement[]
const byText = (s: string) => buttons().find((b) => (b.getAttribute('aria-label') ?? b.textContent ?? '').includes(s))

async function mountDialog(props: { onClose?: () => void; onSignedIn?: () => void } = {}) {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  const onClose = props.onClose ?? vi.fn()
  const onSignedIn = props.onSignedIn ?? vi.fn()
  await act(async () => {
    root.render(<OAuthLoginDialog provider="solovendor" name="Solo Vendor" onClose={onClose} onSignedIn={onSignedIn} />)
  })
  await act(async () => {})
  return { onClose, onSignedIn }
}

/** 推进一次轮询间隔，并让其中的异步请求落定 */
async function tickPoll(seconds = 2) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(seconds * 1000)
  })
}

beforeEach(() => {
  vi.useFakeTimers()
  startMock.mockReset()
  flowMock.mockReset()
  cancelMock.mockReset().mockResolvedValue(flow({ state: 'cancelled' }))
  logoutMock.mockReset()
  capsMock.mockReset()
  openSpy = vi.spyOn(window, 'open').mockImplementation(() => null)
})

afterEach(() => {
  act(() => root.unmount())
  host.remove()
  document.body.innerHTML = ''
  vi.useRealTimers()
  openSpy.mockRestore()
  useAiStore.setState({ caps: null, agent: null })
  useUiStore.setState({ settingsOpen: false, settingsSection: null })
})

describe('账号登录对话框', () => {
  it('等待态：一句话 + 大号验证码 + 「重新打开网页」+「取消」，并打开授权网址', async () => {
    startMock.mockResolvedValue(flow())
    await mountDialog()
    expect(startMock).toHaveBeenCalledWith('solovendor', undefined)
    expect(text()).toContain(ag('oauth.instruction'))
    expect(document.querySelector('[data-oauth-code]')?.textContent).toBe('CODE-1234')
    expect(openSpy).toHaveBeenCalledWith('https://example.invalid/verify?code=CODE-1234', '_blank', 'noreferrer')
    expect(byText(ag('oauth.reopen'))).toBeTruthy()
    expect(byText(ag('oauth.cancel'))).toBeTruthy()
    // 一句话 + 一个主按钮：没有「再试一次」
    expect(byText(ag('oauth.again'))).toBeUndefined()
    // 点「重新打开网页」再开一次
    await act(async () => byText(ag('oauth.reopen'))!.click())
    expect(openSpy).toHaveBeenCalledTimes(2)
  })

  it('按间隔轮询；signed_in 通知父级并停止轮询', async () => {
    startMock.mockResolvedValue(flow())
    flowMock.mockResolvedValueOnce(flow()).mockResolvedValueOnce(flow({ state: 'signed_in', endpoint_id: 'solovendor-oauth' }))
    const { onSignedIn } = await mountDialog()
    expect(flowMock).not.toHaveBeenCalled()
    await tickPoll()
    expect(flowMock).toHaveBeenCalledTimes(1)
    expect(onSignedIn).not.toHaveBeenCalled()
    await tickPoll()
    expect(flowMock).toHaveBeenCalledTimes(2)
    expect(onSignedIn).toHaveBeenCalledTimes(1)
    await tickPoll(10)
    expect(flowMock).toHaveBeenCalledTimes(2) // 不再问
  })

  it.each(['expired', 'denied', 'failed'] as const)('%s 终态：一句话 + 「再试一次」，轮询停止', async (state) => {
    startMock.mockResolvedValue(flow())
    flowMock.mockResolvedValue(flow({ state }))
    await mountDialog()
    await tickPoll()
    expect(document.querySelector(`[data-oauth-state="${state}"]`)?.textContent).toBe(ag(`oauth.state.${state}`))
    expect(document.querySelector('[data-oauth-code]')).toBeNull()
    expect(byText(ag('oauth.reopen'))).toBeUndefined()
    await tickPoll(10)
    expect(flowMock).toHaveBeenCalledTimes(1)
    // 再试一次：重新发起，回到等待态
    startMock.mockResolvedValue(flow({ flow_id: 'f2', user_code: 'CODE-9999' }))
    flowMock.mockResolvedValue(flow({ flow_id: 'f2', user_code: 'CODE-9999' }))
    await act(async () => byText(ag('oauth.again'))!.click())
    expect(startMock).toHaveBeenCalledTimes(2)
    expect(document.querySelector('[data-oauth-code]')?.textContent).toBe('CODE-9999')
  })

  it('发起失败：显示后端那句话 + 「再试一次」', async () => {
    startMock.mockRejectedValue(new Error('boom'))
    await mountDialog()
    expect(document.querySelector('[data-oauth-state="failed"]')).toBeTruthy()
    expect(byText(ag('oauth.again'))).toBeTruthy()
    expect(flowMock).not.toHaveBeenCalled()
  })

  it('取消 / 卸载：停止轮询，还在等的流程尽力取消', async () => {
    startMock.mockResolvedValue(flow())
    flowMock.mockResolvedValue(flow())
    const { onClose } = await mountDialog()
    await tickPoll()
    expect(flowMock).toHaveBeenCalledTimes(1)
    await act(async () => byText(ag('oauth.cancel'))!.click())
    expect(onClose).toHaveBeenCalled()
    act(() => root.unmount())
    expect(cancelMock).toHaveBeenCalledWith('f1')
    await tickPoll(10)
    expect(flowMock).toHaveBeenCalledTimes(1) // 卸载后不再问
    // afterEach 还会 unmount 一次
    root = createRoot(host)
  })

  it('登录成功后卸载不再取消流程', async () => {
    startMock.mockResolvedValue(flow())
    flowMock.mockResolvedValue(flow({ state: 'signed_in' }))
    await mountDialog()
    await tickPoll()
    act(() => root.unmount())
    expect(cancelMock).not.toHaveBeenCalled()
    root = createRoot(host)
  })
})

describe('登录入口与已登录行', () => {
  const oauthRow = (over: Partial<AiEndpoint> = {}): AiEndpoint => ({
    id: 'solovendor-oauth',
    label: 'Solo Vendor 账号',
    agent: 'claude',
    base_url: 'https://api.example.invalid/coding',
    models: [],
    default_model: null,
    wire_api: 'responses',
    has_key: true,
    key_hint: '',
    incompatible: null,
    migratable: null,
    auth: 'oauth',
    signed_in: true,
    oauth_expires_ms: 4_000_000_000_000,
    oauth_provider: 'solovendor',
    oauth_region: 'global',
    ...over,
  })

  async function openClaudeDetail(caps: AiCapabilities) {
    capsMock.mockResolvedValue(caps)
    useAiStore.setState({ caps: null })
    useUiStore.setState({ settingsOpen: true, settingsSection: 'ai' })
    host = document.createElement('div')
    document.body.appendChild(host)
    root = createRoot(host)
    await act(async () => {
      root.render(
        <TooltipProvider>
          <SettingsDialog />
        </TooltipProvider>,
      )
    })
    await act(async () => {})
    await act(async () => byText(ag('rowAria', { name: 'Claude Code' }))!.click())
  }

  const OAUTH = {
    'solovendor': { label: 'Solo Vendor', oauth_available: true, regions: ['global'], default_region: 'global' },
    minimax: { label: 'MiniMax', oauth_available: true, regions: ['cn', 'global'], default_region: 'cn' },
  }

  it('没有自有 client_id 的厂商：入口一个字都不出现', async () => {
    await openClaudeDetail(
      capsOf([agentCaps(), claudeCaps()], {
        oauth: {
          'solovendor': { ...OAUTH['solovendor'], oauth_available: false },
          minimax: { ...OAUTH.minimax, oauth_available: false },
        },
      }),
    )
    expect(document.querySelector('[data-oauth-login]')).toBeNull()
    expect(text()).not.toContain('Solo Vendor')
    expect(text()).not.toContain('MiniMax')
    expect(text()).not.toContain(ag('oauth.action', { name: '' }).trim())
  })

  it('老后端没有 oauth 字段：没有入口', async () => {
    await openClaudeDetail(capsOf([agentCaps(), claudeCaps()]))
    expect(document.querySelector('[data-oauth-login]')).toBeNull()
  })

  it('可用的厂商出现入口（MiniMax 国内 / 海外各一个），点开发起登录', async () => {
    startMock.mockResolvedValue(flow({ provider: 'minimax', region: 'cn', user_code: 'MM-5678' }))
    await openClaudeDetail(capsOf([agentCaps(), claudeCaps()], { oauth: OAUTH }))
    const entries = [...document.querySelectorAll('[data-oauth-login]')].map((e) => e.getAttribute('data-oauth-login'))
    expect(entries).toEqual(['solovendor:global', 'minimax:cn', 'minimax:global'])
    expect(text()).toContain(ag('oauth.action', { name: 'Solo Vendor' }))
    expect(text()).toContain(ag('oauth.action', { name: `MiniMax（${ag('oauth.region.cn')}）` }))
    await act(async () => (document.querySelector('[data-oauth-login="minimax:cn"]') as HTMLButtonElement).click())
    expect(startMock).toHaveBeenCalledWith('minimax', 'cn')
    expect(document.querySelector('[data-oauth-code]')?.textContent).toBe('MM-5678')
  })

  it('入口不出现在没有这条协议的 Agent（Codex）详情里', async () => {
    capsMock.mockResolvedValue(capsOf([agentCaps(), claudeCaps()], { oauth: OAUTH }))
    useAiStore.setState({ caps: null })
    useUiStore.setState({ settingsOpen: true, settingsSection: 'ai' })
    host = document.createElement('div')
    document.body.appendChild(host)
    root = createRoot(host)
    await act(async () => {
      root.render(
        <TooltipProvider>
          <SettingsDialog />
        </TooltipProvider>,
      )
    })
    await act(async () => {})
    await act(async () => byText(ag('rowAria', { name: 'Codex' }))!.click())
    expect(document.querySelector('[data-oauth-login]')).toBeNull()
  })

  it('已登录行：显示「已登录」+「退出」，没有「未填密钥」', async () => {
    logoutMock.mockResolvedValue(capsOf([agentCaps(), claudeCaps()]))
    capsMock.mockResolvedValue(capsOf([agentCaps(), claudeCaps()]))
    await openClaudeDetail(capsOf([agentCaps(), claudeCaps()], { endpoints: [oauthRow()], oauth: OAUTH }))
    expect(document.querySelector('[data-endpoint-signed-in="true"]')?.textContent).toBe(ag('oauth.signedIn'))
    expect(text()).not.toContain(ag('detail.noKeySuffix'))
    await act(async () => (document.querySelector('[data-endpoint-logout]') as HTMLButtonElement).click())
    expect(logoutMock).toHaveBeenCalledWith('solovendor-oauth')
  })

  it('已退出的行：显示「未登录」+「重新登录」（厂商可用时）', async () => {
    startMock.mockResolvedValue(flow())
    await openClaudeDetail(
      capsOf([agentCaps(), claudeCaps()], { endpoints: [oauthRow({ signed_in: false, has_key: false })], oauth: OAUTH }),
    )
    expect(document.querySelector('[data-endpoint-signed-in="false"]')?.textContent).toBe(ag('oauth.signedOut'))
    expect(document.querySelector('[data-endpoint-logout]')).toBeNull()
    await act(async () => (document.querySelector('[data-endpoint-relogin]') as HTMLButtonElement).click())
    expect(startMock).toHaveBeenCalledWith('solovendor', 'global')
  })

  it('已退出且该厂商入口不可用：没有「重新登录」', async () => {
    await openClaudeDetail(
      capsOf([agentCaps(), claudeCaps()], { endpoints: [oauthRow({ signed_in: false, has_key: false })] }),
    )
    expect(document.querySelector('[data-endpoint-relogin]')).toBeNull()
  })
})
