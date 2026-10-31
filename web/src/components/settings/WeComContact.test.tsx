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
import { WeComContactNote } from './WeComContact'
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
  it.each(['done', 'unknown', 'failed'] as const)('zh-CN %s：出现联系行，链接是常量地址的外链', async (state) => {
    await dialogAt(state)
    expect(q('[data-diag-send-status]')!.getAttribute('data-diag-send-status')).toBe(state)
    expect(q('[data-wecom-contact="active"]')).not.toBeNull()
    expect(q('[data-wecom-contact="reserved"]')).toBeNull()
    const a = q<HTMLAnchorElement>('[data-wecom-link]')!
    expect(a.getAttribute('href')).toBe(WECOM_CONTACT_URL)
    expect(a.target).toBe('_blank')
    expect(a.rel).toContain('noreferrer')
  })

  it('zh-CN preparing：只占位（不可见、inert、无可聚焦链接内容对外）', async () => {
    vi.mocked(prepareDiagSend).mockReturnValue(new Promise(() => {}))
    await render(<DiagnosticsSendDialog open onOpenChange={() => {}} capability={CAP} />)
    expect(q('[data-diag-send-status]')!.getAttribute('data-diag-send-status')).toBe('preparing')
    expect(q('[data-wecom-contact="active"]')).toBeNull()
    expect(q('[data-wecom-contact="reserved"]')).not.toBeNull()
  })

  it.each(['ready', 'done', 'unknown', 'failed'] as const)(
    'zh-CN %s：隐形尺寸副本恒为最长文案（带编号那句），各态取同一最大高度',
    async (state) => {
      await dialogAt(state)
      const sizer = q('[data-wecom-contact-sizer]')!
      expect(sizer.getAttribute('aria-hidden')).toBe('true')
      expect(sizer.className).toContain('invisible')
      const hint = i18n.t('settings.diagnostics.send.contact.hint', { ns: 'dialogs' })
      const noId = i18n.t('settings.diagnostics.send.contact.hintNoId', { ns: 'dialogs' })
      expect(hint.length).toBeGreaterThanOrEqual(noId.length)
      expect(sizer.textContent).toContain(hint)
      // 可见那句与尺寸副本叠在同一个网格格子里，高度取两者最大
      const shown = sizer.parentElement!.querySelector('p:not([data-wecom-contact-sizer])')!
      expect(shown.className).toContain('[grid-area:1/1]')
      expect(sizer.className).toContain('[grid-area:1/1]')
      expect(sizer.textContent!.length).toBeGreaterThanOrEqual(shown.textContent!.length)
    },
  )

  it('zh-CN ready：不出现', async () => {
    await dialogAt('ready')
    expect(q('[data-diag-send-status]')!.getAttribute('data-diag-send-status')).toBe('ready')
    expect(q('[data-wecom-contact="active"]')).toBeNull()
    const r = q('[data-wecom-contact="reserved"]')!
    expect(r.getAttribute('aria-hidden')).toBe('true')
    expect(r.hasAttribute('inert')).toBe(true)
    expect(r.className).toContain('invisible')
  })

  it.each(['done', 'unknown', 'failed'] as const)('en-US %s：元素根本不存在', async (state) => {
    await i18n.changeLanguage('en-US')
    await dialogAt(state)
    expect(q('[data-diag-send-status]')!.getAttribute('data-diag-send-status')).toBe(state)
    expect(q('[data-wecom-contact]')).toBeNull() // 含占位：英文界面不渲染任何元素
    expect(q('[data-wecom-link]')).toBeNull()
    expect(q('[data-wecom-qr-toggle]')).toBeNull()
  })

  it('二维码默认折叠（无 img），展开后出现 img 且 alt 有意义', async () => {
    await dialogAt('done')
    expect(q('[data-wecom-qr-toggle]')).not.toBeNull()
    expect(q('[data-wecom-qr-img]')).toBeNull()
    const b = q<HTMLButtonElement>('[data-wecom-qr-toggle]')!
    expect(b.getAttribute('aria-expanded')).toBe('false')
    await act(async () => b.click())
    expect(b.getAttribute('aria-expanded')).toBe('true')
    const img = q<HTMLImageElement>('[data-wecom-qr-img]')!
    expect(img).not.toBeNull()
    expect(img.alt.length).toBeGreaterThan(8)
    expect(img.alt).toContain('企业微信')
  })
})

describe('联系行的二维码折叠', () => {
  it('离开结果态再回来，二维码从折叠开始（重试 / 重新备包后不自己展开）', async () => {
    await render(<WeComContactNote active withReportId />)
    await act(async () => q<HTMLButtonElement>('[data-wecom-qr-toggle]')!.click())
    expect(q('[data-wecom-qr-img]')).not.toBeNull()
    await act(async () => root.render(<TooltipProvider><WeComContactNote active={false} withReportId={false} /></TooltipProvider>))
    expect(q('[data-wecom-qr-img]')).toBeNull()
    await act(async () => root.render(<TooltipProvider><WeComContactNote active withReportId /></TooltipProvider>))
    expect(q('[data-wecom-qr-img]')).toBeNull()
    expect(q('[data-wecom-qr-toggle]')!.getAttribute('aria-expanded')).toBe('false')
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
