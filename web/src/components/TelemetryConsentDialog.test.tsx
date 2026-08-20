/**
 * 首启询问框：出现条件、两个按钮写下的东西、以及**文案不许有硬编码英文/中文**。
 *
 * 「拒绝不比同意难点」是这里唯一的视觉硬约束：两个按钮同一个 variant，
 * 都在同一行、同样大小。深色主按钮留给导出那类真正的主动作。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api', () => ({
  fetchTelemetrySettings: vi.fn(),
  patchTelemetryConsent: vi.fn(),
}))

import { fetchTelemetrySettings, patchTelemetryConsent } from '@/lib/api'
import { TelemetryConsentDialog } from '@/components/TelemetryConsentDialog'
import { setLocale, t } from '@/i18n'
import { setTelemetryEnabled } from '@/lib/telemetry'
import { useTelemetryStore } from '@/store/telemetryStore'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const patchMock = vi.mocked(patchTelemetryConsent)
const fetchMock = vi.mocked(fetchTelemetrySettings)

const settings = (over: Record<string, unknown> = {}) =>
  ({
    consent: 'unset',
    enabled: false,
    hard_disabled: false,
    consent_version: 1,
    saved_consent_version: 0,
  needs_reconsent: false,
    ...over,
  }) as Awaited<ReturnType<typeof fetchTelemetrySettings>>

let host: HTMLDivElement
let root: Root

function mount() {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  act(() => root.render(<TelemetryConsentDialog />))
}

const dlg = (key: string) => t(`telemetry.${key}`, { ns: 'dialogs' })

/** Radix 把内容渲染到 portal 里，所以按整个 document 找 */
const buttonByText = (text: string) =>
  [...document.querySelectorAll('button')].find((b) => b.textContent?.trim() === text)

beforeEach(() => {
  patchMock.mockReset()
  fetchMock.mockReset()
  patchMock.mockResolvedValue(settings({ consent: 'disabled' }))
  setTelemetryEnabled(false)
  useTelemetryStore.setState({ settings: null, askOpen: false })
})

afterEach(() => {
  act(() => root.unmount())
  host.remove()
  document.body.innerHTML = ''
})

describe('出现条件', () => {
  it('askOpen 为假时什么都不渲染', () => {
    mount()
    expect(document.body.textContent).not.toContain(dlg('title'))
  })

  it('consent=unset 时出现', async () => {
    fetchMock.mockResolvedValue(settings())
    mount()
    await act(async () => {
      await useTelemetryStore.getState().load()
    })
    expect(document.body.textContent).toContain(dlg('title'))
  })

  it.each([
    ['enabled', settings({ consent: 'enabled', enabled: true })],
    ['disabled', settings({ consent: 'disabled' })],
    ['hard_disabled', settings({ hard_disabled: true })],
  ])('%s 时不出现', async (_name, value) => {
    fetchMock.mockResolvedValue(value)
    mount()
    await act(async () => {
      await useTelemetryStore.getState().load()
    })
    expect(document.body.textContent).not.toContain(dlg('title'))
  })
})

describe('两个选择', () => {
  beforeEach(async () => {
    fetchMock.mockResolvedValue(settings())
    mount()
    await act(async () => {
      await useTelemetryStore.getState().load()
    })
  })

  it('「分享」写 enabled', async () => {
    patchMock.mockResolvedValue(settings({ consent: 'enabled', enabled: true }))
    await act(async () => {
      buttonByText(dlg('allow'))?.click()
    })
    expect(patchMock).toHaveBeenCalledWith('enabled', 'first_run')
  })

  it('「暂不」写 disabled（不是留在 unset）', async () => {
    await act(async () => {
      buttonByText(dlg('decline'))?.click()
    })
    expect(patchMock).toHaveBeenCalledWith('disabled', 'first_run')
  })

  it('选完就收起，不再纠缠', async () => {
    await act(async () => {
      buttonByText(dlg('decline'))?.click()
    })
    expect(useTelemetryStore.getState().askOpen).toBe(false)
    expect(document.body.textContent).not.toContain(dlg('title'))
  })

  it('拒绝不比同意难点：两个按钮同一个视觉档位', () => {
    const allow = buttonByText(dlg('allow'))
    const decline = buttonByText(dlg('decline'))
    expect(allow && decline).toBeTruthy()
    expect(decline!.className).toBe(allow!.className)
  })

  it('说清楚发什么、不发什么', () => {
    const text = document.body.textContent ?? ''
    for (const key of ['sendsTitle', 'sendsVersion', 'sendsPlatform', 'sendsFeatures',
                       'sendsOutcome', 'neverTitle', 'neverFigures', 'neverScripts',
                       'neverPaths', 'neverData', 'neverPrompts', 'later']) {
      expect(text).toContain(dlg(key))
    }
  })
})

describe('多语言', () => {
  it('英文界面下没有任何未翻译的中文残留', async () => {
    await act(async () => {
      await setLocale('en-US')
    })
    fetchMock.mockResolvedValue(settings())
    mount()
    await act(async () => {
      await useTelemetryStore.getState().load()
    })
    const text = document.body.textContent ?? ''
    expect(text).toContain(dlg('title'))
    expect(text).not.toMatch(/[一-鿿]/)
    // key 本身漏出来（缺文案时 i18n 会原样吐回 key）同样算失败
    expect(text).not.toContain('telemetry.')
    await act(async () => {
      await setLocale('zh-CN')
    })
  })
})
