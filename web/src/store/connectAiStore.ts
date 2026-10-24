import { create } from 'zustand'
import { hasConnectionView } from '@/lib/api'
import { useAiStore } from '@/store/aiStore'
import { useUiStore } from '@/store/uiStore'

/**
 * 「连接一个 AI」小弹窗的开关（`ConnectAiHost` 在 App 里挂唯一一份）。
 *
 * 入口很多（修复入口没连 AI、助手面板空态、模型选择器底部「连接更多 AI」），但弹窗只有一个：它们都只是
 * `open({ reason })`。`reason: 'fix'` 只换一句标题（「连接一个 AI 来修复」）；连好之后的事（修复自动续跑）
 * 不在这里——续跑由 `aiFixStore` 订阅 `caps` 接走，这里只负责「开 / 关」和一个连好的回调。
 */
export type ConnectAiReason = 'general' | 'fix'

interface ConnectAiState {
  open: boolean
  reason: ConnectAiReason
  /** 连好（新连了一个、并且 caps 已刷新）时调用一次 */
  onConnected: (() => void) | null
  openDialog: (opts?: { reason?: ConnectAiReason; onConnected?: () => void }) => void
  close: () => void
}

export const useConnectAiStore = create<ConnectAiState>((set) => ({
  open: false,
  reason: 'general',
  onConnected: null,
  openDialog: (opts) => set({ open: true, reason: opts?.reason ?? 'general', onConnected: opts?.onConnected ?? null }),
  close: () => set({ open: false, onConnected: null }),
}))

/**
 * 所有「去连一个 AI」的入口走这一个函数。后端给了连接视图（`vendors` / `connections`）→ 开小弹窗；
 * 老后端没有 → 退回旧行为（打开设置 → 改图助手），不崩、不开一个空弹窗。
 */
export function requestConnectAi(opts?: { reason?: ConnectAiReason; onConnected?: () => void }): void {
  if (hasConnectionView(useAiStore.getState().caps)) useConnectAiStore.getState().openDialog(opts)
  else useUiStore.getState().setSettingsOpen(true, 'ai')
}
