/**
 * 设置里的「匿名用量统计」：三档同意态、真的写到后端、硬开关关着时点不动。
 *
 * 它放在「隐私、诊断与 About」这一档里——用户找「这东西会不会上传我的图」
 * 时会来这里，而不是去翻一个新分区。
 *
 * 审计 T49 之前这里是个**二值开关**，于是 `unset`（还没问过）和 `disabled`
 * （问过了，用户说不）在界面上一模一样——后端刻意分开的两件事被界面重新合并
 * 了一次。下面这组用例的主语就是这个：**三档必须是三种可辨状态**，而**可写的
 * 仍然只有开 / 关两档**。
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
import { TooltipProvider } from '@/components/ui/Tooltip'
import { t } from '@/i18n'
import { setTelemetryEnabled } from '@/lib/telemetry'
import { TELEMETRY_DISCLOSED_EVENTS } from '@/lib/telemetryDisclosure'
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
    root.render(
      <TooltipProvider>
        <SettingsDialog />
      </TooltipProvider>,
    )
  })
  await act(async () => {})
}

const bodyText = () => document.body.textContent ?? ''

/** 同意控件那一组单选。**认 role + 整组的可达名**，不认某个 class */
const radios = () =>
  [
    ...(document
      .querySelector(`[role="radiogroup"][aria-label="${st('about.telemetry.toggle')}"]`)
      ?.querySelectorAll('button[role="radio"]') ?? []),
  ] as HTMLButtonElement[]
const seg = (label: string) => radios().find((b) => b.textContent?.trim() === label)
/** 当前选中的是哪一档；一档都没选中就是 null——那正是「尚未选择」 */
const chosen = () => radios().find((b) => b.getAttribute('aria-checked') === 'true') ?? null

beforeEach(() => {
  fetchMock.mockReset()
  patchMock.mockReset()
  patchMock.mockResolvedValue(settings({ consent: 'disabled', enabled: false }))
  setTelemetryEnabled(false)
  vi.stubGlobal(
    'fetch',
    vi.fn(() => Promise.resolve({ json: () => Promise.resolve({ checks: [] }) })),
  )
})

afterEach(() => {
  act(() => root.unmount())
  host.remove()
  document.body.innerHTML = ''
  vi.unstubAllGlobals()
  useUiStore.setState({ settingsOpen: false })
})

describe('隐私承诺常驻', () => {
  /**
   * 隐私授权的**最短摘要常驻**：用户判断「这东西会不会上传我的图」靠的是它，
   * 不许折叠。
   */
  it('最短隐私摘要与政策链接常驻，不需要任何交互', async () => {
    await open()
    expect(bodyText()).toContain(st('about.telemetry.title'))
    expect(bodyText()).toContain(st('about.telemetry.summary'))
    expect(bodyText()).not.toContain('**')
    const link = [...document.querySelectorAll('a')].find(
      (a) => a.textContent?.trim() === st('about.telemetry.policy'),
    )
    expect(link?.getAttribute('href')).toContain('privacy.md')
  })
})

describe('三档同意是三种可辨状态', () => {
  it('同意：「开启」被选中', async () => {
    await open(settings({ consent: 'enabled', enabled: true }))
    expect(chosen()).toBe(seg(st('about.telemetry.optIn')))
    expect(bodyText()).not.toContain(st('about.telemetry.unset'))
  })

  it('拒绝：「关闭」被选中，而不是「什么都没选」', async () => {
    await open(settings({ consent: 'disabled', enabled: false }))
    expect(chosen()).toBe(seg(st('about.telemetry.optOut')))
    expect(bodyText()).not.toContain(st('about.telemetry.unset'))
  })

  /**
   * 这一条是整组的主语：**「还没问过」不许画成「用户说了不」**。
   * 判据落在控件的选中态上（一档都没选中），而不是某句话上——只看文案的话，
   * 把两档都渲染成选中也照样绿。
   */
  it('未选择：一档都没选中，并且明说是「尚未选择」', async () => {
    await open(settings({ consent: 'unset', enabled: false }))
    expect(radios()).toHaveLength(2)
    expect(chosen()).toBeNull()
    expect(bodyText()).toContain(st('about.telemetry.unset'))
  })

  it('未选择与拒绝画出来不一样', async () => {
    await open(settings({ consent: 'unset', enabled: false }))
    const unsetChecked = radios().map((b) => b.getAttribute('aria-checked'))
    const unsetText = bodyText()
    await act(async () => {
      root.unmount()
    })
    host.remove()
    document.body.innerHTML = ''
    await open(settings({ consent: 'disabled', enabled: false }))
    expect(radios().map((b) => b.getAttribute('aria-checked'))).not.toEqual(unsetChecked)
    expect(bodyText()).not.toEqual(unsetText)
  })

  /**
   * 第四种情形：同意过，但同意的是**上一版采集范围**（后端升了
   * `CONSENT_VERSION`），此刻一个字节都不发。画成「已开启」是一句假话。
   */
  it('同意的是上一版采集范围：说清在等重新确认', async () => {
    await open(
      settings({
        consent: 'enabled',
        enabled: false,
        needs_reconsent: true,
        saved_consent_version: 1,
        consent_version: 2,
      }),
    )
    expect(bodyText()).toContain(st('about.telemetry.needsReconsent'))
  })
})

