import { create } from 'zustand'
import { checkAiConnection, effectiveConnection, hasConnectionView, type AiConnection } from '@/lib/api'
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
  /** 编辑表单打开时先聚焦哪一格（「重填密钥」→ key，「改模型」→ model，「改地址」→ url）；null = 不特意聚焦 */
  editFocus: EditFocus | null
  closeEditor: () => void
}

export type EditFocus = 'key' | 'model' | 'url'

export const useConnectAiStore = create<ConnectAiState>((set) => ({
  open: false,
  reason: 'general',
  onConnected: null,
  name: null,
  openDialog: (opts) =>
    set({ open: true, reason: opts?.reason ?? 'general', onConnected: opts?.onConnected ?? null, name: opts?.name ?? null }),
  close: () => set({ open: false, onConnected: null, name: null }),
  editing: null,
  editFocus: null,
  closeEditor: () => set({ editing: null, editFocus: null }),
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
export function requestCheckConnection(focus?: EditFocus): void {
  // 用户选中的那条优先（即使它此刻不算 ready——凭据被拒的就是要改它的那条），否则与发送同一个 `effectiveConnection`
  const conn = currentConnection()
  const endpoint = conn ? useAiStore.getState().caps?.endpoints?.find((e) => e.id === conn.id) : undefined
  if (endpoint) useConnectAiStore.setState({ editing: endpoint.id, editFocus: focus ?? null })
  else requestConnectAi()
}

/** 失败卡此刻指的那条连接（与 `requestCheckConnection` 同一个判据：用户选中的优先，否则与发送同一个 `effectiveConnection`） */
export function currentConnection(): AiConnection | null {
  const { caps, connection } = useAiStore.getState()
  if (!hasConnectionView(caps)) return null
  return caps?.connections?.find((c) => c.id === connection) ?? effectiveConnection(connection, caps)
}

/** 一次「检查连接」的结局：`ok` = 现在是 ready；否则 `reason` 是探测原因闭集 / 连接的 error 码（没有 = 通用一句） */
export interface ConnectionCheckResult {
  ok: boolean
  reason: string | null
}

/**
 * 重新探测一条 Key 连接（`POST /api/ai/connections/<id>/check`），再重读能力表（通过了界面才跟着变）。
 * 永远有结局：请求没到后端 / 404（不是 Key 类）都落成 `{ok:false}`，由调用方给「修改连接」。
 */
export async function runConnectionCheck(id: string): Promise<ConnectionCheckResult> {
  let res
  try {
    res = await checkAiConnection(id)
  } catch {
    return { ok: false, reason: 'network' }
  }
  try {
    await useAiStore.getState().loadCaps(true)
  } catch {
    /* 重读失败：下面照常按这次响应里的连接视图下结论 */
  }
  const conn = res.connection
  if (conn?.state === 'ready') return { ok: true, reason: null }
  const probe = res.probe
  // 断网 / 缺型号时记录没动（checked=false）：说「连不上」，别把旧记录里的原因当成这次的结论
  if (probe && !probe.checked) return { ok: false, reason: 'network' }
  return { ok: false, reason: conn?.error_reason ?? probe?.reason ?? conn?.error ?? null }
}

/** 连接卡上的「改地址 / 改模型 / 重填密钥」：打开**这一条**连接的编辑表单；它没有接口记录（目录厂商没存过）→ 返回 false，由调用方就地处理 */
export function requestEditConnection(id: string, focus?: EditFocus): boolean {
  if (!useAiStore.getState().caps?.endpoints?.some((e) => e.id === id)) return false
  useConnectAiStore.setState({ editing: id, editFocus: focus ?? null })
  return true
}
