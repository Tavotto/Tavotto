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
