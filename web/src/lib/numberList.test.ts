/**
 * `number_list` 字段（固定刻度位置）的文本 ↔ 数组换算。
 *
 * 这个控件的真实用法是「从别处粘一串数进来」，所以分隔符收得宽，
 * 解不出的段落丢掉而不是整串作废。
 */
import { describe, expect, it } from 'vitest'

import { formatNumberList, parseNumberList } from './numberList'

describe('parseNumberList', () => {
  it('逗号 / 空格 / 分号都算分隔符，全角标点也收', () => {
    expect(parseNumberList('0, 0.5 1;2，3；4')).toEqual([0, 0.5, 1, 2, 3, 4])
  })

  it('负数、科学计数、前后空白都认', () => {
    expect(parseNumberList('  -1.5 , 2e-3 ')).toEqual([-1.5, 0.002])
  })

  it('解不出数的段落丢掉，能用的留下（粘进来带单位的数据不至于全废）', () => {
    expect(parseNumberList('1, abc, 3')).toEqual([1, 3])
  })

  it('空串 / 纯分隔符 → 空数组（= 用当前刻度）', () => {
    expect(parseNumberList('')).toEqual([])
    expect(parseNumberList(' , ; ')).toEqual([])
  })

  it('与 formatNumberList 互逆', () => {
    const v = [0, 0.25, 0.5, 1]
    expect(parseNumberList(formatNumberList(v))).toEqual(v)
  })
})
