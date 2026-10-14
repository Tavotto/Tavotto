import { create } from 'zustand'

/**
 * 「意外错误」通知轨卡片的状态（PR A）。**纯内存**：不落盘、不进 telemetry、不带错误原文——
 * 卡片上只有一句固定的话，原文只在诊断的未捕获错误环里随发送包走。
 *
 * 谁能往里放：只有 `lib/unexpectedErrors.ts`（分类 / 去重 / 上界都在那里）。这里只管「轨上现在有没有一张」：
 * 同时最多 1 张，`show()` 在已有一张时是空操作。
 */
interface UnexpectedErrorState {
  /** 轨上的那一张；`token` 每次新出现 +1（通知轨据此排「最新靠近底边」并重建动画） */
  card: { token: number } | null
  show: () => boolean
  dismiss: () => void
}

let token = 0

export const useUnexpectedErrorStore = create<UnexpectedErrorState>((set, get) => ({
  card: null,
  show: () => {
    if (get().card) return false
    set({ card: { token: ++token } })
    return true
  },
  dismiss: () => set({ card: null }),
}))
