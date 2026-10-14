import { create } from 'zustand'
import type { EngineExitKind, PreviousExit } from '@/lib/desktop'

/**
 * 退出相关两张通知轨卡片的状态（ADR 0129）。**纯内存**：不落盘、不进 telemetry、不带任何错误文本。
 *
 * - `engineExit`：本次会话里本机引擎已意外退出（壳发事件 / 回读得来）。「重新启动」修复卡由它驱动，
 *   **不受发送开关控制**（引擎没了，谁都需要这颗按钮）；它同时让「连接断了，正在重连…」这句假话让位。
 * - `previous`：上次会话没有正常退出 / 引擎退出后重启（壳 `take_previous_exit` 取走的一次性结果）。
 *   只在 `lib/exitNotices.ts` 确认发送入口可用（`diagSendStore.capability !== null`）后才登记；
 *   卡片的主动作只打开现有发送确认框，不备包、不上传。
 *
 * 谁能往里放：只有 `lib/exitNotices.ts`。
 */
interface ExitNoticeState {
  engineExit: EngineExitKind | null
  /** 「重新启动」已点下、壳还没把页面换掉：按钮转圈，不许再点 */
  restarting: boolean
  previous: { kind: PreviousExit; token: number } | null
  setEngineExit: (kind: EngineExitKind) => void
  setRestarting: (restarting: boolean) => void
  showPrevious: (kind: PreviousExit) => void
  dismissPrevious: () => void
}

let token = 0

export const useExitNoticeStore = create<ExitNoticeState>((set, get) => ({
  engineExit: null,
  restarting: false,
  previous: null,
  // 同一次退出事件 + 回读会各来一次：已有就不动
  setEngineExit: (kind) => {
    if (get().engineExit) return
    set({ engineExit: kind })
  },
  setRestarting: (restarting) => set({ restarting }),
  showPrevious: (kind) => {
    if (get().previous) return
    set({ previous: { kind, token: ++token } })
  },
  dismissPrevious: () => set({ previous: null }),
}))
