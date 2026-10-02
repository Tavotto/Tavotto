import { create } from 'zustand'
import { t, type UiMessage } from '@/i18n'
import { captureTelemetry } from '@/lib/telemetry'
import {
  ApiError,
  applyUpdate,
  backendErrorMsg,
  checkUpdate,
  patchUpdateSettings,
  type UpdateStatus,
} from '@/lib/api'
import {
  checkDesktopUpdate,
  installDesktopUpdate,
  isDesktop,
  relaunchDesktop,
  type DesktopUpdateInfo,
} from '@/lib/desktop'
import { readDismissedUpdate, writeDismissedUpdate } from '@/lib/updateNotice'

/**
 * 版本更新状态。启动时静默取一次（后端有 24h 节流，不会真的每次都联网），
 * 拿到 update_available 才露出提示——启动时弹一次 `UpdateNoticeDialog`
 * （「稍后」按版本记住）+「⋯」上的圆点；用户在
 * 「设置 → 检查更新」里可以手动立即检查、关掉自动检查、或直接执行升级。
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

// A check overlapping a preference write may carry the old auto_check snapshot.
// Only that field is protected; version/check results still settle normally.
let autoCheckWriteVersion = 0

interface UpdateState {
  status: UpdateStatus | null
  checking: boolean
  applying: boolean
  /** 升级成功后为 true——进程还跑着旧代码，界面要一直提示重启 */
  restartRequired: boolean
  applyLog: string | null
  /**
   * 上一次 `apply()` 的结局。**与 applyLog 是两件事**：失败时后端把原因放在
   * 响应体的 `log` 里、`error` 字段是空的，于是 jsonFetch 只留下「HTTP 500」，
   * 界面把它和成功的安装日志画成同一片灰字——用户看不出装没装上，也就不知道
   * 该不该再点一次（审计 T48「失败保留重试路径」）。
   */
  applyFailed: boolean
  /**
   * 用户对哪个版本点过「稍后」。**按版本记，不按会话记**：同一版本下次启动
   * 不再催，出了更新的版本才再提示。落在 localStorage（本机偏好，不进文档），
   * 启动时读回来；「⋯」上的圆点不受它影响——那是安静的提醒，弹窗才是打招呼。
   */
  dismissedVersion: string | null
  /** 手动「立即检查」在 fetch 层就失败时的提示（连不上后端等）；自动检查不写 */
  checkError: string | null
  check: (force?: boolean) => Promise<void>
  apply: () => Promise<void>
  autoCheckSaving: boolean
  autoCheckFailure: { value: boolean; message: UiMessage } | null
  setAutoCheck: (v: boolean) => Promise<void>
  /** 对这个版本说「稍后」 */
  dismiss: (version: string) => void

  /* ------------------------------ 桌面通道 ------------------------------ */
  desktopPhase: DesktopPhase
  /** 查到的新版本；null = 还没查 / 已是最新 */
  desktopUpdate: DesktopUpdateInfo | null
  /** 下载进度 0–1；null = 服务端没给 Content-Length（进度条走不确定态） */
  desktopProgress: number | null
  desktopError: string | null
  /** 查过一次没有新版（用来把「已是最新版本」和「还没查」分开） */
  desktopChecked: boolean
  /**
   * 上一次**成功**查询完成的时刻。界面上的「最新」只能说到这一刻为止——
   * 它是那次查询的回答，不是对发布状态的实时核验（审计 T48）。查询失败时
   * 不更新：拿一个失败的时刻去支撑「那时是最新的」就是在编。
   */
  desktopCheckedAtMs: number | null
  checkDesktop: () => Promise<void>
  installDesktop: () => Promise<void>
  relaunching: boolean
  relaunchFailed: boolean
  relaunch: () => Promise<void>
}

