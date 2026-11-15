import { t as translate } from '@/i18n'
import { formatDate, formatDateTime, formatShortWhen } from '@/i18n/format'
import type { ReviewFailureCode } from '@/lib/api'

/** 外部审阅的文案助手（ADR 0187）：命名空间 + 前缀固定在这一处，调用点只写最后一段 */
export const rs = (key: string, values?: Record<string, unknown>) =>
  translate(`review.${key}`, { ns: 'dialogs', ...(values ?? {}) })

export const isoMs = (iso: string): number => {
  const n = Date.parse(iso)
  return Number.isFinite(n) ? n : 0
}

/** 页签顶部那行用的短日期：「12月15日」 */
export const expiryShort = (iso: string): string => formatDateTime(isoMs(iso), { month: 'long', day: 'numeric' })

export const expiryDate = (iso: string): string => formatDate(isoMs(iso))

/** 失败码 → 一句话；不认识的码按「没有成功」说 */
export const failureText = (code: ReviewFailureCode | string): string =>
  rs(`failure.${code}`, { defaultValue: rs('failure.client_error') })

/** 意见时间：当天只写时分，更早的写短日期（昨天 / 周二 / 11/10） */
export function commentWhen(iso: string, now: number = Date.now()): string {
  const ts = isoMs(iso)
  const sameDay = new Date(ts).toDateString() === new Date(now).toDateString()
  return sameDay ? formatDateTime(ts, { timeStyle: 'short' }) : formatShortWhen(ts, now)
}

/** 「V2 · 今天 14:20」里的时间 */
export const versionWhen = (iso: string, now: number = Date.now()): string => {
  const ts = isoMs(iso)
  const sameDay = new Date(ts).toDateString() === new Date(now).toDateString()
  return sameDay
    ? `${formatShortWhen(ts, now)} ${formatDateTime(ts, { timeStyle: 'short' })}`
    : formatDateTime(ts, { dateStyle: 'medium', timeStyle: 'short' })
}
