/**
 * 「帮助与诊断」页里的「发送问题反馈」入口（ADR 0118）：前端侧失败即关——能力说明没给 / 关着 / 取不到，
 * 页面上就**没有**这一行（正式用户看不到可上传的入口）；打开后才画，且点它之前不出任何远程请求。
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
  discardDiagSend: vi.fn(),
}))

import { fetchDiagSendCapability, prepareDiagSend, startDiagSend, discardDiagSend } from '@/lib/api'
import { DiagnosticsSettings } from '@/components/settings/DiagnosticsSettings'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { useEnvStore } from '@/store/envStore'
import { useContactStore } from '@/store/contactStore'
import { useDiagSendStore } from '@/store/diagSendStore'
import { useUiStore } from '@/store/uiStore'
import { currentLocale } from '@/i18n'

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

const capMock = vi.mocked(fetchDiagSendCapability)
let host: HTMLDivElement
let root: Root

async function mount() {
  vi.stubGlobal(
    'fetch',
    vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve({ checks: [] }) } as Response)),
  )
  useEnvStore.setState({ env: { ok: true, python: '/p', source: 'system', matplotlib: '3', managed: false, bundled: true, state: 'idle' } as never })
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => {
    root.render(
      <TooltipProvider>
        <DiagnosticsSettings />
      </TooltipProvider>,
    )
  })
  await act(async () => {})
}

beforeEach(() => {
  capMock.mockReset()
  useDiagSendStore.setState({ capability: null })
  useContactStore.setState({ capability: null, open: false })
  vi.mocked(prepareDiagSend).mockReset()
  vi.mocked(startDiagSend).mockReset()
  vi.mocked(discardDiagSend).mockReset().mockResolvedValue({ ok: true })
})
afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
  vi.unstubAllGlobals()
})

describe('发送问题反馈入口', () => {
  it.each([
    ['关着', () => capMock.mockResolvedValue({ enabled: false })],
    ['取不到', () => capMock.mockRejectedValue(new Error('404'))],
    ['回了别的东西', () => capMock.mockResolvedValue({} as never)],
  ])('能力说明%s：页面上没有这一行', async (_n, arrange) => {
    arrange()
    await mount()
    expect(document.body.querySelector('[data-diagnostics-send]')).toBeNull()
    expect(document.body.querySelector('[data-diagnostics-bundle]')).not.toBeNull() // 本地导出照旧
    expect(vi.mocked(prepareDiagSend)).not.toHaveBeenCalled()
  })

  it('打开后才画这一行；点它之前不备包、不发送', async () => {
    capMock.mockResolvedValue({ enabled: true, note_max_chars: 1000, retention_days: 30, categories: ['other'] })
    await mount()
    expect(document.body.querySelector('[data-diagnostics-send]')).not.toBeNull()
    expect(vi.mocked(prepareDiagSend)).not.toHaveBeenCalled()
    expect(vi.mocked(startDiagSend)).not.toHaveBeenCalled()
  })

  it('顺序（2026-10-21）：发送问题反馈第一行，诊断包最后一行', async () => {
    capMock.mockResolvedValue({ enabled: true, categories: ['other'] })
    await mount()
    const send = document.body.querySelector('[data-diagnostics-send]')!
    const bundle = document.body.querySelector('[data-diagnostics-bundle]')!
    const group = send.parentElement!
    expect(group.firstElementChild).toBe(send)
    expect(group.lastElementChild).toBe(bundle)
  })
})

describe('联系作者开着时：设置里的「发送问题反馈」就是顶栏那个对话框（2026-10 审计 A2）', () => {
  const contactCap = { enabled: true, body_max: 5000 } as never

  it('点它关设置、打开联系作者对话框；不打开诊断发送对话框', async () => {
    capMock.mockResolvedValue({ enabled: true, note_max_chars: 1000, retention_days: 30, categories: ['other'] })
    useContactStore.setState({ capability: contactCap })
    useUiStore.setState({ settingsOpen: true })
    await mount()
    const open = document.body.querySelector<HTMLElement>('[data-diagnostics-send-open]')!
    await act(async () => open.click())
    expect(useContactStore.getState().open).toBe(true)
    expect(useUiStore.getState().settingsOpen).toBe(false)
    expect(useDiagSendStore.getState().open).toBe(false)
  })

  it('联系作者开着、诊断发送关着：这一行照样在（它的家是联系作者对话框）', async () => {
    capMock.mockResolvedValue({ enabled: false })
    useContactStore.setState({ capability: contactCap })
    await mount()
    expect(document.body.querySelector('[data-diagnostics-send]')).not.toBeNull()
  })

  it('企业微信那一行随之撤掉（它在联系作者对话框里）；联系作者关着时仍在设置里', async () => {
    capMock.mockResolvedValue({ enabled: false })
    useContactStore.setState({ capability: contactCap })
    await mount()
    expect(currentLocale()).toBe('zh-CN') // 企业微信行只在中文界面出现：先钉住前提，下面的两个断言才有主语
    expect(document.body.querySelector('[data-diagnostics-contact]')).toBeNull()
    await act(async () => root.unmount())
    host.remove()
    useContactStore.setState({ capability: null })
    await mount()
    expect(document.body.querySelector('[data-diagnostics-contact]')).not.toBeNull()
  })
})
