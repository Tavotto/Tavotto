/**
 * 「帮助与诊断」页里的「发送问题反馈」入口（ADR 0118）：默认关闭——能力说明没给 / 关着 / 取不到，
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

  it('这一行在「诊断报告」组的最后：能力说明异步到达时，上面的入口不动', async () => {
    capMock.mockResolvedValue({ enabled: true, categories: ['other'] })
    await mount()
    const group = document.body.querySelector('[data-diagnostics-send]')!.parentElement!
    expect(group.lastElementChild).toBe(document.body.querySelector('[data-diagnostics-send]'))
  })
})
