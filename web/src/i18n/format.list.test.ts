import { afterEach, describe, expect, it } from 'vitest'
import { setLocale } from '@/i18n'
import { listJoin } from './format'

afterEach(async () => {
  await setLocale('zh-CN')
})

describe('listJoin', () => {
  it('中文：英文包名与「和」之间留空格，两个、三个都是', async () => {
    await setLocale('zh-CN')
    expect(listJoin(['pandas', 'openpyxl'])).toBe('pandas 和 openpyxl')
    expect(listJoin(['a', 'b', 'c'])).toBe('a、b 和 c')
    expect(listJoin(['a', 'b'], 'disjunction')).toBe('a 或 b')
  })

  it('中文：汉字之间不加空格；单个原样', async () => {
    await setLocale('zh-CN')
    expect(listJoin(['数据', '图'])).toBe('数据和图')
    expect(listJoin(['pandas'])).toBe('pandas')
  })

  it('英文不受影响', async () => {
    await setLocale('en-US')
    expect(listJoin(['a', 'b', 'c'])).toBe('a, b, and c')
    expect(listJoin(['pandas', 'openpyxl'])).toBe('pandas and openpyxl')
  })
})
