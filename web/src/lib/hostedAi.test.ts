/**
 * 「Tavotto Agent」（内置 AI，ADR 0200）的失败说明：后端给闭集原因，前端只翻译——每个原因一句话、带日期的有日期、
 * 名字由品牌常量拼出、认不得的原因也落进「暂时连不上」那一句。
 */
import { describe, expect, it } from 'vitest'

import { ApiError } from '@/lib/api'
import { PRODUCT_NAME } from '@/lib/brand'
import { formatMessage, setLocale } from '@/i18n'
import { formatDate } from '@/i18n/format'
import { hostedAiName, hostedFailureMessage, hostedFailureOfError, hostedFailureOfFix, isWaitReason } from './hostedAi'

const NAME = `${PRODUCT_NAME} Agent`
const TOMORROW = Date.UTC(2026, 10, 22, 16, 0, 0)

describe('hostedFailureMessage', () => {
  it('名字取自品牌常量', () => {
    expect(hostedAiName()).toBe(NAME)
  })

  it('额度类：有恢复时刻就写日期，没有就只说用完了', async () => {
    await setLocale('zh-CN')
    expect(formatMessage(hostedFailureMessage({ reason: 'quota_exhausted', retry_at: null }))).toBe(`${NAME} 的额度用完了。`)
    expect(formatMessage(hostedFailureMessage({ reason: 'quota_exhausted', retry_at: TOMORROW }))).toBe(
      `${NAME} 的额度用完了，${formatDate(TOMORROW)}恢复。`,
    )
    expect(formatMessage(hostedFailureMessage({ reason: 'daily_limit', retry_at: TOMORROW }))).toBe(
      `${NAME} 今天的额度用完了，${formatDate(TOMORROW)}再用。`,
    )
    expect(formatMessage(hostedFailureMessage({ reason: 'daily_limit', retry_at: null }))).toBe(`${NAME} 今天的额度用完了。`)
  })

  it('未授权一句；熔断 / 网络 / 网关出错 / 未知原因统一「暂时连不上」', async () => {
    await setLocale('zh-CN')
    expect(formatMessage(hostedFailureMessage({ reason: 'not_entitled', retry_at: null }))).toBe(`这台电脑上的 ${NAME} 暂时用不了。`)
    for (const reason of ['paused', 'network', 'server', 'rate_limited', 'kind_disabled', 'client_error', 'disabled', 'something_new']) {
      expect(formatMessage(hostedFailureMessage({ reason, retry_at: null }))).toBe(`${NAME} 暂时连不上，稍后再试。`)
    }
  })

  it('英文界面同样一句话', async () => {
    await setLocale('en-US')
    expect(formatMessage(hostedFailureMessage({ reason: 'daily_limit', retry_at: TOMORROW }))).toBe(
      `${NAME} is out of quota for today. Try again on ${formatDate(TOMORROW)}.`,
    )
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
