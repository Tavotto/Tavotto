/**
 * 通知轨（`NotificationRail`）上「到时自己走」的两条——状态与操作提示——在指针停在上面、焦点落在它的
 * 按钮上时不走表；toast 卸载时把按住的原因一并放开；同一条 toast 换文字时原位换（宪法第二十三节）。
 *
 * 判据的主语是 **store 里那条消息还在不在**（`uiStore.status` / `useHintStore.current`），不是 DOM：
 * DOM 上的 toast 退场还要保活 90ms，拿它当主语会把「正在退场」读成「还在」。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { NotificationRail } from '@/components/StatusBar'
import { literal } from '@/i18n'
import { HINT_AUTO_DISMISS_MS, showHint, useHintStore } from '@/lib/onboarding/hints'
import { useOnboardingStore } from '@/store/onboardingStore'
import { STATUS_AUTO_DISMISS_MS, useUiStore } from '@/store/uiStore'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

let container: HTMLDivElement
let root: Root

const status = () => useUiStore.getState().status
const statusToast = () => container.querySelector<HTMLElement>('[data-status-live]')!.parentElement!.querySelector<HTMLElement>('[data-state]:not([data-onboarding-hint]):not([data-fast-edit-added-note])')
const hintToast = () => container.querySelector<HTMLElement>('[data-onboarding-hint]')

const pointer = (el: Element, type: 'pointerover' | 'pointerout') =>
  act(() => {
    el.dispatchEvent(new MouseEvent(type, { bubbles: true, relatedTarget: document.body }))
  })
const tick = (ms: number) => act(() => vi.advanceTimersByTime(ms))
const say = (text: string) => act(() => useUiStore.getState().setStatus(literal(text)))

beforeEach(() => {
  vi.useFakeTimers()
  localStorage.clear()
  useOnboardingStore.getState().resetHints()
  useHintStore.setState({ current: null, token: 0 })
  useUiStore.setState({ status: null, statusTone: 'info' })
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  act(() => root.render(<NotificationRail />))
})

afterEach(async () => {
  await act(async () => root.unmount())
  container.remove()
  act(() => useUiStore.getState().setStatus(null))
  useHintStore.getState().dismiss()
  vi.useRealTimers()
})

describe('状态 toast 的自动收起', () => {
  it('没人碰：到时自己走', () => {
    say('已导出')
    tick(STATUS_AUTO_DISMISS_MS - 1)
    expect(status()).not.toBeNull()
    tick(1)
    expect(status()).toBeNull()
  })

  it('指针停在上面不走表；离开后只数剩下的那截', () => {
    say('已导出')
    tick(1000)
    pointer(statusToast()!, 'pointerover')
    tick(60_000)
    expect(status(), '指针在上面的一分钟里不许消失').not.toBeNull()
    pointer(statusToast()!, 'pointerout')
    tick(STATUS_AUTO_DISMISS_MS - 1000 - 1)
    expect(status(), '离开后剩 3.5s，还差 1ms').not.toBeNull()
    tick(1)
    expect(status()).toBeNull()
  })

  it('指针停着时新状态顶掉旧状态：新的一条也不走', () => {
    say('已写回 fig3.py')
    pointer(statusToast()!, 'pointerover')
    say('已导出 figure_3.pdf')
    tick(60_000)
    expect(status()).not.toBeNull()
    pointer(statusToast()!, 'pointerout')
    tick(STATUS_AUTO_DISMISS_MS)
    expect(status()).toBeNull()
  })

  it('toast 卸载时放开按住的原因：指针停着时它被关掉，下一条状态照常走', () => {
    say('已导出')
    pointer(statusToast()!, 'pointerover')
    act(() => useUiStore.getState().setStatus(null))
    tick(200) // 退场保活 90ms 之后卸载
    expect(statusToast()).toBeNull()
    say('已保存')
    tick(STATUS_AUTO_DISMISS_MS)
    expect(status(), '上一条被关时指针还在上面，pointerleave 不会再来；不放开的话这条永远不走').toBeNull()
  })

  it('同一条 toast 换文字：原位换，DOM 文本是新字、旧句在 data-ghost 里', () => {
    say('已写回 fig3.py')
    say('已导出 figure_3.pdf')
    const box = statusToast()!.querySelector<HTMLElement>('.swap-text')!
    expect(box.textContent).toBe('已导出 figure_3.pdf')
    expect(box.dataset.ghost).toBe('已写回 fig3.py')
  })
})

describe('操作提示的自动收起', () => {
  it('焦点落在它的 × 上不走表；焦点离开后接着数', () => {
    act(() => {
      showHint('multi_select')
    })
    expect(useHintStore.getState().current).toBe('multi_select')
    const close = hintToast()!.querySelector<HTMLButtonElement>('button')!
    act(() => close.focus())
    tick(HINT_AUTO_DISMISS_MS * 3)
    expect(useHintStore.getState().current, '焦点在 × 上的时候不许消失').toBe('multi_select')
    act(() => close.blur())
    tick(HINT_AUTO_DISMISS_MS - 1)
    expect(useHintStore.getState().current).toBe('multi_select')
    tick(1)
    expect(useHintStore.getState().current).toBeNull()
  })

  it('指针停在提示上不走表', () => {
    act(() => {
      showHint('multi_select')
    })
    pointer(hintToast()!, 'pointerover')
    tick(HINT_AUTO_DISMISS_MS * 3)
    expect(useHintStore.getState().current).toBe('multi_select')
    pointer(hintToast()!, 'pointerout')
    tick(HINT_AUTO_DISMISS_MS)
    expect(useHintStore.getState().current).toBeNull()
  })
})