describe('可撤销：两个方向都写得回后端', () => {
  it('从同意改成拒绝：写 disabled', async () => {
    await open(settings({ consent: 'enabled', enabled: true }))
    setTelemetryEnabled(true)
    await act(async () => {
      seg(st('about.telemetry.optOut'))?.click()
    })
    expect(patchMock).toHaveBeenCalledWith('disabled', 'settings')
  })

  it('从拒绝改回同意：写 enabled', async () => {
    await open(settings({ consent: 'disabled', enabled: false }))
    patchMock.mockResolvedValue(settings())
    await act(async () => {
      seg(st('about.telemetry.optIn'))?.click()
    })
    expect(patchMock).toHaveBeenCalledWith('enabled', 'settings')
  })

  /**
   * 界面**说得出** unset，却**写不回** unset：表过态就是表过态，回到「还没问过」
   * 只会让首启询问再弹一次。控件上只有两档可点，这一条把它钉住。
   */
  it('界面上没有任何一个能写回「未选择」的入口', async () => {
    await open(settings({ consent: 'unset', enabled: false }))
    expect(radios().map((b) => b.textContent?.trim())).toEqual([
      st('about.telemetry.optIn'),
      st('about.telemetry.optOut'),
    ])
  })

  it('TAVOTTO_NO_TELEMETRY 关着时两档都点不动，并说明是谁关的', async () => {
    await open(settings({ consent: 'unset', enabled: false, hard_disabled: true }))
    expect(radios().every((b) => b.disabled)).toBe(true)
    expect(bodyText()).toContain(st('about.telemetry.hardDisabled'))
    await act(async () => {
      seg(st('about.telemetry.optIn'))?.click()
    })
    expect(patchMock).not.toHaveBeenCalled()
  })
})

describe('「会发送哪些数据」', () => {
  const disclosureBtn = () =>
    [...document.querySelectorAll('button')].find(
      (b) => b.textContent?.trim() === st('about.telemetry.detailsTitle'),
    ) as HTMLButtonElement

  it('默认折叠——它是一张清单，不该每次打开设置都占半屏', async () => {
    await open()
    expect(disclosureBtn().getAttribute('aria-expanded')).toBe('false')
    expect(document.querySelectorAll('[data-telemetry-event]')).toHaveLength(0)
  })

  /**
   * 展开后**每条事件都有一行**，且与闭集精确相等（顺序也比）。
   *
   * 判据认 `data-telemetry-event` 的取值集合，不认「文本里含某几个词」——
   * 后者在漏掉一条时照样绿，而漏掉的那一条正是用户没同意过的采集。
   * 闭集与后端 `EVENTS` 表的对齐由 `tests/test_telemetry_disclosure.py` 看住，
   * 这里只负责「闭集里的每一条真的渲染出来了」。
   */
  it('展开后逐条列出，与闭集一一对应', async () => {
    await open()
    await act(async () => {
      disclosureBtn().click()
    })
    const listed = [...document.querySelectorAll('[data-telemetry-event]')].map((el) =>
      el.getAttribute('data-telemetry-event'),
    )
    expect(listed).toEqual([...TELEMETRY_DISCLOSED_EVENTS])
    // 每一行都得有真的文案，空的 `<li>` 看起来像「这条不发」
    for (const el of document.querySelectorAll('[data-telemetry-event]')) {
      expect((el.textContent ?? '').trim().length).toBeGreaterThan(0)
    }
  })

  it('展开后「绝不发送」与标识说明也在，且不带字面 Markdown 星号', async () => {
    await open()
    await act(async () => {
      disclosureBtn().click()
    })
    const text = bodyText()
    expect(text).toContain(st('about.telemetry.never'))
    expect(text).toContain(st('about.telemetry.autoProps'))
    // 「跨启动稳定」这一句是这段话诚实性的关键，不许在精简里被删掉
    expect(text).toContain(st('about.telemetry.sendsPersist'))
    expect(text).not.toContain('**')
  })
})
