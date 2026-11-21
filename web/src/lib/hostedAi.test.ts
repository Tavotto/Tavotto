/**
 * 「Tavotto Agent」（内置 AI，ADR 0200）的失败说明：后端给闭集原因，前端只翻译——每个原因一句话、带日期的有日期、
 * 名字由品牌常量拼出、认不得的原因也落进「暂时连不上」那一句。
 */
import { describe, expect, it } from 'vitest'

import { ApiError } from '@/lib/api'
import { PRODUCT_NAME } from '@/lib/brand'
import { formatMessage, setLocale } from '@/i18n'
import { formatDateTime } from '@/i18n/format'
import { hostedAiName, hostedFailureMessage, hostedFallbackMessage, hostedFallbackOfFix, isTomorrow, hostedFailureOfError, hostedFailureOfFix, isWaitReason } from './hostedAi'

const NAME = `${PRODUCT_NAME} Agent`
const NOW = new Date(2026, 10, 21, 10, 0, 0).getTime()
const TOMORROW = new Date(2026, 10, 22, 0, 0, 0).getTime()
const LATER = new Date(2026, 10, 30, 0, 0, 0).getTime()

describe('hostedFailureMessage', () => {
  it('名字取自品牌常量', () => {
    expect(hostedAiName()).toBe(NAME)
  })

  it('额度类：明天说「明天」，其余只写月日（不带年），没有时刻就只说用完了', async () => {
    await setLocale('zh-CN')
    expect(formatMessage(hostedFailureMessage({ reason: 'quota_exhausted', retry_at: null }, NOW))).toBe('额度已用尽，请稍后再试')
    expect(formatMessage(hostedFailureMessage({ reason: 'daily_limit', retry_at: TOMORROW }, NOW))).toBe('今日额度已用尽，请明天再试')
    expect(formatMessage(hostedFailureMessage({ reason: 'daily_limit', retry_at: null }, NOW))).toBe('今日额度已用尽，请明天再试')
    // 恢复日就是明天：按「今日额度」的说法
    expect(formatMessage(hostedFailureMessage({ reason: 'quota_exhausted', retry_at: TOMORROW }, NOW))).toBe('今日额度已用尽，请明天再试')
    const md = formatDateTime(LATER, { month: 'short', day: 'numeric' })
    expect(md).not.toContain('2026')
    expect(formatMessage(hostedFailureMessage({ reason: 'quota_exhausted', retry_at: LATER }, NOW))).toBe(`额度已用尽，请 ${md} 再试`)
    expect(isTomorrow(TOMORROW, NOW)).toBe(true)
    expect(isTomorrow(LATER, NOW)).toBe(false)
    expect(isTomorrow(NOW + 3_600_000, NOW)).toBe(false) // 今天晚些时候不是明天
  })

  it('暂停服务一句；退到自己的 Key 之后卡片里的灰字', async () => {
    await setLocale('zh-CN')
    expect(formatMessage(hostedFailureMessage({ reason: 'paused', retry_at: null }))).toBe(`${NAME} 暂停服务，稍后恢复。`)
    const fb = (reason: string, label: string, kind: 'key' | 'subscription') => ({ reason, retry_at: null, label, kind })
    expect(formatMessage(hostedFallbackMessage(fb('daily_limit', 'DeepSeek', 'key')))).toBe('今日额度已用尽，已经切换到您的 DeepSeek Key')
    expect(formatMessage(hostedFallbackMessage(fb('daily_limit', 'ChatGPT', 'subscription')))).toBe('今日额度已用尽，已经切换到您的 ChatGPT 订阅')
    expect(formatMessage(hostedFallbackMessage(fb('quota_exhausted', 'DeepSeek', 'key')))).toBe('额度已用尽，已经切换到您的 DeepSeek Key')
    expect(formatMessage(hostedFallbackMessage(fb('quota_exhausted', 'Claude', 'subscription')))).toBe('额度已用尽，已经切换到您的 Claude 订阅')
    await setLocale('en-US')
    expect(formatMessage(hostedFallbackMessage(fb('daily_limit', 'DeepSeek', 'key')))).toBe('Today’s quota is used up. Switched to your DeepSeek key.')
    expect(formatMessage(hostedFallbackMessage(fb('daily_limit', 'ChatGPT', 'subscription')))).toBe('Today’s quota is used up. Switched to your ChatGPT subscription.')
    await setLocale('zh-CN')
    // 后端没给名称 / 形状不对：不说这一句
    expect(hostedFallbackOfFix({ hosted_fallback: { reason: 'daily_limit', retry_at: null } })).toBeNull()
    expect(hostedFallbackOfFix({ hosted_fallback: { reason: 'daily_limit', retry_at: null, label: 'X', kind: 'subscription' } })?.kind).toBe('subscription')
  })

  it('未授权一句；熔断 / 网络 / 网关出错 / 未知原因统一「暂时连不上」', async () => {
    await setLocale('zh-CN')
    expect(formatMessage(hostedFailureMessage({ reason: 'not_entitled', retry_at: null }))).toBe(`这台电脑上的 ${NAME} 暂时用不了。`)
    for (const reason of ['network', 'server', 'rate_limited', 'kind_disabled', 'client_error', 'disabled', 'something_new']) {
      expect(formatMessage(hostedFailureMessage({ reason, retry_at: null }))).toBe(`${NAME} 暂时连不上，稍后再试。`)
    }
  })

  it('英文界面同样一句话', async () => {
    await setLocale('en-US')
    expect(formatMessage(hostedFailureMessage({ reason: 'daily_limit', retry_at: TOMORROW }, NOW))).toBe('Today’s quota is used up. Please try again tomorrow.')
    expect(formatMessage(hostedFailureMessage({ reason: 'quota_exhausted', retry_at: LATER }, NOW))).toBe(
      `Your quota is used up. Please try again on ${formatDateTime(LATER, { month: 'short', day: 'numeric' })}.`,
    )
    expect(formatMessage(hostedFailureMessage({ reason: 'paused', retry_at: null }))).toBe(`${NAME} is paused for now.`)
    await setLocale('zh-CN')
  })

  it('只有额度类值得等；原样重发对它们没有意义', () => {
    expect(isWaitReason({ reason: 'quota_exhausted', retry_at: null })).toBe(true)
    expect(isWaitReason({ reason: 'daily_limit', retry_at: null })).toBe(true)
    expect(isWaitReason({ reason: 'paused', retry_at: null })).toBe(false)
  })
})

