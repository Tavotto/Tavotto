import { create } from 'zustand'
import { applyUpdate, checkUpdate, patchUpdateSettings, type UpdateStatus } from '@/lib/api'
import {
  checkDesktopUpdate,
  installDesktopUpdate,
  isDesktop,
  relaunchDesktop,
  type DesktopUpdateInfo,
} from '@/lib/desktop'

/**
 * 版本更新状态。启动时静默取一次（后端有 24h 节流，不会真的每次都联网），
 * 拿到 update_available 才在顶栏露出提示；用户在「设置 → 检查更新」里可以
 * 手动立即检查、关掉自动检查、或直接执行升级。
 *
 * 升级永远不静默进行：学术制图要可复现，版本什么时候变必须是用户按下按钮的结果。
 *
 * **两条互斥的升级通道**（不是两套 UI，是两种机制）：
 *   * 浏览器 / pip / pipx：Python updater（`/api/update/*`）跑 pip 装 wheel，
 *     升完进程里还是旧代码，靠 restart_required 提示重启；
 *   * 桌面壳：Tauri updater 下载签名过的安装包就地替换，装完 relaunch。
 *     后端在桌面模式直接把 `/api/update/*` 关掉（desktop: true），所以这条
 *     绝不会和上面那条同时动。
 */

/** 桌面更新走到哪一步；下载是唯一会持续一段时间的阶段，要给进度 */
export type DesktopPhase = 'idle' | 'checking' | 'downloading' | 'installed'

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

  /* ------------------------------ 桌面通道 ------------------------------ */
  desktopPhase: DesktopPhase
  /** 查到的新版本；null = 还没查 / 已是最新 */
  desktopUpdate: DesktopUpdateInfo | null
  /** 下载进度 0–1；null = 服务端没给 Content-Length（进度条走不确定态） */
  desktopProgress: number | null
  desktopError: string | null
  /** 查过一次没有新版（用来把「已是最新版本」和「还没查」分开） */
  desktopChecked: boolean
  checkDesktop: () => Promise<void>
  installDesktop: () => Promise<void>
  relaunch: () => Promise<void>
}

export const useUpdateStore = create<UpdateState>((set, get) => ({
  status: null,
  checking: false,
  applying: false,
  restartRequired: false,
  applyLog: null,
  dismissed: false,
  checkError: null,
  desktopPhase: 'idle',
  desktopUpdate: null,
  desktopProgress: null,
  desktopError: null,
  desktopChecked: false,

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

  checkDesktop: async () => {
    if (get().desktopPhase !== 'idle') return
    set({ desktopPhase: 'checking', desktopError: null })
    try {
      const info = await checkDesktopUpdate()
      set({ desktopUpdate: info, desktopChecked: true, dismissed: false })
    } catch (e) {
      // 离线是常态，但用户按下的按钮必须有下文——无声无息的按钮和坏掉没区别
      set({ desktopError: e instanceof Error ? e.message : '检查更新失败' })
    } finally {
      set({ desktopPhase: 'idle' })
    }
  },

  installDesktop: async () => {
    if (get().desktopPhase !== 'idle' || !get().desktopUpdate) return
    set({ desktopPhase: 'downloading', desktopProgress: 0, desktopError: null })
    try {
      await installDesktopUpdate((f) => set({ desktopProgress: f }))
      // 装完了但还跑着旧进程：与 pip 那条同一条纪律，重启才算换版本
      set({ desktopPhase: 'installed' })
    } catch (e) {
      set({
        desktopPhase: 'idle',
        desktopProgress: null,
        desktopError: e instanceof Error ? e.message : '下载或安装失败',
      })
    }
  },

  relaunch: async () => {
    try {
      await relaunchDesktop()
    } catch (e) {
      set({ desktopError: e instanceof Error ? e.message : '重启失败，请手动退出后重新打开' })
    }
  },
}))

/**
 * 启动时静默查一次。**桌面与浏览器各走各的**：桌面壳里后端的 updater 是
 * 关着的，查它只会拿到一句「已停用」；浏览器模式里 Tauri 的 check 根本不存在。
 */
export function checkUpdateOnStartup(): void {
  const s = useUpdateStore.getState()
  if (isDesktop()) void s.checkDesktop()
  else void s.check(false)
}
