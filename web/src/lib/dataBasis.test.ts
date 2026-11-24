/** 「依据」那一句（`basisOf`）：封闭句式、只拼后端给的数；没有 facts 不显示；没画的行按原因拆开。 */
import { describe, expect, it } from 'vitest'
import { basisOf } from './dataBasis'

type C = Parameters<typeof basisOf>[0]
const facts = (o: Partial<NonNullable<C['facts']>> = {}) => ({ rows: 40, drawn: 36, groups: null, n_min: null, n_max: null, x_values: null, ...o })
const mainKeys = (c: C) => basisOf(c)?.main.map((s) => s.key)

describe('basisOf：主句', () => {
  it('没有 facts：不显示（即使有没画的行）', () => {
    expect(basisOf({ title_code: 'points_mean', unplotted: { count: 3 } })).toBeNull()
    expect(basisOf({ title_code: 'points_mean', facts: null })).toBeNull()
  })

  it('有分组：每组一样多 / 不一样多', () => {
    expect(basisOf({ title_code: 'points_mean', facts: facts({ groups: 3, n_min: 8, n_max: 8 }) })!.main).toEqual([{ key: 'groupsEqual', values: { groups: 3, n: 8 } }])
    expect(basisOf({ title_code: 'box_points', facts: facts({ groups: 3, n_min: 8, n_max: 15 }) })!.main).toEqual([{ key: 'groupsRange', values: { groups: 3, min: 8, max: 15 } }])
  })

  it('趋势类：有分组时再接横轴取值；没有分组是一整句', () => {
    for (const code of ['line_points', 'line_band', 'line_units']) {
      expect(mainKeys({ title_code: code, facts: facts({ groups: 2, n_min: 18, n_max: 18, x_values: 6 }) })).toEqual(['groupsEqual', 'xAlso'])
      expect(mainKeys({ title_code: code, facts: facts({ groups: 2, n_min: 5, n_max: 9, x_values: 6 }) })).toEqual(['groupsRange', 'xAlso'])
      expect(basisOf({ title_code: code, facts: facts({ x_values: 6 }) })!.main).toEqual([{ key: 'xOnly', values: { drawn: 36, x: 6 } }])
    }
  })

  it('非趋势画法即使有 x_values 也不写横轴取值', () => {
    expect(mainKeys({ title_code: 'points_mean', facts: facts({ groups: 2, n_min: 18, n_max: 18, x_values: 6 }) })).toEqual(['groupsEqual'])
  })

  it('散点：「按 N 个点绘制，每行一个点」，不写分组', () => {
    for (const code of ['scatter', 'scatter_fit']) {
      expect(basisOf({ title_code: code, facts: facts({ groups: 2, n_min: 18, n_max: 18, x_values: 30 }) })!.main).toEqual([{ key: 'points', values: { drawn: 36 } }])
    }
  })

  it('其它没有分组的（单组分布）：「按 N 个数值绘制」', () => {
    expect(basisOf({ title_code: 'hist', facts: facts() })!.main).toEqual([{ key: 'plain', values: { drawn: 36 } }])
    expect(basisOf({ title_code: 'hist', facts: facts({ x_values: 6 }) })!.main).toEqual([{ key: 'plain', values: { drawn: 36 } }])
  })
})

describe('basisOf：没画的行', () => {
  const sk = (o: Partial<NonNullable<C['facts']>>, count = 0) => basisOf({ title_code: 'hist', facts: facts(o), unplotted: { count } })!.skipped

  it('只有缺值 / 只有不是数 / 两种都有', () => {
    expect(sk({ skipped_missing: 2, skipped_other: 0 }, 2)).toEqual({ key: 'skippedMissing', values: { count: 2 } })
    expect(sk({ skipped_missing: 0, skipped_other: 3 }, 3)).toEqual({ key: 'skippedOther', values: { count: 3 } })
    expect(sk({ skipped_missing: 2, skipped_other: 3 }, 5)).toEqual({ key: 'skippedBoth', values: { missing: 2, other: 3 } })
    expect(sk({ skipped_missing: 4 }, 4)).toEqual({ key: 'skippedMissing', values: { count: 4 } }) // 另一个没给 = 0
  })

  it('都是 0：没有这一句', () => {
    expect(sk({ skipped_missing: 0, skipped_other: 0 }, 0)).toBeNull()
    expect(sk({}, 0)).toBeNull()
  })

  it('旧响应没有拆开的两个数：按总数「另有 N 行未计入」', () => {
    expect(sk({}, 4)).toEqual({ key: 'skippedAll', values: { count: 4 } })
  })
})
