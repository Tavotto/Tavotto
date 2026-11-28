/** 多个 y 的「依据」句（`series_facts`）：封闭句式、只拼后端给的数；旧响应不拿第一列的数顶替。 */
import { describe, expect, it } from 'vitest'
import { basisOf } from './dataBasis'
import type { AiDataSeriesFacts } from './api'

type C = Parameters<typeof basisOf>[0]
const sf = (series: [number, number?, number?][], o: Partial<AiDataSeriesFacts> = {}): AiDataSeriesFacts => ({
  rows: 20,
  eligible_rows: 20,
  rows_any: 20,
  rows_all: 20,
  series: series.map(([n, drawn, missing], i) => ({ y: `y${i}`, n, x_values: n, ...(drawn === undefined ? {} : { err_drawn: drawn, err_missing: missing ?? 0 }) })),
  ...o,
})
const oldFacts = { rows: 12, drawn: 4, groups: null, n_min: null, n_max: null, x_values: 4 }
const sentences = (c: C) => {
  const b = basisOf(c)!
  return [...b.main, b.errGap].filter((x) => x != null)
}

describe('多个 y：按系列数', () => {
  it('数量相同且覆盖相同：各有 n 个数据点', () => {
    expect(sentences({ title_code: 'line_points', series_facts: sf([[6], [6]]) })).toEqual([{ key: 'seriesEqual', values: { k: 2, n: 6 } }])
  })
  it('数量不同（至多 3 个系列）：逐个列出，保持系列顺序', () => {
    expect(sentences({ title_code: 'line_points', series_facts: sf([[4], [5]]) })).toEqual([{ key: 'seriesEach', values: { k: 2, counts: ['4', '5'] } }])
    expect(sentences({ title_code: 'line_points', series_facts: sf([[5], [4], [6]]) })).toEqual([{ key: 'seriesEach', values: { k: 3, counts: ['5', '4', '6'] } }])
  })
  it('数量相同但有效的行不是同一批：注明覆盖位置不同', () => {
    expect(sentences({ title_code: 'line_points', series_facts: sf([[2], [2]], { rows_any: 4, rows_all: 0 }) })).toEqual([{ key: 'seriesApart', values: { k: 2, n: 2 } }])
  })
  it('超过 3 个系列且数量不同：用范围', () => {
    const c = { title_code: 'line_points', series_facts: sf([[116], [120], [118], [117], [119]]) }
    expect(sentences(c)).toEqual([{ key: 'seriesRange', values: { k: 5, min: 116, max: 120 } }])
  })
  it('有中心值却缺误差：各系列相加；没有缺 / 没配误差列不写', () => {
    expect(basisOf({ title_code: 'line_err', series_facts: sf([[4, 4, 0], [5, 4, 1]]) })!.errGap).toEqual({ key: 'errGap', values: { count: 1 } })
    expect(basisOf({ title_code: 'line_err', series_facts: sf([[4, 4, 0], [5, 5, 0]]) })!.errGap).toBeNull()
    expect(basisOf({ title_code: 'line_points', series_facts: sf([[4], [5]]) })!.errGap).toBeNull()
  })
  it('没画的行用 series_facts 里拆好的数', () => {
    const b = basisOf({ title_code: 'line_points', series_facts: sf([[6], [6]], { skipped_missing: 2, skipped_other: 1 }) })!
    expect(b.skipped).toEqual({ key: 'skippedBoth', values: { missing: 2, other: 1 } })
  })
})

describe('多个 y：旧响应与判断', () => {
  it('普通折线（没有误差列）配方里有多个 y、却没有 series_facts：不显示旧的第一列数字', () => {
    expect(basisOf({ title_code: 'line_points', facts: oldFacts, values: { y: ['a', 'b'] } })).toBeNull()
    expect(basisOf({ title_code: 'line_band', facts: oldFacts, values: { y: ['a', 'b'] } })).toBeNull()
  })
  it('旧响应有没画的行时仍照旧写那一句，主句留空', () => {
    const b = basisOf({ title_code: 'line_points', facts: { ...oldFacts, skipped_missing: 2 }, values: { y: ['a', 'b'] } })!
    expect(b.main).toEqual([])
    expect(b.skipped).toEqual({ key: 'skippedMissing', values: { count: 2 } })
  })
  it('单个 y 的句子逐字不变（即使带着一个 values.y）', () => {
    expect(basisOf({ title_code: 'line_points', facts: { ...oldFacts, groups: 2, n_min: 6, n_max: 6 }, values: { y: ['a'] } })!.main).toEqual([
      { key: 'groupsEqual', values: { groups: 2, n: 6 } },
      { key: 'xAlso', values: { x: 4 } },
    ])
    expect(basisOf({ title_code: 'line_err', facts: oldFacts, error_column: 'e', values: { y: ['a'] } })!.main).toEqual([{ key: 'rowsErr', values: { drawn: 4 } }])
  })
  it('把列当分组 / 大表的多个 y 没有 facts 也没有 series_facts：不显示（不会冒出孤零零的「另有 N 行未计入」）', () => {
    expect(basisOf({ title_code: 'points_mean', values: { y: ['a', 'b', 'c'] }, unplotted: { count: 3 } })).toBeNull()
    expect(basisOf({ title_code: 'line_points', values: { y: ['a', 'b'] }, unplotted: { count: 3 } })).toBeNull()
    expect(basisOf({ title_code: 'line_err', values: { y: ['a', 'b'] }, error_columns: [{ column: 'a_sd' }, { column: 'b_sd' }], unplotted: { count: 3 } })).toBeNull()
  })
  it('四个趋势画法码都按多个 y 处理（旧响应不显示第一列的数）', () => {
    for (const code of ['line_points', 'line_band', 'line_units', 'line_err']) expect(basisOf({ title_code: code, facts: oldFacts, values: { y: ['a', 'b'] } })?.main ?? []).toEqual([])
  })
  it('别的族的多列纵轴（区间 / 构成……）照旧按 facts 写', () => {
    expect(basisOf({ title_code: 'interval', facts: oldFacts, values: { y: ['est', 'lo', 'hi'] } })!.main).toEqual([{ key: 'plain', values: { drawn: 4 } }])
  })
})
