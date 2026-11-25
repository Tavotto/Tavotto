import { create } from 'zustand'
import {
  ApiError,
  activateLicense,
  fetchLicenseStatus,
  fetchLicenseAccount,
  fetchLicenseUsage,
  fetchReviewQuota,
  refreshLicense,
  releaseLicense,
  setLicensePlateName,
  type LicenseFailureCode,
  type LicenseAccount,
  type LicenseStatus,
} from '@/lib/api'
import { currentLocale } from '@/i18n'
import { premiumPageUrl } from '@/lib/brand'

/**
 * 高级版授权的前端状态（ADR 0193 §八）。**前端不碰远程**：激活 / 刷新 / 解绑 / 建付款页都是本机引擎做，这里只遥控。
 * `status === null` = 还没问过；`{enabled:false}` = 入口整个不出现（`PRODUCTION_LIVE = False` 且没设测试端点）。
 * 失败只存稳定的码，一码一句话在界面层翻（`license.failure.*`）。
 */
const FAILURE_CODES: ReadonlySet<string> = new Set([
  'invalid_code',
  'device_limit',
  'revoked',
  'expired',
  'price_unavailable',
  'rate_limited',
  'network',
  'server',
  'client_error',
])

function failureOf(e: unknown): LicenseFailureCode {
  const code = e instanceof ApiError ? e.message : ''
  return FAILURE_CODES.has(code) ? (code as LicenseFailureCode) : 'client_error'
}

interface LicenseState {
  status: LicenseStatus | null
  failure: LicenseFailureCode | null
  busy: boolean
  /** 账户详情（类型 / 设备名额）：null = 还没取 / 取不到（那几行不画） */
  account: Extract<LicenseAccount, { ok: true }> | null
  /** 用量：`null` = 还没取；字段 `null` = 暂时算不准（界面写「用量暂不可用」） */
  usage: { agent_used: number | null; review_new_left: number | null } | null
  loadAccount: () => Promise<void>
  loadUsage: () => Promise<void>
  load: () => Promise<void>
  activate: (code: string) => Promise<boolean>
  refresh: () => Promise<boolean>
  release: () => Promise<boolean>
  /** 在系统浏览器打开官网价格页（购买 / 续费共用） */
  buy: () => Promise<boolean>
  /** 铭牌刻字：写本机（引擎 license/plate.json），不联网；失败就保持原名 */
  setPlateName: (name: string) => Promise<void>
  clearFailure: () => void
}

export const useLicenseStore = create<LicenseState>((set, get) => {
  const run = async (fn: () => Promise<LicenseStatus | null>): Promise<boolean> => {
    if (get().busy) return false
    set({ busy: true, failure: null })
    try {
      const next = await fn()
      set(next ? { status: next, busy: false } : { busy: false })
      return true
    } catch (e) {
      set({ busy: false, failure: failureOf(e) })
      return false
    }
  }
  return {
    status: null,
    failure: null,
    busy: false,
    account: null,
    usage: null,
    loadAccount: async () => {
      try {
        const a = await fetchLicenseAccount()
        set({ account: a.ok ? a : null })
      } catch {
        set({ account: null })
      }
      // 引擎在 `/v1/me` 回「已作废」时会把本机令牌标为 revoked：取完账户再读一次本机状态（纯本机、不联网），页面才跟着变
      try {
        set({ status: await fetchLicenseStatus() })
      } catch {
        /* 读不到就保持现状 */
      }
    },
    loadUsage: async () => {
      // 两个来源互不拖累：任何一个失败都只是那一项「暂不可用」
      const [agent, review] = await Promise.allSettled([fetchLicenseUsage(), fetchReviewQuota()])
      const used = agent.status === 'fulfilled' ? agent.value.agent_used : null
      const left = review.status === 'fulfilled' ? review.value.new_left : null
      set({
        usage: {
          agent_used: typeof used === 'number' && Number.isFinite(used) ? Math.min(1, Math.max(0, used)) : null,
          review_new_left: typeof left === 'number' ? left : null,
        },
      })
    },
    load: async () => {
      try {
        set({ status: await fetchLicenseStatus() })
      } catch {
        // 老后端没有这个接口 / 暂时取不到：当作没有入口，不报错
        set({ status: { enabled: false } })
      }
    },
    activate: (code) => run(() => activateLicense(code.trim())),
    refresh: () => run(() => refreshLicense()),
    release: () => run(() => releaseLicense()),
    buy: async () => {
      // 档位在官网价格页选，应用里不指定某一档（购买 / 续费都一样）；点了才出网，由系统浏览器打开
      window.open(premiumPageUrl(currentLocale()), '_blank', 'noopener,noreferrer')
      return true
    },
    setPlateName: async (name) => {
      try {
        set({ status: await setLicensePlateName(name) })
      } catch {
        /* 刻不上就保持原名，不打扰 */
      }
    },
    clearFailure: () => set({ failure: null }),
  }
})

/** 授权入口是否出现：能力开着才有；没问过 / 关着都当没有 */
export const licenseVisible = (s: LicenseStatus | null): s is Extract<LicenseStatus, { enabled: true }> =>
  s?.enabled === true

/** 某个功能键此刻是否解锁（付费功能的灰态判据；真正的权益判断在服务端，这里只决定画法） */
export const licenseHas = (s: LicenseStatus | null, feature: string): boolean =>
  s?.enabled === true && s.licensed && s.features.includes(feature)
