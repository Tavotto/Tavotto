import { create } from 'zustand'
import { fetchDiagSendCapability, type DiagSendCapability } from '@/lib/api'

/**
 * 「发送问题反馈」此刻可不可用（ADR 0118）。**默认关闭**：没取到 / 关着 / 回了别的东西都是 `null`，
 * 界面就不画入口、隐私摘要也不多说那一句。事实在后端（`engine/diagsend.enabled()`：开关 + 白名单主机），
 * 这里只是它的一份只读镜像；取这一份是本机请求，**不联网**。
 */
interface DiagSendState {
  capability: DiagSendCapability | null
  load: () => Promise<void>
}

export const useDiagSendStore = create<DiagSendState>((set) => ({
  capability: null,
  load: async () => {
    try {
      const c = await fetchDiagSendCapability()
      set({ capability: c?.enabled === true ? c : null })
    } catch {
      set({ capability: null })
    }
  },
}))
