import { create } from 'zustand'
import {
  ApiError,
  activateLicense,
  fetchLicenseStatus,
  licenseCheckout,
  refreshLicense,
  releaseLicense,
  type LicenseFailureCode,
  type LicenseStatus,
} from '@/lib/api'
import { currentLocale } from '@/i18n'

/**
 * 高级版授权的前端状态（ADR 0193 §八）。**前端不碰远程**：激活 / 刷新 / 解绑 / 建付款页都是本机引擎做，这里只遥控。
 * `status === null` = 还没问过；`{enabled:false}` = 入口整个不出现（`PRODUCTION_LIVE = False` 且没设测试端点）。
 * 失败只存稳定的码，一码一句话在界面层翻（`license.failure.*`）。
 */
/** 购买用的 Stripe Price lookup key，按界面语言选（与官网一致）；价格改在 Stripe、不改代码，常量只在这一处 */
export const LICENSE_LOOKUP_KEYS = { zh: 'founding_year_cny', other: 'founding_year_usd' } as const

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
  load: () => Promise<void>
  activate: (code: string) => Promise<boolean>
  refresh: () => Promise<boolean>
  release: () => Promise<boolean>
  /** 建付款页并在系统浏览器打开；`renew` = 给本机这一份续费 */
  buy: (renew?: boolean) => Promise<boolean>
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
    buy: (renew = false) =>
      run(async () => {
        const zh = currentLocale() === 'zh-CN'
        const r = await licenseCheckout(LICENSE_LOOKUP_KEYS[zh ? 'zh' : 'other'], zh ? 'zh' : 'en', renew)
        window.open(r.url, '_blank', 'noopener,noreferrer')
        return null
      }),
    clearFailure: () => set({ failure: null }),
  }
})

/** 授权入口是否出现：能力开着才有；没问过 / 关着都当没有 */
export const licenseVisible = (s: LicenseStatus | null): s is Extract<LicenseStatus, { enabled: true }> =>
  s?.enabled === true

/** 某个功能键此刻是否解锁（付费功能的灰态判据；真正的权益判断在服务端，这里只决定画法） */
export const licenseHas = (s: LicenseStatus | null, feature: string): boolean =>
  s?.enabled === true && s.licensed && s.features.includes(feature)
