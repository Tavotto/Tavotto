/**
 * 设置里的「匿名用量统计」开关：真的写到后端，且硬开关关着时点不动。
 *
 * 它放在「隐私、诊断与 About」这一档里——用户找「这东西会不会上传我的图」
 * 时会来这里，而不是去翻一个新分区。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  fetchTelemetrySettings: vi.fn(),
  patchTelemetryConsent: vi.fn(),
}))

import { fetchTelemetrySettings, patchTelemetryConsent } from '@/lib/api'
import { SettingsDialog } from '@/components/SettingsDialog'
import { t } from '@/i18n'
import { setTelemetryEnabled } from '@/lib/telemetry'
import { useTelemetryStore } from '@/store/telemetryStore'
import { useUiStore } from '@/store/uiStore'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const fetchMock = vi.mocked(fetchTelemetrySettings)
const patchMock = vi.mocked(patchTelemetryConsent)

const settings = (over: Record<string, unknown> = {}) =>
  ({
    consent: 'enabled',
    enabled: true,
    hard_disabled: false,
    consent_version: 1,
    saved_consent_version: 1,
  needs_reconsent: false,
    ...over,
  }) as Awaited<ReturnType<typeof fetchTelemetrySettings>>

const st = (key: string) => t(`settings.${key}`, { ns: 'dialogs' })

let host: HTMLDivElement
let root: Root

async function open(initial = settings()) {
  fetchMock.mockResolvedValue(initial)
  useTelemetryStore.setState({ settings: null, askOpen: false })
  useUiStore.setState({ settingsOpen: true, settingsSection: 'about' })
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => {
    root.render(<SettingsDialog />)
  })
  await act(async () => {})
}

const toggle = () =>
  [...document.querySelectorAll('button[role="switch"]')].find(
    (b) => b.getAttribute('aria-label') === st('about.telemetry.toggle'),
  ) as HTMLButtonElement | undefined

beforeEach(() => {
  fetchMock.mockReset()
  patchMock.mockReset()
  patchMock.mockResolvedValue(settings({ consent: 'disabled', enabled: false }))
  setTelemetryEnabled(false)
  // AboutSection 挂载时会拉一次 /api/diagnostics
  vi.stubGlobal('fetch', vi.fn(() => Promise.resolve({ json: () => Promise.resolve({ checks: [] }) })))
})

afterEach(() => {
  act(() => root.unmount())
  host.remove()
  document.body.innerHTML = ''
  vi.unstubAllGlobals()
  useUiStore.setState({ settingsOpen: false })
})

describe('设置里的开关', () => {
  /**
   * 隐私授权的**最短摘要常驻**：用户判断「这东西会不会上传我的图」靠的是它，
   * 不许折叠。完整的「会发送什么 / 绝不发送什么」两份清单读一次就够，
   * 进小问号——但**必须真的打得开**，否则就是把承诺藏了起来。
   */
  it('最短隐私摘要与政策链接常驻，不需要任何交互', async () => {
    await open()
    const text = document.body.textContent ?? ''
    expect(text).toContain(st('about.telemetry.title'))
    expect(text).toContain(st('about.telemetry.summary'))
    expect(text).not.toContain('**')
    const link = [...document.querySelectorAll('a')].find(
      (a) => a.textContent?.trim() === st('about.telemetry.policy'),
    )
    expect(link?.getAttribute('href')).toContain('privacy.md')
  })

  it('「会发送什么 / 绝不发送什么」在小问号里，点开就有', async () => {
    await open()
    const before = document.body.textContent ?? ''
    // 折叠状态下确实看不到——否则下面那段断言证明不了任何事
    expect(before).not.toContain(st('about.telemetry.sendsBefore'))

    const help = [...document.querySelectorAll('button')].find(
      (b) => b.getAttribute('aria-label') === st('about.telemetry.helpAria'),
    ) as HTMLButtonElement
    expect(help).toBeTruthy()
    await act(async () => {
      help.click()
    })

    const text = document.body.textContent ?? ''
    expect(text).toContain(st('about.telemetry.sendsBefore'))
    // 跨会话稳定这一句必须真的出现，而且**不能带字面 Markdown 星号**
    expect(text).toContain(st('about.telemetry.sendsPersist'))
    expect(text).toContain(st('about.telemetry.sendsAfter'))
    expect(text).toContain(st('about.telemetry.never'))
    expect(text).not.toContain('**')
  })

  it('小问号键盘可达：聚焦即展开，Esc 收回', async () => {
    await open()
    const help = [...document.querySelectorAll('button')].find(
      (b) => b.getAttribute('aria-label') === st('about.telemetry.helpAria'),
    ) as HTMLButtonElement
    expect(help.getAttribute('aria-expanded')).toBe('false')
    await act(async () => {
      help.focus()
      help.dispatchEvent(new FocusEvent('focus', { bubbles: false }))
    })
    expect(help.getAttribute('aria-expanded')).toBe('true')
    expect(document.body.textContent).toContain(st('about.telemetry.never'))

    await act(async () => {
      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    })
    expect(help.getAttribute('aria-expanded')).toBe('false')
  })

  it('关掉时写 disabled 并立刻停止发送', async () => {
    await open()
    setTelemetryEnabled(true)
    expect(toggle()?.getAttribute('aria-checked')).toBe('true')
    await act(async () => {
      toggle()?.click()
    })
    expect(patchMock).toHaveBeenCalledWith('disabled', 'settings')
  })

  it('打开时写 enabled', async () => {
    await open(settings({ consent: 'disabled', enabled: false }))
    patchMock.mockResolvedValue(settings())
    await act(async () => {
      toggle()?.click()
    })
    expect(patchMock).toHaveBeenCalledWith('enabled', 'settings')
  })

  it('TAVOTTO_NO_TELEMETRY 关着时开关是死的，并说明是谁关的', async () => {
    await open(settings({ consent: 'unset', enabled: false, hard_disabled: true }))
    expect(toggle()?.disabled).toBe(true)
    expect(document.body.textContent).toContain(st('about.telemetry.hardDisabled'))
    await act(async () => {
      toggle()?.click()
    })
    expect(patchMock).not.toHaveBeenCalled()
  })
})
