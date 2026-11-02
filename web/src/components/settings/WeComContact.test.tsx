/**
 * 企业微信联系入口：只在中文界面出现（判据 `currentLocale()`，不看系统区域）；英文界面不渲染元素。
 * 位置：发送问题反馈确认框的 done / unknown / failed 三态，与「帮助与诊断」页（发送功能关闭时也在）。
 * 点链接 = 带地址常量的外链（`target=_blank` 由壳转交系统浏览器，与仓库其余外链同一写法）。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  fetchDiagnosticsSummary: vi.fn(),
  fetchDiagSendCapability: vi.fn(),
  prepareDiagSend: vi.fn(),
  startDiagSend: vi.fn(),
  fetchDiagSendStatus: vi.fn(),
  cancelDiagSend: vi.fn(),
  discardDiagSend: vi.fn(),
}))

import {
  fetchDiagSendCapability,
  fetchDiagSendStatus,
  prepareDiagSend,
  startDiagSend,
  discardDiagSend,
  type DiagSendCapability,
  type DiagSendPrepared,
  type DiagSendStatus,
} from '@/lib/api'
import { i18n } from '@/i18n'
import { WECOM_CONTACT_URL } from '@/lib/contact'
import { DiagnosticsSendDialog } from './DiagnosticsSendDialog'
import { DiagnosticsSettings } from './DiagnosticsSettings'
import { TooltipProvider } from '../ui/Tooltip'
import { useEnvStore } from '@/store/envStore'
import { useDiagSendStore } from '@/store/diagSendStore'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true
Element.prototype.scrollIntoView ??= function scrollIntoView() {}
Element.prototype.hasPointerCapture ??= () => false
globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
} as never

const CAP: DiagSendCapability = { enabled: true, note_max_chars: 1000, retention_days: 30, categories: ['other'] }
const PREPARED = {
  id: 'pid-1',
  size: 2048,
  sha256: 'a'.repeat(64),
  schema: 6,
  max_bytes: 1 << 26,
  too_large: false,
  entries: [{ name: 'report.json', kind: 'environment' }],
} as DiagSendPrepared
const status = (over: Partial<DiagSendStatus>): DiagSendStatus =>
  ({ id: 'pid-1', state: 'sending', stage: 'init', size: 2048, sha256: PREPARED.sha256, sent: 0, total: 0, ...over }) as DiagSendStatus

let host: HTMLDivElement
let root: Root
const q = <T extends Element = HTMLElement>(sel: string) => document.body.querySelector<T>(sel)

async function render(node: React.ReactNode) {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => {
    root.render(<TooltipProvider>{node}</TooltipProvider>)
  })
  await act(async () => {})
}

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: false })
  for (const m of [prepareDiagSend, startDiagSend, fetchDiagSendStatus, fetchDiagSendCapability]) vi.mocked(m).mockReset()
  vi.mocked(discardDiagSend).mockReset().mockResolvedValue({ ok: true })
  vi.mocked(prepareDiagSend).mockResolvedValue(PREPARED)
  vi.mocked(startDiagSend).mockResolvedValue(status({}))
  useDiagSendStore.setState({ capability: null })
})
afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

async function dialogAt(state: 'ready' | 'done' | 'unknown' | 'failed') {
  if (state !== 'ready') {
    vi.mocked(fetchDiagSendStatus).mockResolvedValue(
      state === 'failed'
        ? status({ state: 'failed', stage: null, code: 'rate_limited', retryable: true })
        : status({ state, stage: null, report_id: 'TVD-ABCD-EFGH-JKMN-PQRS' }),
    )
  }
  await render(<DiagnosticsSendDialog open onOpenChange={() => {}} capability={CAP} />)
  if (state === 'ready') return
  await act(async () => {
    q('[data-diag-send-confirm]')!.click()
  })
  await act(async () => {
    await vi.advanceTimersByTimeAsync(400)
  })
}

describe('发送问题反馈确认框', () => {
  it('zh-CN done（没有回信通道）：副标题位置换成「已发送 · 想直接沟通？」，链接是常量地址的外链', async () => {
    await dialogAt('done')
    expect(q('[data-diag-send-status]')!.getAttribute('data-diag-send-status')).toBe('done')
    const line = q('[data-diag-send-sentence]')!
    expect(line.textContent).toContain(i18n.t('settings.diagnostics.send.doneTitle', { ns: 'dialogs' }))
    const a = line.querySelector<HTMLAnchorElement>('[data-wecom-link]')!
    expect(a.getAttribute('href')).toBe(WECOM_CONTACT_URL)
    expect(a.target).toBe('_blank')
    expect(a.rel).toContain('noreferrer')
  })

  it.each(['ready', 'unknown', 'failed'] as const)('zh-CN %s：确认框里不出现联系链接（也不占位）', async (state) => {
    await dialogAt(state)
    expect(q('[data-diag-send-status]')!.getAttribute('data-diag-send-status')).toBe(state)
    expect(q('[data-wecom-link]')).toBeNull()
    expect(q('[data-wecom-contact]')).toBeNull()
  })

  it('en-US done：只写「已发送」对应的英文，没有联系链接', async () => {
    await i18n.changeLanguage('en-US')
    await dialogAt('done')
    expect(q('[data-diag-send-status]')!.getAttribute('data-diag-send-status')).toBe('done')
    expect(q('[data-wecom-link]')).toBeNull()
    expect(q('[data-diag-send-sentence]')!.textContent).toBe(i18n.t('settings.diagnostics.send.doneTitle', { ns: 'dialogs' }))
  })
})

describe('帮助与诊断页', () => {
  async function settings(capability: object | null) {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve({ checks: [] }) } as Response)),
    )
    useEnvStore.setState({ env: { ok: true, python: '/p', source: 'system', matplotlib: '3', managed: false, bundled: true, state: 'idle' } as never })
    if (capability) vi.mocked(fetchDiagSendCapability).mockResolvedValue(capability as never)
    else vi.mocked(fetchDiagSendCapability).mockResolvedValue({ enabled: false })
    await render(<DiagnosticsSettings />)
  }

  it('zh-CN：发送功能开着时出现，在摘要之后、诊断包之前（发送第一行、诊断包最后一行）', async () => {
    await settings(CAP)
    const row = q('[data-diagnostics-contact]')!
    expect(row).not.toBeNull()
    expect(q<HTMLAnchorElement>('[data-diagnostics-contact] [data-wecom-link]')!.getAttribute('href')).toBe(WECOM_CONTACT_URL)
    expect(q('[data-diagnostics-contact] [data-wecom-qr-img]')).toBeNull()
    const rows = Array.from(q('[data-diagnostics-send]')!.parentElement!.children)
    expect(rows[0]).toBe(q('[data-diagnostics-send]'))
    expect(rows[rows.length - 2]).toBe(row)
    expect(rows[rows.length - 1]).toBe(q('[data-diagnostics-bundle]'))
  })

  it('zh-CN：发送功能关闭（capability null）也出现', async () => {
    await settings(null)
    expect(q('[data-diagnostics-send]')).toBeNull()
    expect(q('[data-diagnostics-contact]')).not.toBeNull()
  })

  it('en-US：不出现', async () => {
    await i18n.changeLanguage('en-US')
    await settings(CAP)
    expect(q('[data-diagnostics-send]')).not.toBeNull()
    expect(q('[data-diagnostics-contact]')).toBeNull()
    expect(q('[data-wecom-link]')).toBeNull()
  })
})