export const useUpdateStore = create<UpdateState>((set, get) => ({
  status: null,
  checking: false,
  applying: false,
  restartRequired: false,
  applyLog: null,
  applyFailed: false,
  dismissedVersion: readDismissedUpdate(),
  checkError: null,
  autoCheckSaving: false,
  autoCheckFailure: null,
  desktopPhase: 'idle',
  desktopUpdate: null,
  desktopProgress: null,
  desktopError: null,
  desktopChecked: false,
  desktopCheckedAtMs: null,
  relaunching: false,
  relaunchFailed: false,

  check: async (force = false) => {
    if (get().checking) return
    const writeVersion = autoCheckWriteVersion
    set({ checking: true, ...(force ? { checkError: null } : {}) })
    try {
      const status = await checkUpdate(force)
      const current = get()
      const overlapsWrite = writeVersion !== autoCheckWriteVersion || current.autoCheckSaving
      set({
        status:
          overlapsWrite && current.status
            ? { ...status, auto_check: current.status.auto_check }
            : status,
        checkError: null,
      })
    } catch (e) {
      // 自动检查失败保持安静（离线是常态，顶栏什么都不显示即可）；
      // 手动点「立即检查」必须有下文——无声无息的按钮和坏掉没有区别。
      // 走到这里说明 fetch 层就失败了（后端联网失败会以 status.error 正常返回）
      if (force) {
        set({
          checkError:
            e instanceof Error
              ? t('update.checkFailed', { ns: 'errors', error: e.message })
              : t('update.checkFailedOffline', { ns: 'errors' }),
        })
      }
    } finally {
      set({ checking: false })
    }
  },

  apply: async () => {
    if (get().applying) return
    set({ applying: true, applyLog: null, applyFailed: false })
    try {
      const res = await applyUpdate()
      set({ applyLog: res.log, restartRequired: res.restart_required, applyFailed: !res.ok })
    } catch (e) {
      // 后端失败走 500，原因在响应体的 `log` 里而不是 `error`——不取出来的话
      // 用户读到的是「HTTP 500」，pip 真正说的那句话被扔掉了
      const log = e instanceof ApiError && typeof e.body.log === 'string' ? e.body.log : null
      set({
        applyLog:
          log ?? (e instanceof Error ? e.message : t('update.applyFailed', { ns: 'errors' })),
        applyFailed: true,
      })
    } finally {
      set({ applying: false })
    }
  },

  setAutoCheck: async (v) => {
    if (get().autoCheckSaving || !get().status) return
    autoCheckWriteVersion++
    set({ autoCheckSaving: true, autoCheckFailure: null })
    try {
      const saved = await patchUpdateSettings({ auto_check: v })
      const status = get().status
      if (status) set({ status: { ...status, auto_check: saved.auto_check } })
    } catch (e) {
      // Retain the last acknowledged value, not an optimistic toggle. Keep the
      // requested choice for retry and the descriptor for live language changes.
      set({ autoCheckFailure: { value: v, message: backendErrorMsg(e) } })
    } finally {
      autoCheckWriteVersion++
      set({ autoCheckSaving: false })
    }
  },

  dismiss: (version) => {
    writeDismissedUpdate(version)
    set({ dismissedVersion: version })
  },

  checkDesktop: async () => {
    if (get().desktopPhase !== 'idle') return
    set({ desktopPhase: 'checking', desktopError: null })
    try {
      const info = await checkDesktopUpdate()
      set({
        desktopUpdate: info,
        desktopChecked: true,
        desktopCheckedAtMs: Date.now(),
      })
    } catch (e) {
      // 离线是常态，但用户按下的按钮必须有下文——无声无息的按钮和坏掉没区别
      set({
        desktopError: e instanceof Error ? e.message : t('update.desktopCheckFailed', { ns: 'errors' }),
      })
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
      // 匿名用量统计：**装成功之后**才记（下载失败 / 用户中途取消都不算）。
      // 桌面这条通道整个在 Tauri 层，后端 updater 在桌面模式是关着的，
      // 所以这一条只能由前端记；pip / pipx 那条由后端 updater 自己记。
      const version = get().desktopUpdate?.version
      captureTelemetry('update_completed', {
        update_kind: 'desktop',
        // 空串在白名单里是非法值（会让整条事件被丢掉），所以拿不到版本号时
        // 干脆不带这个属性——少一个属性好过少一条事件
        ...(version ? { target_version: version } : {}),
      })
    } catch (e) {
      set({
        desktopPhase: 'idle',
        desktopProgress: null,
        desktopError:
          e instanceof Error ? e.message : t('update.desktopInstallFailed', { ns: 'errors' }),
      })
    }
  },

  relaunch: async () => {
    if (get().relaunching || get().desktopPhase !== 'installed') return
    set({ relaunching: true, relaunchFailed: false, desktopError: null })
    try {
      await relaunchDesktop()
    } catch (e) {
      set({
        relaunchFailed: true,
        // Native IPC can reject with a string. Diagnostics stay literal; the
        // recovery instruction is translated by both installed-state surfaces.
        desktopError: e instanceof Error ? e.message : typeof e === 'string' ? e : null,
      })
    } finally {
      set({ relaunching: false })
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
