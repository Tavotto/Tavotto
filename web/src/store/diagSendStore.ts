import { create } from 'zustand'
import { fetchDiagSendCapability, type DiagSendCapability } from '@/lib/api'

/**
 * 「发送问题反馈」此刻可不可用（ADR 0118）。**没取到即关**（引擎侧发行版默认开启，`TAVOTTO_NO_DIAG_UPLOAD=1` 可关）：没取到 / 关着 / 回了别的东西都是 `null`，
 * 界面就不画入口、隐私摘要也不多说那一句。事实在后端（`engine/diagsend.enabled()`：两个关闭开关 + 白名单主机），
 * 这里只是它的一份只读镜像；取这一份是本机请求，**不联网**。
 */
interface DiagSendState {
  capability: DiagSendCapability | null
  /** 「发送问题反馈」对话框是否开着（全应用唯一一个，由 `DiagnosticsSendHost` 挂载；入口只是把它打开） */
  open: boolean
  setOpen: (open: boolean) => void
  /** AI 修复会话替用户起草的说明：只预填进说明框，由用户看过、改过、点「发送」才发（ADR 0118 / 0149）；打开时被对话框取走并清空 */
  presetNote: string
  /** 带着 AI 起草的说明打开对话框（仍是本机备包，不发送） */
  openWithNote: (note: string) => void
  /** 取走预填的说明（对话框打开时调） */
  takePresetNote: () => string
  /** 本页累计发送成功的次数：AI 修复的「发送」卡据此知道用户真的点了发送 */
  sentCount: number
  markSent: () => void
  load: () => Promise<void>
}

export const useDiagSendStore = create<DiagSendState>((set, get) => ({
  capability: null,
  open: false,
  setOpen: (open) => set({ open }),
  presetNote: '',
  openWithNote: (note) => set({ open: true, presetNote: note }),
  takePresetNote: () => {
    const note = get().presetNote
    if (note) set({ presetNote: '' })
    return note
  },
  sentCount: 0,
  markSent: () => set((s) => ({ sentCount: s.sentCount + 1 })),
  load: async () => {
    try {
      const c = await fetchDiagSendCapability()
      set({ capability: c?.enabled === true ? c : null })
    } catch {
      set({ capability: null })
    }
  },
}))
