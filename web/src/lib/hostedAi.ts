import { ApiError } from '@/lib/api'
import { PRODUCT_NAME } from '@/lib/brand'
import { formatDateTime } from '@/i18n/format'
import { msg, type UiMessage } from '@/i18n'

/**
 * 「Tavotto Agent」（内置 AI，ADR 0200）的前端侧：连接 id、名字，和「没能开任务」时给用户的一句话。
 * 事实在后端（`engine/hosted_ai.py`）：这里只翻译它给的闭集原因，不判断额度、不判断授权。
 */

/** 虚拟连接的 id（与后端 `hosted_ai.PROVIDER_ID` 同值）；连接上另有 `hosted: true` */
export const HOSTED_AI_ID = 'tavotto-agent'

/** 界面名：由品牌常量拼出，不手写产品名 */
export const hostedAiName = (): string => `${PRODUCT_NAME} Agent`

/** 后端 `hosted_ai.FAILURES` 的闭集 */
export type HostedFailureReason =
  | 'quota_exhausted'
  | 'daily_limit'
  | 'not_entitled'
  | 'kind_disabled'
  | 'paused'
  | 'rate_limited'
  | 'network'
  | 'server'
  | 'client_error'
  | 'disabled'

export interface HostedFailure {
  reason: HostedFailureReason | string
  /** 额度恢复 / 可再试的时刻（UTC 毫秒）；网关没给 = null */
  retry_at: number | null
}

const isHostedFailure = (x: unknown): x is HostedFailure =>
  !!x && typeof x === 'object' && typeof (x as { reason?: unknown }).reason === 'string'

const normalize = (x: HostedFailure): HostedFailure => ({
  reason: x.reason,
  retry_at: typeof x.retry_at === 'number' && Number.isFinite(x.retry_at) ? x.retry_at : null,
})

/** `POST /api/ai/run` 的 409 `ai_hosted_unavailable`（params 是 `{reason, retry_at}`）→ 失败说明；别的错误 = null */
export function hostedFailureOfError(e: unknown): HostedFailure | null {
  if (!(e instanceof ApiError) || e.body?.code !== 'ai_hosted_unavailable') return null
  const p = e.body?.params
  return isHostedFailure(p) ? normalize(p) : { reason: 'server', retry_at: null }
}

/** `POST /api/ai/fix` 的 `{ok: false, reason: 'ai_hosted_unavailable', hosted}` */
export function hostedFailureOfFix(res: { reason?: string; hosted?: unknown }): HostedFailure | null {
  if (res.reason !== 'ai_hosted_unavailable') return null
  return isHostedFailure(res.hosted) ? normalize(res.hosted) : { reason: 'server', retry_at: null }
}

/** 稍后再试有没有意义：额度类等到恢复的时刻才会好，原样重发只会再得到同一句 */
export const isWaitReason = (f: HostedFailure): boolean => f.reason === 'quota_exhausted' || f.reason === 'daily_limit'

/** 本地日历上的「明天」（按本地日期算，不按 24 小时） */
export function isTomorrow(ts: number, now: number = Date.now()): boolean {
  const day = (t: number) => {
    const d = new Date(t)
    return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime()
  }
  const tomorrow = new Date(day(now))
  tomorrow.setDate(tomorrow.getDate() + 1)
  return day(ts) === tomorrow.getTime()
}

/** 「什么时候再用」：明天说「明天」，其余只写月日（不带年）；没有时刻 = null */
export function whenMessage(retryAt: number | null, now: number = Date.now()): UiMessage | null {
  if (retryAt == null) return null
  if (isTomorrow(retryAt, now)) return msg('hosted.whenTomorrow', undefined, 'ai')
  return msg('hosted.whenDate', { date: formatDateTime(retryAt, { month: 'short', day: 'numeric' }) }, 'ai')
}

/** 一句话（不带句号以外的修饰；每个键都写成字面量，i18n 检查才数得到） */
export function hostedFailureMessage(f: HostedFailure, now: number = Date.now()): UiMessage {
  const name = hostedAiName()
  const when = whenMessage(f.retry_at, now)
  switch (f.reason) {
    case 'quota_exhausted':
      return when ? msg('hosted.quotaOn', { name, when }, 'ai') : msg('hosted.quota', { name }, 'ai')
    case 'daily_limit':
      return when ? msg('hosted.dailyOn', { name, when }, 'ai') : msg('hosted.daily', { name }, 'ai')
    case 'not_entitled':
      return msg('hosted.notEntitled', { name }, 'ai')
    case 'paused':
      return msg('hosted.paused', { name }, 'ai')
    default:
      return msg('hosted.unavailable', { name }, 'ai')
  }
}

/** 修复因额度静默退到自己的 Key 之后，卡片里的一行灰字 */
export function hostedFallbackMessage(f: HostedFailure, now: number = Date.now()): UiMessage {
  const when = whenMessage(f.retry_at, now)
  if (f.reason === 'quota_exhausted') return when ? msg('hosted.fallbackQuotaOn', { when }, 'ai') : msg('hosted.fallbackQuota', undefined, 'ai')
  return msg('hosted.fallbackDaily', undefined, 'ai')
}

/** 「改用我自己的 Key」按钮上的字 */
export const hostedOwnKeyLabel = (): UiMessage => msg('hosted.useOwnKey', undefined, 'ai')
