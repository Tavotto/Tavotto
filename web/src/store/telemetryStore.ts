import { create } from 'zustand'
import {
  fetchTelemetrySettings,
  patchTelemetryConsent,
  type TelemetryConsent,
  type TelemetrySettings,
} from '@/lib/api'
import { setTelemetryEnabled } from '@/lib/telemetry'

/**
 * 匿名用量统计的同意态。
 *
 * **三档而不是一个布尔**：`unset` / `enabled` / `disabled`。「还没问过」和
 * 「问过了，用户说不」必须分得开——只有前者才该弹一次询问，后者再弹就是骚扰。
 * 真正的状态存在后端的用户配置里，这里只是它的一份只读镜像 + 两个写入口。
 *
 * 首启询问的三条纪律：
 *   ① 询问出现之前**一个事件都不发**（后端在 unset 时连 install_id 都不生成）；
 *   ② 「暂不」写 `disabled` 而不是留在 unset —— 留着等于每次启动都再问一遍；
 *   ③ `TAVOTTO_NO_TELEMETRY=1` 时不弹（管理员已经替这台机器做了决定）。
 */
interface TelemetryState {
  settings: TelemetrySettings | null
  /** 询问弹窗当前是否该出现（load 之后才可能为真） */
  askOpen: boolean
  load: () => Promise<void>
  /** 用户在首启弹窗或设置里做出选择 */
  choose: (consent: TelemetryConsent, source: 'first_run' | 'settings') => Promise<void>
}

function adopt(settings: TelemetrySettings) {
  // 缓存给 lib/telemetry 用，免得每次编辑都白跑一次 HTTP
  setTelemetryEnabled(settings.enabled)
  return settings
}

export const useTelemetryStore = create<TelemetryState>((set) => ({
  settings: null,
  askOpen: false,

  load: async () => {
    try {
      const settings = adopt(await fetchTelemetrySettings())
      set({
        settings,
        // 两种情况都要问一次：**从没问过**（unset），以及**同意的是上一版
        // 采集范围**（后端升了 CONSENT_VERSION）。后者不是新用户——重新同意
        // 不换 install_id、也不再发 telemetry_enabled，由后端负责。
        // 硬开关关着时**不问**：管理员已经替这台机器做了决定，弹一个点了
        // 也没用的框只会让人以为是自己关的（needs_reconsent 后端已经算进去了，
        // 这里对 unset 那一路再挡一道）。
        askOpen:
          (settings.consent === 'unset' && !settings.hard_disabled) ||
          settings.needs_reconsent,
      })
    } catch {
      // 取不到就当没开：宁可少发，也不能在不知道同意态时发
      setTelemetryEnabled(false)
      set({ settings: null, askOpen: false })
    }
  },

  choose: async (consent, source) => {
    // 先收起询问：无论后端写没写成，用户已经表过态，不该再看到同一个框
    set({ askOpen: false })
    try {
      set({ settings: adopt(await patchTelemetryConsent(consent, source)) })
    } catch {
      setTelemetryEnabled(false)
    }
  },
}))
