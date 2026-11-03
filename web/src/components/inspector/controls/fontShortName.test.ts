import { describe, expect, it } from 'vitest'
import { cjkFontShortName } from './fontShortName'

describe('cjkFontShortName', () => {
  it.each([
    ['宋体-简', '宋体'],
    ['苹方-简', '苹方'],
    ['黑体-繁', '黑体'],
    ['宋体-简体', '宋体'],
    ['Songti SC', 'Songti'],
    ['PingFang SC', 'PingFang'],
    ['Noto Sans SC Regular', 'Noto Sans'],
    ['Noto Sans CJK SC Medium', 'Noto Sans CJK'],
    ['Source Han Serif-Regular', 'Source Han Serif'],
    ['自动', '自动'],
    ['微软雅黑', '微软雅黑'],
    ['华文黑体', '华文黑体'],
    ['Noto Sans', 'Noto Sans'],
    ['Regular', 'Regular'],
    ['简', '简'],
  ])('%s → %s', (input, expected) => {
    expect(cjkFontShortName(input)).toBe(expected)
  })
})
