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

/** 额度恢复的日期（只写月日，不带年）；没有时刻 / 就是明天（那种情况用「请明天再试」的说法）= null */
export function whenMessage(retryAt: number | null, now: number = Date.now()): UiMessage | null {
  if (retryAt == null || isTomorrow(retryAt, now)) return null
  return msg('hosted.whenDate', { date: formatDateTime(retryAt, { month: 'short', day: 'numeric' }) }, 'ai')
}

/** 一句话（不带句号以外的修饰；每个键都写成字面量，i18n 检查才数得到） */
export function hostedFailureMessage(f: HostedFailure, now: number = Date.now()): UiMessage {
  const name = hostedAiName()
  const when = whenMessage(f.retry_at, now)
  switch (f.reason) {
    case 'quota_exhausted':
      // 恢复日是明天 = 按「今日额度」的说法；有别的日期才写日期
      if (f.retry_at != null && isTomorrow(f.retry_at, now)) return msg('hosted.daily', undefined, 'ai')
      return when ? msg('hosted.quotaOn', { when }, 'ai') : msg('hosted.quota', undefined, 'ai')
    case 'daily_limit':
      return msg('hosted.daily', undefined, 'ai')
    case 'not_entitled':
      return msg('hosted.notEntitled', { name }, 'ai')
    case 'paused':
      return msg('hosted.paused', { name }, 'ai')
    default:
      return msg('hosted.unavailable', { name }, 'ai')
  }
}

/** 因额度静默退到的那条连接：显示名（取自连接弹窗列表）与种类，后端 `ai_connections.display_of` 给 */
export interface HostedFallback extends HostedFailure {
  label: string
  kind: 'key' | 'subscription'
}

/** 修复因额度静默退到自己的连接之后，卡片里的一行灰字：「今日额度已用尽，已经切换到您的 DeepSeek Key / ChatGPT 订阅」 */
export function hostedFallbackMessage(f: HostedFallback): UiMessage {
  const name = f.label
  if (f.reason === 'quota_exhausted') {
    return f.kind === 'subscription' ? msg('hosted.fallbackQuotaSub', { name }, 'ai') : msg('hosted.fallbackQuotaKey', { name }, 'ai')
  }
  return f.kind === 'subscription' ? msg('hosted.fallbackDailySub', { name }, 'ai') : msg('hosted.fallbackDailyKey', { name }, 'ai')
}

/** `POST /api/ai/fix` 成功响应里的 `hosted_fallback`；形状不对 = null（不说这一句，修复照常跑） */
export function hostedFallbackOfFix(res: { hosted_fallback?: unknown }): HostedFallback | null {
  const f = res.hosted_fallback as Partial<HostedFallback> | undefined
  if (!f || typeof f.reason !== 'string' || typeof f.label !== 'string' || !f.label) return null
  return { reason: f.reason, retry_at: typeof f.retry_at === 'number' ? f.retry_at : null, label: f.label, kind: f.kind === 'subscription' ? 'subscription' : 'key' }
}

/** 「改用我自己的 Key」按钮上的字 */
export const hostedOwnKeyLabel = (): UiMessage => msg('hosted.useOwnKey', undefined, 'ai')
