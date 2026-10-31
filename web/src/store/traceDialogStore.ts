import { create } from 'zustand'
import type { ExecTraceOperation } from '@/lib/api'

/** 宽的执行记录卡要看的那一次任务 */
export interface TraceDialogTarget {
  kind?: ExecTraceOperation
  taskRef: string
  pj: string | null
  running: boolean
  settledKey?: unknown
}

interface TraceDialogState {
  target: TraceDialogTarget | null
  open: (target: TraceDialogTarget) => void
  close: () => void
}

/**
 * 从**会自己消失的浮层**（任务历史弹层、悬浮详情卡）里打开的执行记录卡：对话框不能挂在那一行里——
 * 点「查看执行记录」时弹层要收起，行跟着卸载，挂在行里的对话框也就一起没了（或者弹层压在对话框上面遮住 Waterfall）。
 * 所以状态放在这里，由一个常驻的 `TraceDialogHost` 画；浮层只管发「打开」并收起自己。
 */
export const useTraceDialogStore = create<TraceDialogState>((set) => ({
  target: null,
  open: (target) => set({ target }),
  close: () => set({ target: null }),
}))
