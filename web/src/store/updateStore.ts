import { create } from 'zustand'
import { applyUpdate, checkUpdate, patchUpdateSettings, type UpdateStatus } from '@/lib/api'

/**
 * 版本更新状态。启动时静默取一次（后端有 24h 节流，不会真的每次都联网），
 * 拿到 update_available 才在顶栏露出提示；用户在「设置 → 检查更新」里可以
 * 手动立即检查、关掉自动检查、或直接执行升级。
 *
 * 升级永远不静默进行：学术制图要可复现，版本什么时候变必须是用户按下按钮的结果。
 */
interface UpdateState {
  status: UpdateStatus | null
  checking: boolean
  applying: boolean
  /** 升级成功后为 true——进程还跑着旧代码，界面要一直提示重启 */
  restartRequired: boolean
  applyLog: string | null
  /** 用户手动关掉本次顶栏提示（不改设置，仅本次会话） */
  dismissed: boolean
  /** 手动「立即检查」在 fetch 层就失败时的提示（连不上后端等）；自动检查不写 */
  checkError: string | null
  check: (force?: boolean) => Promise<void>
  apply: () => Promise<void>
  setAutoCheck: (v: boolean) => Promise<void>
  dismiss: () => void
}

export const useUpdateStore = create<UpdateState>((set, get) => ({
  status: null,
  checking: false,
  applying: false,
  restartRequired: false,
  applyLog: null,
  dismissed: false,
  checkError: null,

  check: async (force = false) => {
    if (get().checking) return
    set({ checking: true, ...(force ? { checkError: null } : {}) })
    try {
      const status = await checkUpdate(force)
      set({ status, checkError: null, dismissed: force ? false : get().dismissed })
    } catch (e) {
      // 自动检查失败保持安静（离线是常态，顶栏什么都不显示即可）；
      // 手动点「立即检查」必须有下文——无声无息的按钮和坏掉没有区别。
      // 走到这里说明 fetch 层就失败了（后端联网失败会以 status.error 正常返回）
      if (force) {
        set({
          checkError:
            e instanceof Error ? `检查失败：${e.message}` : '检查失败：无法连接本地服务',
        })
      }
    } finally {
      set({ checking: false })
    }
  },

  apply: async () => {
    if (get().applying) return
    set({ applying: true, applyLog: null })
    try {
      const res = await applyUpdate()
      set({ applyLog: res.log, restartRequired: res.restart_required })
    } catch (e) {
      set({ applyLog: e instanceof Error ? e.message : '升级失败' })
    } finally {
      set({ applying: false })
    }
  },

  setAutoCheck: async (v) => {
    await patchUpdateSettings({ auto_check: v })
    const status = get().status
    if (status) set({ status: { ...status, auto_check: v } })
  },

  dismiss: () => set({ dismissed: true }),
}))
