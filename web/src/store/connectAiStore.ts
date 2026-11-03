import { create } from 'zustand'
import { effectiveConnection, hasConnectionView } from '@/lib/api'
import { useAiStore } from '@/store/aiStore'
import { useUiStore } from '@/store/uiStore'

/**
 * 「连接一个 AI」小弹窗的开关（`ConnectAiHost` 在 App 里挂唯一一份）。
 *
 * 入口很多（修复入口没连 AI、助手面板空态、模型选择器底部「连接更多 AI」），但弹窗只有一个：它们都只是
 * `open({ reason })`。`reason: 'fix'` 只换一句标题（「连接一个 AI 来修复」）；连好之后的事（修复自动续跑）
 * 不在这里——续跑由 `aiFixStore` 订阅 `caps` 接走，这里只负责「开 / 关」和一个连好的回调。
 */
export type ConnectAiReason = 'general' | 'fix' | 'convert'

interface ConnectAiState {
  open: boolean
  reason: ConnectAiReason
  /** 连好（新连了一个、并且 caps 已刷新）时调用一次 */
  onConnected: (() => void) | null
  /** 文案里的 {{name}}（reason=convert 时是要转换的脚本名） */
  name: string | null
  openDialog: (opts?: { reason?: ConnectAiReason; onConnected?: () => void; name?: string }) => void
  close: () => void
  /** 正在编辑的已存 Key 连接 id（「检查连接」打开它的地址 / 密钥 / 型号表单）；null = 没在编辑 */
  editing: string | null
  closeEditor: () => void
}

export const useConnectAiStore = create<ConnectAiState>((set) => ({
  open: false,
  reason: 'general',
  onConnected: null,
  name: null,
  openDialog: (opts) =>
    set({ open: true, reason: opts?.reason ?? 'general', onConnected: opts?.onConnected ?? null, name: opts?.name ?? null }),
  close: () => set({ open: false, onConnected: null, name: null }),
  editing: null,
  closeEditor: () => set({ editing: null }),
}))

/**
 * 所有「去连一个 AI」的入口走这一个函数。后端给了连接视图（`vendors` / `connections`）→ 开小弹窗；
 * 老后端没有 → 退回旧行为（打开设置 → 改图助手），不崩、不开一个空弹窗。
 */
export function requestConnectAi(opts?: { reason?: ConnectAiReason; onConnected?: () => void; name?: string }): void {
  if (hasConnectionView(useAiStore.getState().caps)) useConnectAiStore.getState().openDialog(opts)
  else useUiStore.getState().setSettingsOpen(true, 'ai')
}

/**
 * 失败卡的「检查连接」：打开**刚才派活用的那条连接**（`effectiveConnection`，与 `aiStore` 发送时同一个判据）的编辑表单，
 * 地址已填好——自定义中转站不在厂商列表里（`listedVendors` 排除了 custom），通用的「连接 AI」弹窗只能再新建一条，
 * 看不到也改不了出错的地址（Codex #1523 P2）。那条连接不是 Key 类（没有接口记录）→ 退回通用弹窗。
 */
export function requestCheckConnection(): void {
  const { caps, connection } = useAiStore.getState()
  // 用户选中的那条优先（即使它此刻不算 ready——凭据被拒的就是要改它的那条），否则与发送同一个 `effectiveConnection`
  const conn = hasConnectionView(caps)
    ? (caps?.connections?.find((c) => c.id === connection) ?? effectiveConnection(connection, caps))
    : null
  const endpoint = conn ? caps?.endpoints?.find((e) => e.id === conn.id) : undefined
  if (endpoint) useConnectAiStore.setState({ editing: endpoint.id })
  else requestConnectAi()
}
