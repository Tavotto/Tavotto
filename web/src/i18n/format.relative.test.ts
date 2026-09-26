import { afterEach, describe, expect, it } from 'vitest'
import { setLocale } from '@/i18n'
import { formatRelativeTime } from './format'

const NOW = Date.UTC(2026, 8, 26, 12)
const ago = (s: number) => NOW - s * 1000

afterEach(async () => {
  await setLocale('zh-CN')
})

describe('formatRelativeTime', () => {
  it('中文：最大整单位、数字写法（不出「昨天 / 上周」）', async () => {
    await setLocale('zh-CN')
    expect(formatRelativeTime(ago(5), NOW)).toBe('1分钟前')
    expect(formatRelativeTime(ago(2 * 3600 + 59), NOW)).toBe('2小时前')
    expect(formatRelativeTime(ago(26 * 3600), NOW)).toBe('1天前')
    expect(formatRelativeTime(ago(8 * 86400), NOW)).toBe('1周前')
    expect(formatRelativeTime(ago(40 * 86400), NOW)).toBe('1个月前')
    expect(formatRelativeTime(ago(400 * 86400), NOW)).toBe('1年前')
  })

  it('英文跟着界面语言；未来时间按一分钟算', async () => {
    await setLocale('en-US')
    expect(formatRelativeTime(ago(3 * 86400), NOW)).toBe('3 days ago')
    expect(formatRelativeTime(NOW + 3600_000, NOW)).toBe('1 minute ago')
  })
})
