import { useDiagSendStore } from './diagSendStore'
import { useExitNoticeStore } from './exitNoticeStore'
import { useUnexpectedErrorStore } from './unexpectedErrorStore'

type Parts = { diagEnabled: boolean; sendOpen: boolean; unexpected: boolean; previous: boolean }

/**
 * 窗口右下角此刻有没有反馈卡（意外错误 / 上次没正常退出）：与 `components/FeedbackCorner` 决定「画哪一张」的
 * 条件同一个式子——发送功能可用、发送窗口没开、两张卡至少有一张。动画卡提示（ADR 0177）与它同角，
 * 在场时让位：提示引擎不出新卡，已出的那张不画、不走表。
 */
export const feedbackCornerShowing = (p: Parts): boolean => p.diagEnabled && !p.sendOpen && (p.unexpected || p.previous)

export function feedbackCornerBusy(): boolean {
  const d = useDiagSendStore.getState()
  return feedbackCornerShowing({
    diagEnabled: d.capability !== null,
    sendOpen: d.open,
    unexpected: !!useUnexpectedErrorStore.getState().card,
    previous: !!useExitNoticeStore.getState().previous,
  })
}

export function useFeedbackCornerBusy(): boolean {
  const diagEnabled = useDiagSendStore((s) => s.capability !== null)
  const sendOpen = useDiagSendStore((s) => s.open)
  const unexpected = useUnexpectedErrorStore((s) => !!s.card)
  const previous = useExitNoticeStore((s) => !!s.previous)
  return feedbackCornerShowing({ diagEnabled, sendOpen, unexpected, previous })
}
