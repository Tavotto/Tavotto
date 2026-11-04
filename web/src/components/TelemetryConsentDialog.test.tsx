/**
 * 首启询问框：出现条件、两个按钮写下的东西、以及**文案不许有硬编码英文/中文**。
 *
 * 「拒绝不比同意难点」是这里唯一的视觉硬约束：主次可分，但两个选项同尺寸（lg）、
 * 同一行、都是真按钮；拒绝写 disabled 不再问。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api', () => ({
  fetchTelemetrySettings: vi.fn(),
  patchTelemetryConsent: vi.fn(),
}))

import { fetchTelemetrySettings, patchTelemetryConsent } from '@/lib/api'
import { CONSENT_VARIANTS, pickConsentVariant, TelemetryConsentDialog } from '@/components/TelemetryConsentDialog'
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
const openDetails = () =>
  act(() => {
    ;(document.querySelector('[data-telemetry-details-toggle]') as HTMLButtonElement).click()
  })

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

  it('拒绝不比同意难点：同尺寸、同一行、都是真按钮（不是文字链接）', () => {
    const allow = buttonByText(dlg('allow'))!
    const decline = buttonByText(dlg('decline'))!
    expect(allow && decline).toBeTruthy()
    expect(allow.tagName).toBe('BUTTON')
    expect(decline.tagName).toBe('BUTTON')
    // lg 档：h-8 + text-base；任何一颗缩小或去掉底色都会掉出这里
    for (const b of [allow, decline]) {
      expect(b.className).toContain('h-8')
      expect(b.className).toContain('text-base')
      expect(b.className).toContain('rounded-full')
    }
    expect(decline.className).toContain('bg-surface-hover')
    expect(decline.parentElement).toBe(allow.parentElement)
  })

  it('两颗按钮是仅有的出口：不画右上角 ×（画了却关不掉就是假控件）', () => {
    expect(document.querySelector('[data-dialog-close]')).toBeNull()
  })

  it('Esc 不算表态：框还在、什么都没写', async () => {
    const content = document.querySelector('[data-dialog]') as HTMLElement
    await act(async () => {
      content.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    })
    expect(patchMock).not.toHaveBeenCalled()
    expect(useTelemetryStore.getState().askOpen).toBe(true)
    expect(document.body.textContent).toContain(dlg('title'))
  })

  it('重新同意：delta 句在展开区里（默认收着），小字行链接换成「这次新增了什么」，主按钮写「继续」', () => {
    const q = () => document.querySelector('[data-telemetry-reconsent-delta]')
    const toggle = () => document.querySelector('[data-telemetry-details-toggle]')!
    useTelemetryStore.setState({ settings: settings({ consent: 'unset' }), askOpen: true })
    expect(toggle().textContent).toBe(dlg('details'))
    openDetails()
    expect(q()).toBeNull()
    act(() => useTelemetryStore.setState({
      settings: settings({ consent: 'enabled', saved_consent_version: 2, needs_reconsent: true }),
    }))
    expect(toggle().textContent).toBe(dlg('detailsReconsent'))
    expect(buttonByText(dlg('allowReconsent'))).toBeTruthy()
    // 展开态在重新询问时保留着：delta 句是展开区的第一个孩子
    const details = document.querySelector('[data-telemetry-details]')!
    expect(details.firstElementChild).toBe(q())
    expect(q()!.textContent).toBe(dlg('reconsentDelta'))
  })

  it('详情默认折叠；点开后「会发送 / 绝不发送」与隐私政策链接才出现', () => {
    const sel = '[data-telemetry-sends], [data-telemetry-never], [data-telemetry-details]'
    expect(document.querySelector(sel)).toBeNull()
    const toggle = document.querySelector('[data-telemetry-details-toggle]')!
    expect(toggle.getAttribute('aria-expanded')).toBe('false')
    openDetails()
    expect(toggle.getAttribute('aria-expanded')).toBe('true')
    const text = document.body.textContent ?? ''
    for (const key of ['sendsTitle', 'sendsVersion', 'sendsPlatform', 'sendsFeatures',
                       'sendsOutcome', 'sendsErrorSummary', 'neverTitle', 'neverFigures', 'neverScripts',
                       'neverPaths', 'neverData', 'neverPrompts', 'policy']) {
      expect(text).toContain(dlg(key))
    }
    expect(document.querySelector('[data-telemetry-sends]')).not.toBeNull()
    expect(document.querySelector('[data-telemetry-never]')).not.toBeNull()
  })

  it('默认可见的事实：标题、第一句、事实句、设置里可改', () => {
    const text = document.body.textContent ?? ''
    for (const key of ['title', 'fact', 'later', 'details']) expect(text).toContain(dlg(key))
    expect(document.querySelector('[data-telemetry-lead]')!.textContent).toBe(
      dlg(CONSENT_VARIANTS[document.querySelector('[data-telemetry-variant]')!.getAttribute('data-telemetry-variant') as 'A'].lead),
    )
  })
})

describe('三套卡片', () => {
  it('随机源可注入：落在 [0,1) 里各档对应一套，边界不越界', () => {
    expect(pickConsentVariant(() => 0)).toBe('A')
    expect(pickConsentVariant(() => 0.4)).toBe('B')
    expect(pickConsentVariant(() => 0.99)).toBe('C')
    expect(pickConsentVariant(() => 1)).toBe('C')
  })

  it('三套只有 lead 不同：共用同一组事实键（发什么、不发什么、在哪改、详情链接）', () => {
    const ids = Object.keys(CONSENT_VARIANTS)
    expect(ids).toEqual(['A', 'B', 'C'])
    for (const v of Object.values(CONSENT_VARIANTS)) expect(Object.keys(v)).toEqual(['lead'])
    expect(new Set(Object.values(CONSENT_VARIANTS).map((v) => v.lead)).size).toBe(3)
    const bodies = ids.map((id) => {
      vi.spyOn(Math, 'random').mockReturnValue(ids.indexOf(id) / 3 + 0.01)
      fetchMock.mockResolvedValue(settings())
      mount()
      act(() => useTelemetryStore.setState({ settings: settings(), askOpen: true }))
      openDetails()
      expect(document.querySelector('[data-telemetry-variant]')!.getAttribute('data-telemetry-variant')).toBe(id)
      expect(document.querySelector(`[data-telemetry-art="${id}"]`)).not.toBeNull()
      // 去掉 lead 之后，其余文字逐字相同
      const lead = document.querySelector('[data-telemetry-lead]')!
      const clone = document.querySelector('[data-telemetry-variant]')!.cloneNode(true) as HTMLElement
      clone.querySelector('[data-telemetry-lead]')!.remove()
      void lead
      const out = clone.textContent
      act(() => root.unmount())
      host.remove()
      document.body.innerHTML = ''
      vi.restoreAllMocks()
      return out
    })
    expect(bodies[1]).toBe(bodies[0])
    expect(bodies[2]).toBe(bodies[0])
  })

  it('同一次打开里固定：重渲染不换套；关了再开才重抽', () => {
    const spy = vi.spyOn(Math, 'random').mockReturnValue(0.01)
    mount()
    act(() => useTelemetryStore.setState({ settings: settings(), askOpen: true }))
    const cur = () => document.querySelector('[data-telemetry-variant]')!.getAttribute('data-telemetry-variant')
    expect(cur()).toBe('A')
    spy.mockReturnValue(0.99)
    act(() => useTelemetryStore.setState({ settings: settings({ needs_reconsent: false }) }))
    act(() => root.render(<TelemetryConsentDialog />))
    expect(cur()).toBe('A')
    act(() => useTelemetryStore.setState({ askOpen: false }))
    act(() => useTelemetryStore.setState({ askOpen: true }))
    expect(cur()).toBe('C')
    spy.mockRestore()
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
