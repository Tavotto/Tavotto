/**
 * 缺数据卡「复制过去再运行」点过之后卡片该落在哪一档（ADR 0173）。
 *
 * 后端载荷里的 `copy.state` 是「提议时」的状态；点了复制、被拒了（c2 / c3 / c4），这一次尝试里的那张卡要换档，
 * 而报告本身不变——所以记在这里，按「尝试 + 路径」取：重新运行出新尝试，键就对不上了，卡片自然回到后端给的那一档。
 * 只是显示态：谁可以复制、复制到哪里，永远由后端按改指表现场判（请求里没有源）。
 */
import { create } from 'zustand'
import type { CopyOverride } from '@/lib/preparationText'

interface InputCopyState {
  overrides: Record<string, CopyOverride>
  set: (key: string, value: CopyOverride) => void
  clear: () => void
}

export const useInputCopyStore = create<InputCopyState>((set) => ({
  overrides: {},
  set: (key, value) => set((s) => ({ overrides: { ...s.overrides, [key]: value } })),
  clear: () => set({ overrides: {} }),
}))
