import { describe, expect, it } from 'vitest'
import { ApiError } from '@/lib/api'
import { describeRequestFailure, NOTICE_INLINE_MAX, splitNoticeText } from './noticeText'

describe('splitNoticeText：一句话留在正文，一段进详情', () => {
  it('空 / 空白 → 两边都没有', () => {
    expect(splitNoticeText(null)).toEqual({ inline: null, detail: null })
    expect(splitNoticeText('  ')).toEqual({ inline: null, detail: null })
  })
  it('一句话（≤ 上限、无换行）→ 正文', () => {
    expect(splitNoticeText('panels 读不回来')).toEqual({ inline: 'panels 读不回来', detail: null })
    const edge = 'x'.repeat(NOTICE_INLINE_MAX)
    expect(splitNoticeText(edge).inline).toBe(edge)
  })
  it('超过上限或带换行 → 整段进详情，正文没有', () => {
    const long = 'x'.repeat(NOTICE_INLINE_MAX + 1)
    expect(splitNoticeText(long)).toEqual({ inline: null, detail: long })
    expect(splitNoticeText('Traceback\n  File "a.py"')).toEqual({ inline: null, detail: 'Traceback\n  File "a.py"' })
  })
})

describe('describeRequestFailure：一句原因 + 详情', () => {
  const GENERIC = '通用原因'
  it('裸的 HTTP 500 → 通用原因；详情带状态、稳定码、诊断引用', () => {
    const e = new ApiError('HTTP 500', 500, { code: 'upstream_timeout' }, 'ref-1')
    expect(describeRequestFailure(e, GENERIC)).toEqual({
      reason: GENERIC,
      detail: 'HTTP 500\nupstream_timeout\nref ref-1',
    })
  })
  it('后端给了读得懂的一句 → 它就是原因；状态仍进详情', () => {
    const e = new ApiError('没有可用的助手', 409, {})
    expect(describeRequestFailure(e, GENERIC)).toEqual({ reason: '没有可用的助手', detail: 'HTTP 409' })
  })
  it('后端原话太长 → 通用原因，原话进详情', () => {
    const long = '很长'.repeat(60)
    const r = describeRequestFailure(new ApiError(long, 500, {}), GENERIC)
    expect(r.reason).toBe(GENERIC)
    expect(r.detail).toBe(`HTTP 500\n${long}`)
  })
  it('网络层错误（不是 ApiError）：一句话的留作原因，没有详情', () => {
    expect(describeRequestFailure(new TypeError('Failed to fetch'), GENERIC)).toEqual({
      reason: 'Failed to fetch',
      detail: null,
    })
  })
})
