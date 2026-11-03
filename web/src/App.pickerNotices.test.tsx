/**
 * 项目选择页（phase === 'none'）也要挂通知轨：引擎退出的修复卡 / 上次没正常退出的提示卡不能等开了项目才看得见。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useOnboardingStore } from '@/store/onboardingStore'

vi.mock('@/components/ProjectPicker', () => ({ ProjectPicker: () => <div data-fake-picker /> }))
vi.mock('@/components/TelemetryConsentDialog', () => ({ TelemetryConsentDialog: () => null }))
vi.mock('@/components/DiagnosticsSendHost', () => ({ DiagnosticsSendHost: () => null }))
vi.mock('@/components/UpdateNoticeDialog', () => ({ UpdateNoticeDialog: () => null }))
vi.mock('@/components/NativeConfirmDialog', () => ({ NativeConfirmDialog: () => null }))
vi.mock('@/components/CloseGuardDialog', () => ({ CloseGuardDialog: () => null }))

import { App } from '@/App'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { useExitNoticeStore } from '@/store/exitNoticeStore'
import { useProjectStore } from '@/store/projectStore'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

let host: HTMLDivElement
let root: Root

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn(() => Promise.reject(new Error('offline'))))
  useProjectStore.setState({ phase: 'none', init: vi.fn(() => Promise.resolve()) } as never)
  // 首启欢迎页（ADR 0136）会盖在选择页上：这里测的是选择页自己的通知轨，记成欢迎页已经出现过
  useOnboardingStore.setState({ welcomeSeenAt: 1 })
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})
afterEach(() => {
  act(() => root.unmount())
  host.remove()
  act(() => useExitNoticeStore.setState({ engineExit: null, restarting: false, previous: null }))
  vi.unstubAllGlobals()
})

describe('项目选择页的通知轨（#1040 B1）', () => {
  it('phase === none：引擎退出的修复卡出现，且只有一份', async () => {
    await act(async () => {
      root.render(
        <TooltipProvider>
          <App />
        </TooltipProvider>,
      )
    })
    expect(host.querySelector('[data-fake-picker]')).not.toBeNull()
    expect(host.querySelector('[data-engine-exited-card]')).toBeNull()
    await act(async () => useExitNoticeStore.getState().setEngineExit('nonzero'))
    expect(host.querySelectorAll('[data-engine-exited-card]')).toHaveLength(1)
    expect(host.querySelectorAll('[data-picker-notification-host]')).toHaveLength(1)
  })

  it('选择页那份通知轨不贡献 role=alert（选择页自己的报错是唯一的 alert），assertive 播报区仍在', async () => {
    await act(async () => {
      root.render(
        <TooltipProvider>
          <App />
        </TooltipProvider>,
      )
    })
    const rail = host.querySelector('[data-picker-notification-host]')!
    expect(rail.querySelectorAll('[role="alert"]')).toHaveLength(0)
    expect(rail.querySelectorAll('[aria-live="assertive"]').length).toBeGreaterThanOrEqual(2)
    await act(async () => useExitNoticeStore.getState().setEngineExit('nonzero'))
    expect(rail.querySelectorAll('[role="alert"]')).toHaveLength(0)
  })
})
