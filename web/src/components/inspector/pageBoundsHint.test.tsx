/**
 * 「纸面尺寸」提示（`layout_page_bounds`）的锚点组件：只在锚点（右栏）存在时画、不进通知轨、
 * 悬停不走表、可关、一颗按钮把人带到「画布」页、reduced-motion 下关闭即卸载。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { NotificationRail } from '@/components/StatusBar'
import { HINT_AUTO_DISMISS_MS, showHint, startHintEngine, useHintStore } from '@/lib/onboarding/hints'
import { configureOnboardingPersistence, useOnboardingStore } from '@/store/onboardingStore'
import { useUiStore } from '@/store/uiStore'
import { PageBoundsHint } from './PageBoundsHint'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

let container: HTMLDivElement
let root: Root
let stop: (() => void) | null = null

const note = () => container.querySelector<HTMLElement>('[data-onboarding-hint="layout_page_bounds"]')
const tick = (ms: number) => act(() => vi.advanceTimersByTime(ms))

beforeEach(() => {
  vi.useFakeTimers()
  configureOnboardingPersistence(null)
  useOnboardingStore.getState().resetOnboarding()
  useHintStore.setState({ current: null, token: 0 })
  useUiStore.setState({ rightOpen: true, rightTab: 'properties', status: null })
  stop = startHintEngine()
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  act(() =>
    root.render(
      <>
        <PageBoundsHint />
        <NotificationRail />
      </>,
    ),
  )
})

afterEach(() => {
  act(() => root.unmount())
  container.remove()
  stop?.()
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('PageBoundsHint', () => {
  it('出现时只有一份：锚点组件画，通知轨不再画同一条', () => {
    act(() => void showHint('layout_page_bounds'))
    expect(container.querySelectorAll('[data-onboarding-hint]').length).toBe(1)
    expect(note()).not.toBeNull()
    expect(note()!.textContent).toContain('Figure')
  })

  it('通知轨的 polite live region 照样播报这条提示（可见 toast 不画），且只播一遍（#1220 评审 P2）', () => {
    act(() => void showHint('layout_page_bounds'))
    const live = [...container.querySelectorAll<HTMLElement>('[aria-live="polite"]')].filter((el) =>
      el.textContent?.includes('Figure'),
    )
    expect(live).toHaveLength(1)
    expect(live[0].classList.contains('sr-only')).toBe(true)
    expect(container.querySelectorAll('[data-onboarding-hint]').length).toBe(1) // 可见的只有锚点那份
    expect(note()!.closest('[aria-live]')).toBeNull() // 锚点组件自己不再 live
  })

  it('不抢键盘焦点', () => {
    act(() => void showHint('layout_page_bounds'))
    expect(note()!.contains(document.activeElement)).toBe(false)
  })

  it('指针停在上面不走表，移开后接着数', () => {
    act(() => void showHint('layout_page_bounds'))
    act(() => note()!.dispatchEvent(new MouseEvent('pointerover', { bubbles: true })))
    // React 的 onPointerEnter 由 pointerover 合成；再补一枪 pointerenter 兼容不同实现
    act(() => note()!.dispatchEvent(new PointerEvent('pointerenter')))
    tick(HINT_AUTO_DISMISS_MS * 2)
    expect(useHintStore.getState().current).toBe('layout_page_bounds')
    act(() => note()!.dispatchEvent(new MouseEvent('pointerout', { bubbles: true, relatedTarget: document.body })))
    act(() => note()!.dispatchEvent(new PointerEvent('pointerleave')))
    tick(HINT_AUTO_DISMISS_MS + 1)
    expect(useHintStore.getState().current).toBeNull()
  })

  it('「打开画布页」切到画布页签并收起提示', () => {
    act(() => void showHint('layout_page_bounds'))
    const open = note()!.querySelector<HTMLButtonElement>('button')!
    act(() => open.click())
    expect(useUiStore.getState().rightTab).toBe('canvas')
    expect(useHintStore.getState().current).toBeNull()
  })

  it('× 关掉后不重现', () => {
    act(() => void showHint('layout_page_bounds'))
    const buttons = note()!.querySelectorAll<HTMLButtonElement>('button')
    act(() => buttons[buttons.length - 1].click())
    tick(200)
    expect(note()).toBeNull()
    expect(showHint('layout_page_bounds')).toBe(false)
  })

  it('右栏收起 → 提示一并卸载（不留孤岛）', () => {
    act(() => void showHint('layout_page_bounds'))
    act(() => useUiStore.setState({ rightOpen: false }))
    tick(200)
    expect(note()).toBeNull()
  })

  it('reduced-motion：关闭即卸载，不留退场空档', () => {
    vi.stubGlobal('matchMedia', (q: string) => ({
      matches: q.includes('prefers-reduced-motion'),
      media: q,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
      onchange: null,
    }))
    act(() => void showHint('layout_page_bounds'))
    act(() => useHintStore.getState().dismiss())
    expect(note()).toBeNull() // 没有 tick：立即卸载
  })

  describe('焦点救援（#1220 评审 P1）', () => {
    let tabs: HTMLElement[]
    let unsub: () => void
    beforeEach(() => {
      tabs = ['properties', 'canvas'].map((id) => {
        const b = document.createElement('button')
        b.setAttribute('data-inspector-tab', id)
        b.setAttribute('aria-selected', String(useUiStore.getState().rightTab === id))
        b.getClientRects = (() => [{}] as unknown as DOMRectList) as HTMLElement['getClientRects']
        document.body.appendChild(b)
        return b
      })
      // 页签随 rightTab 变（真 Inspector 的 aria-selected 同源）
      unsub = useUiStore.subscribe((s) =>
        tabs.forEach((b) =>
          b.setAttribute('aria-selected', String(b.getAttribute('data-inspector-tab') === s.rightTab)),
        ),
      )
    })
    afterEach(() => {
      unsub()
      tabs.forEach((b) => b.remove())
    })

    it('键盘激活「打开画布页」后焦点落在「画布」页签，不是 body', async () => {
      act(() => void showHint('layout_page_bounds'))
      const open = note()!.querySelector<HTMLButtonElement>('button')!
      act(() => open.focus())
      await act(async () => {
        open.click()
        await Promise.resolve()
      })
      tick(300)
      expect(document.activeElement).toBe(tabs[1])
    })

    it('× 关闭后焦点落在当前选中的页签', async () => {
      act(() => void showHint('layout_page_bounds'))
      const buttons = note()!.querySelectorAll<HTMLButtonElement>('button')
      const close = buttons[buttons.length - 1]
      act(() => close.focus())
      await act(async () => {
        close.click()
        await Promise.resolve()
      })
      tick(300)
      expect(document.activeElement).toBe(tabs[0])
    })

    it('焦点不在卡片里时点击不移动焦点', async () => {
      const other = document.createElement('input')
      document.body.appendChild(other)
      act(() => void showHint('layout_page_bounds'))
      act(() => other.focus())
      const open = note()!.querySelector<HTMLButtonElement>('button')!
      await act(async () => {
        open.click()
        await Promise.resolve()
      })
      tick(300)
      expect(document.activeElement).toBe(other)
      other.remove()
    })
  })
})
