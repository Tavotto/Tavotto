/**
 * 动画卡提示的卡片（ADR 0177）：谁画它（卡片画、通知轨不画、活动区照播）、三个出口（× / 按钮 / 让位）。
 * 小动画本身（`scenes.tsx` + vendor/stageshow）在 jsdom 里没有盒子，换成一个桩；真浏览器里看它的是 `e2e/hint-cards.spec.ts`。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { NotificationRail } from '@/components/StatusBar'
import { emitActivity } from '@/lib/activity'
import {
  CARD_AUTO_DISMISS_MS,
  hintDismissTimer,
  resetHintSessionForTest,
  startHintEngine,
  useHintStore,
} from '@/lib/onboarding/hints'
import { useDialogLayerStore } from '@/store/dialogLayerStore'
import { useDiagSendStore } from '@/store/diagSendStore'
import { configureOnboardingPersistence, useOnboardingStore } from '@/store/onboardingStore'
import { useUiStore } from '@/store/uiStore'
import { useUnexpectedErrorStore } from '@/store/unexpectedErrorStore'
import { useWorkspaceStore } from '@/store/workspace'
import { HintCard } from './HintCard'

const fitStage = vi.hoisted(() => vi.fn())
vi.mock('@/store/zoomToSelection', async (orig) => ({ ...(await orig<object>()), fitStage }))
vi.mock('./scenes', () => ({
  default: ({ kind, play }: { kind: string; play: string }) => <i data-scene-stub={kind} data-play={play} />,
}))

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

let container: HTMLDivElement
let root: Root
let stop: (() => void) | null = null

const card = () => container.querySelector<HTMLElement>('[data-hint-card]')
const show = (kind: 'fit_canvas' | 'edge_crop') =>
  act(() => useHintStore.setState((s) => ({ current: kind, token: s.token + 1 })))

beforeEach(async () => {
  vi.useFakeTimers()
  fitStage.mockReset()
  configureOnboardingPersistence(null)
  useOnboardingStore.getState().resetOnboarding()
  resetHintSessionForTest()
  useHintStore.setState({ current: null, token: 0 })
  useDialogLayerStore.setState({ open: 0 })
  useUnexpectedErrorStore.setState({ card: null })
  useDiagSendStore.setState({ open: false })
  useUiStore.setState({ rightOpen: true, status: null })
  useWorkspaceStore.getState().clear()
  stop = startHintEngine()
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  await act(async () => {
    root.render(
      <>
        <HintCard />
        <NotificationRail />
      </>,
    )
  })
})

afterEach(() => {
  act(() => root.unmount())
  container.remove()
  stop?.()
  vi.useRealTimers()
})

describe('HintCard', () => {
  it('没有卡片种类的提示时什么都不画', () => {
    expect(card()).toBeNull()
  })

  it('卡片画一份：一句话、小动画的位置；通知轨不再画同一条，活动区照播', async () => {
    emitActivity({ kind: 'panel.edge_dragged', outcome: 'resized' })
    await act(async () => {})
    expect(card()?.getAttribute('data-hint-card')).toBe('edge_crop')
    expect(card()!.querySelector('[data-hint-card-text]')!.textContent).toMatch(/Alt|⌥/)
    expect(card()!.querySelector('[data-scene-stub="edge_crop"]')).not.toBeNull()
    // 拖边裁剪没有「一步替用户做完」的动作：不带按钮
    expect(card()!.querySelector('[data-hint-card-action]')).toBeNull()
    expect(container.querySelectorAll('[data-onboarding-hint]').length).toBe(0)
    expect(container.textContent!.match(/裁剪|crop/gi)!.length).toBe(2) // 卡片一份 + 读屏活动区一份
  })

  it('× = 这条别再提：卡收起、账上记 done', async () => {
    emitActivity({ kind: 'panel.edge_dragged', outcome: 'resized' })
    await act(async () => {})
    act(() => card()!.querySelector<HTMLElement>('[data-hint-card-close]')!.click())
    expect(useHintStore.getState().current).toBeNull()
    expect(useOnboardingStore.getState().hintStats.edge_crop?.done).toBe(true)
  })

  it('「适应画布」按钮只在回正那张卡上，点了就调 fitStage', async () => {
    show('fit_canvas')
    await act(async () => {})
    const btn = card()!.querySelector<HTMLElement>('[data-hint-card-action]')
    expect(btn).not.toBeNull()
    act(() => btn!.click())
    expect(fitStage).toHaveBeenCalledTimes(1)
  })

  it('有模态对话框 / 反馈卡在场时让位：不画，但不收起，之后回来', async () => {
    // 走真的出卡路径：带着 30 秒自动收起的表
    emitActivity({ kind: 'panel.edge_dragged', outcome: 'resized' })
    await act(async () => {})
    act(() => useDialogLayerStore.setState({ open: 1 }))
    act(() => vi.advanceTimersByTime(400))
    expect(card()).toBeNull()
    expect(useHintStore.getState().current).toBe('edge_crop')
    act(() => useDialogLayerStore.setState({ open: 0 }))
    expect(card()).not.toBeNull()
    act(() => {
      useDiagSendStore.setState({ capability: { enabled: true } as never, open: false })
      useUnexpectedErrorStore.setState({ card: { token: 1 } })
    })
    act(() => vi.advanceTimersByTime(400))
    expect(card()).toBeNull()
    expect(useHintStore.getState().current).toBe('edge_crop')
    // 让位期间不走表：盖了很久也不会在用户没看见的时候自己收起
    act(() => vi.advanceTimersByTime(CARD_AUTO_DISMISS_MS * 3))
    expect(useHintStore.getState().current).toBe('edge_crop')
    act(() => useUnexpectedErrorStore.setState({ card: null }))
    expect(card()).not.toBeNull()
  })

  it('焦点在 × 上把卡关掉：hold 放开，下一张卡照常到时自己收起', async () => {
    emitActivity({ kind: 'panel.edge_dragged', outcome: 'resized' })
    await act(async () => {})
    const close = card()!.querySelector<HTMLElement>('[data-hint-card-close]')!
    act(() => close.focus())
    act(() => card()!.dispatchEvent(new Event('pointerover', { bubbles: true })))
    act(() => close.click())
    act(() => vi.advanceTimersByTime(400))
    expect(card()).toBeNull()
    show('fit_canvas')
    await act(async () => {})
    act(() => hintDismissTimer.start(CARD_AUTO_DISMISS_MS, () => useHintStore.getState().dismiss()))
    expect(hintDismissTimer.running).toBe(true)
    act(() => vi.advanceTimersByTime(CARD_AUTO_DISMISS_MS))
    expect(useHintStore.getState().current).toBeNull()
  })
})
