/**
 * 右下角的问题反馈卡（`FeedbackCorner`，2026-10-19）：意外错误与「上次没有正常退出」各一句标题 + 一句为什么 +
 * 一颗主按钮「发送问题反馈」+ ×；主动作只打开确认框（本机备包），绝不直接发。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  prepareDiagSend: vi.fn(),
  startDiagSend: vi.fn(),
}))

import { FeedbackCorner } from '@/components/FeedbackCorner'
import { t } from '@/i18n'
import { prepareDiagSend, startDiagSend } from '@/lib/api'
import { useDiagSendStore } from '@/store/diagSendStore'
import { useExitNoticeStore } from '@/store/exitNoticeStore'
import { useUnexpectedErrorStore } from '@/store/unexpectedErrorStore'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

let container: HTMLDivElement
let root: Root

const unexpectedCard = () => container.querySelector<HTMLElement>('[data-unexpected-error-card]')
const previousCard = () => container.querySelector<HTMLElement>('[data-previous-exit-card]')
const cards = () => container.querySelectorAll('section')
const sendButton = (el: HTMLElement) => el.querySelector<HTMLButtonElement>('[data-feedback-corner-send]')!
const closeButton = (el: HTMLElement) => el.querySelector<HTMLButtonElement>('[data-feedback-corner-close]')!
const tick = (ms: number) => act(() => vi.advanceTimersByTime(ms))

beforeEach(() => {
  vi.useFakeTimers()
  useDiagSendStore.setState({ capability: { enabled: true }, open: false })
  useUnexpectedErrorStore.getState().dismiss()
  useExitNoticeStore.setState({ engineExit: null, restarting: false, previous: null })
  vi.mocked(prepareDiagSend).mockClear()
  vi.mocked(startDiagSend).mockClear()
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  act(() => root.render(<FeedbackCorner />))
})

afterEach(async () => {
  await act(async () => root.unmount())
  container.remove()
  act(() => {
    useUnexpectedErrorStore.getState().dismiss()
    useExitNoticeStore.setState({ engineExit: null, restarting: false, previous: null })
  })
  useDiagSendStore.setState({ capability: null, open: false })
  vi.useRealTimers()
})

describe('意外错误卡', () => {
  it('标题 + 一句为什么 + 一颗主按钮 + ×；不带错误原文', () => {
    act(() => void useUnexpectedErrorStore.getState().show())
    const el = unexpectedCard()!
    expect(el.textContent).toContain(t('unexpectedError.text', { ns: 'workspace' }))
    expect(el.textContent).toContain(t('unexpectedError.body', { ns: 'workspace' }))
    const buttons = el.querySelectorAll('button')
    expect(buttons).toHaveLength(2)
    const primary = el.querySelectorAll('[data-variant="primary"]')
    expect(primary).toHaveLength(1)
    expect(primary[0].textContent).toBe(t('settings.diagnostics.send.cardButton', { ns: 'dialogs' }))
  })

  it('点主动作只 setOpen(true)（并收起这张卡）；prepare / start 的调用次数为 0', () => {
    act(() => void useUnexpectedErrorStore.getState().show())
    act(() => sendButton(unexpectedCard()!).click())
    expect(useDiagSendStore.getState().open).toBe(true)
    expect(useUnexpectedErrorStore.getState().card).toBeNull()
    expect(prepareDiagSend).not.toHaveBeenCalled()
    expect(startDiagSend).not.toHaveBeenCalled()
  })

  it('× 关掉：卡片 store 清空，不打开确认框', () => {
    act(() => void useUnexpectedErrorStore.getState().show())
    act(() => closeButton(unexpectedCard()!).click())
    expect(useUnexpectedErrorStore.getState().card).toBeNull()
    expect(useDiagSendStore.getState().open).toBe(false)
  })

  it('发送功能关着：就算 store 里有卡也不渲染', () => {
    useDiagSendStore.setState({ capability: null })
    act(() => {
      useUnexpectedErrorStore.getState().show()
      useExitNoticeStore.getState().showPrevious('app_unclean')
    })
    expect(cards()).toHaveLength(0)
  })
})

describe('上次没有正常退出卡', () => {
  it('标题 + 一句为什么 + 主按钮；点主动作只 setOpen(true)，prepare / start 为 0', () => {
    act(() => useExitNoticeStore.getState().showPrevious('engine_exited'))
    const el = previousCard()!
    expect(el.dataset.previousExitCard).toBe('engine_exited')
    expect(el.textContent).toContain(t('exitNotice.previous.text', { ns: 'workspace' }))
    expect(el.textContent).toContain(t('exitNotice.previous.body', { ns: 'workspace' }))
    act(() => sendButton(el).click())
    expect(useDiagSendStore.getState().open).toBe(true)
    expect(useExitNoticeStore.getState().previous).toBeNull()
    expect(prepareDiagSend).not.toHaveBeenCalled()
    expect(startDiagSend).not.toHaveBeenCalled()
  })

  it('× 关掉只清 store', () => {
    act(() => useExitNoticeStore.getState().showPrevious('app_unclean'))
    act(() => closeButton(previousCard()!).click())
    expect(useExitNoticeStore.getState().previous).toBeNull()
    expect(useDiagSendStore.getState().open).toBe(false)
  })
})

describe('确认框与排队的卡（Codex #1184）', () => {
  it('两类都在时点发送：两张一起收起，确认框上面不再冒出第二张', () => {
    act(() => {
      useExitNoticeStore.getState().showPrevious('app_unclean')
      useUnexpectedErrorStore.getState().show()
    })
    act(() => sendButton(unexpectedCard()!).click())
    tick(200)
    expect(useDiagSendStore.getState().open).toBe(true)
    expect(useUnexpectedErrorStore.getState().card).toBeNull()
    expect(useExitNoticeStore.getState().previous).toBeNull()
    expect(cards()).toHaveLength(0)
  })

  it('确认框开着时不渲染（store 不动）；关掉后卡回来', () => {
    useDiagSendStore.setState({ open: true })
    act(() => useExitNoticeStore.getState().showPrevious('app_unclean'))
    tick(200)
    expect(cards()).toHaveLength(0)
    expect(useExitNoticeStore.getState().previous).not.toBeNull()
    act(() => useDiagSendStore.setState({ open: false }))
    tick(200)
    expect(previousCard()).not.toBeNull()
  })
})

describe('同时只有一张', () => {
  it('两类都在：先说意外错误；它关掉后才轮到上次没正常退出', () => {
    act(() => {
      useExitNoticeStore.getState().showPrevious('app_unclean')
      useUnexpectedErrorStore.getState().show()
    })
    tick(200)
    expect(cards()).toHaveLength(1)
    expect(unexpectedCard()).not.toBeNull()
    act(() => closeButton(unexpectedCard()!).click())
    tick(200)
    expect(cards()).toHaveLength(1)
    expect(previousCard()).not.toBeNull()
  })
})

describe('不盖住准备引导卡（Codex #1184）', () => {
  const rect = (r: Partial<DOMRect>) => ({ x: 0, y: 0, top: 0, left: 0, right: 0, bottom: 0, width: 0, height: 0, toJSON: () => ({}), ...r }) as DOMRect
  const CARD_H = 120
  let restore: () => void
  beforeEach(() => {
    // 卡自己的实测高度（jsdom 量不到几何）
    const orig = HTMLElement.prototype.getBoundingClientRect
    HTMLElement.prototype.getBoundingClientRect = function (this: HTMLElement) {
      return this.matches('section') ? rect({ height: CARD_H, width: 320 }) : orig.call(this)
    }
    restore = () => (HTMLElement.prototype.getBoundingClientRect = orig)
  })
  afterEach(() => {
    restore()
    document.querySelectorAll('[data-prep-card]').forEach((e) => e.remove())
  })
  const addPrep = async (r: Partial<DOMRect>) => {
    const prep = document.createElement('div')
    prep.setAttribute('data-prep-card', '')
    prep.getBoundingClientRect = () => rect(r)
    await act(async () => {
      document.body.appendChild(prep)
      await Promise.resolve()
    })
    return prep
  }
  const corner = () => container.querySelector<HTMLElement>('[data-feedback-corner]')!

  it('上面放得下：抬到准备卡上方；准备卡消失后回到默认位置', async () => {
    Object.assign(window, { innerWidth: 1024, innerHeight: 768 })
    act(() => void useUnexpectedErrorStore.getState().show())
    expect(corner().style.bottom).toBe('')
    const prep = await addPrep({ left: 612, right: 1012, top: 500, bottom: 756, width: 400, height: 256 })
    expect(corner().style.bottom).toBe(`${768 - 500 + 8}px`)
    await act(async () => {
      prep.remove()
      await Promise.resolve()
    })
    expect(corner().style.bottom).toBe('')
  })

  it('窗口矮、上面放不下：改放到准备卡左侧贴底，整张卡仍在窗口里', async () => {
    Object.assign(window, { innerWidth: 1024, innerHeight: 560 })
    act(() => void useUnexpectedErrorStore.getState().show())
    await addPrep({ left: 612, right: 1012, top: 100, bottom: 548, width: 400, height: 448 })
    expect(corner().style.bottom).toBe('')
    expect(corner().style.right).toBe(`${1024 - 612 + 8}px`)
  })

  it('上面、左边都放不下（窗口太小）：留在默认位置，不把卡推出窗口', async () => {
    Object.assign(window, { innerWidth: 700, innerHeight: 400 })
    act(() => void useUnexpectedErrorStore.getState().show())
    await addPrep({ left: 288, right: 688, top: 40, bottom: 388, width: 400, height: 348 })
    expect(corner().style.bottom).toBe('')
    expect(corner().style.right).toBe('')
  })

  it('准备卡收起成角标（data-prep-pill）也避让', async () => {
    Object.assign(window, { innerWidth: 1024, innerHeight: 768 })
    act(() => void useUnexpectedErrorStore.getState().show())
    const pill = document.createElement('div')
    pill.setAttribute('data-prep-pill', '')
    pill.getBoundingClientRect = () => rect({ left: 800, right: 1012, top: 716, bottom: 756, width: 212, height: 40 })
    await act(async () => {
      document.body.appendChild(pill)
      await Promise.resolve()
    })
    expect(corner().style.bottom).toBe(`${768 - 716 + 8}px`)
    pill.remove()
  })

  it('准备卡不在这张卡的水平范围里（属性栏开着、画布在左边）：不动', async () => {
    Object.assign(window, { innerWidth: 1400, innerHeight: 900 })
    act(() => void useUnexpectedErrorStore.getState().show())
    await addPrep({ left: 600, right: 1000, top: 600, bottom: 880, width: 400, height: 280 })
    expect(corner().style.bottom).toBe('')
    expect(corner().style.right).toBe('')
  })
})