describe('从后端响应里认出它', () => {
  it('POST /api/ai/run 的 409 ai_hosted_unavailable', () => {
    const e = new ApiError('x', 409, { code: 'ai_hosted_unavailable', params: { reason: 'quota_exhausted', retry_at: TOMORROW } })
    expect(hostedFailureOfError(e)).toEqual({ reason: 'quota_exhausted', retry_at: TOMORROW })
    expect(hostedFailureOfError(new ApiError('x', 409, { code: 'ai_script_busy', params: {} }))).toBeNull()
    expect(hostedFailureOfError(new Error('offline'))).toBeNull()
    // 形状不对：仍是一条安静的「暂时连不上」，不是崩溃
    expect(hostedFailureOfError(new ApiError('x', 409, { code: 'ai_hosted_unavailable' }))).toEqual({ reason: 'server', retry_at: null })
    expect(hostedFailureOfError(new ApiError('x', 409, { code: 'ai_hosted_unavailable', params: { reason: 'paused', retry_at: 'soon' } }))).toEqual({ reason: 'paused', retry_at: null })
  })

  it('POST /api/ai/fix 的 ok:false', () => {
    expect(hostedFailureOfFix({ reason: 'ai_hosted_unavailable', hosted: { reason: 'paused', retry_at: null } })).toEqual({ reason: 'paused', retry_at: null })
    expect(hostedFailureOfFix({ reason: 'ai_fix_unavailable' })).toBeNull()
    expect(hostedFailureOfFix({ reason: 'ai_hosted_unavailable' })).toEqual({ reason: 'server', retry_at: null })
  })
})
