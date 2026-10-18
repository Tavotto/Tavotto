import { describe, expect, it } from 'vitest'
import { ApiError } from '@/lib/api'
import { describeRequestFailure, NOTICE_INLINE_MAX, splitNoticeSentences, splitNoticeText } from './noticeText'

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

describe('splitNoticeSentences：读得懂的原因取开头放得下的整句', () => {
  it('放得下整段 → 与 splitNoticeText 一致', () => {
    expect(splitNoticeSentences('源文件变了。')).toEqual({ inline: '源文件变了。', detail: null })
  })
  it('英文两句连着超限 → 前两句在正文，后面的出路进详情', () => {
    const a = 'The source file changed. Your edits are preserved.'
    const b = 'Restore the matching source or choose a compatible history version.'
    expect(splitNoticeSentences(`${a} ${b}`)).toEqual({ inline: a, detail: b })
  })
  it('中文按句号切', () => {
    const a = '源文件已经变了，你的编辑都还在。'
    const b = '请还原匹配的源文件，或选择一个兼容的历史版本后再试一次，这样导出才能继续往下进行，不会丢失。'
    expect(splitNoticeSentences(a + b + b)).toEqual({ inline: a + b, detail: b })
  })
  it('没有句界 / 带换行 / 首句就超限 → 整段进详情', () => {
    const long = 'x'.repeat(NOTICE_INLINE_MAX + 1)
    expect(splitNoticeSentences(long)).toEqual({ inline: null, detail: long })
    const tb = 'Traceback.\n  File "a.py"'
    expect(splitNoticeSentences(tb)).toEqual({ inline: null, detail: tb })
    const big = `${'y'.repeat(NOTICE_INLINE_MAX + 5)}. ok.`
    expect(splitNoticeSentences(big)).toEqual({ inline: null, detail: big })
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
