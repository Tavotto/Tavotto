/** 「依据」那一句（`basisSentences`）：封闭句式、只拼后端给的数，没有 facts 不显示。 */
import { describe, expect, it } from 'vitest'
import { basisSentences } from './dataBasis'

const facts = (o: Partial<NonNullable<Parameters<typeof basisSentences>[0]['facts']>> = {}) => ({
  rows: 40, drawn: 36, groups: null, n_min: null, n_max: null, x_values: null, ...o,
})
const keys = (c: Parameters<typeof basisSentences>[0]) => basisSentences(c)?.map((s) => s.key) ?? null

describe('basisSentences', () => {
  it('没有 facts：不显示（即使有没画的行）', () => {
    expect(basisSentences({ title_code: 'points_mean', unplotted: { count: 3 } })).toBeNull()
    expect(basisSentences({ title_code: 'points_mean', facts: null })).toBeNull()
  })

  it('有分组、每组一样多 / 不一样多', () => {
    expect(basisSentences({ title_code: 'points_mean', facts: facts({ groups: 3, n_min: 12, n_max: 12 }) })).toEqual([
      { key: 'groupsEqual', values: { drawn: 36, groups: 3, n: 12 } },
    ])
    expect(basisSentences({ title_code: 'box_points', facts: facts({ groups: 3, n_min: 8, n_max: 15 }) })).toEqual([
      { key: 'groupsRange', values: { drawn: 36, groups: 3, min: 8, max: 15 } },
    ])
  })

  it('折线 / 趋势：有分组接「横轴有 X 个不同取值」；没有分组是「N 个值，横轴有 X 个不同取值」', () => {
    for (const code of ['line_points', 'line_band', 'line_units']) {
      expect(keys({ title_code: code, facts: facts({ groups: 2, n_min: 18, n_max: 18, x_values: 6 }) })).toEqual(['groupsEqual', 'xAlso'])
      expect(basisSentences({ title_code: code, facts: facts({ x_values: 6 }) })).toEqual([{ key: 'xOnly', values: { drawn: 36, x: 6 } }])
    }
  })

  it('非折线画法即使有 x_values 也不写横轴取值', () => {
    expect(keys({ title_code: 'points_mean', facts: facts({ groups: 2, n_min: 18, n_max: 18, x_values: 6 }) })).toEqual(['groupsEqual'])
    expect(keys({ title_code: 'hist', facts: facts({ x_values: 6 }) })).toBeNull()
  })

  it('散点：「N 个点，每行一个」，不写分组', () => {
    for (const code of ['scatter', 'scatter_fit']) {
      expect(basisSentences({ title_code: code, facts: facts({ groups: 2, n_min: 18, n_max: 18, x_values: 30 }) })).toEqual([
        { key: 'points', values: { drawn: 36 } },
      ])
    }
  })

  it('没画的行：句尾再接一句；0 行不接', () => {
    expect(keys({ title_code: 'points_mean', facts: facts({ groups: 3, n_min: 12, n_max: 12 }), unplotted: { count: 4 } })).toEqual(['groupsEqual', 'unplotted'])
    expect(basisSentences({ title_code: 'scatter', facts: facts(), unplotted: { count: 4 } })!.at(-1)).toEqual({ key: 'unplotted', values: { count: 4 } })
    expect(keys({ title_code: 'scatter', facts: facts(), unplotted: { count: 0 } })).toEqual(['points'])
  })

  it('其余情形只剩没画的行时留那一句，什么都没有就是 null', () => {
    expect(keys({ title_code: 'hist', facts: facts(), unplotted: { count: 2 } })).toEqual(['unplotted'])
    expect(keys({ title_code: 'hist', facts: facts() })).toBeNull()
  })
})
