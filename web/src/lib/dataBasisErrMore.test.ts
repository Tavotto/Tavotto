/** 依据句：`bar_err`（每行一根柱）与多个 y 各配各的误差列（「a_sd」「b_sd」）。 */
import { describe, expect, it } from 'vitest'
import { basisOf } from './dataBasis'

type C = Parameters<typeof basisOf>[0]
const facts = (o: Partial<NonNullable<C['facts']>> = {}) => ({ rows: 12, drawn: 12, groups: 4, n_min: 3, n_max: 3, x_values: 4, ...o })

describe('bar_err', () => {
  it('主句是「每行一根柱」的变体（不是「点」），仍接「误差线取自…」', () => {
    const b = basisOf({ title_code: 'bar_err', facts: facts(), error_column: 'SD' })!
    expect(b.main).toEqual([{ key: 'rowsBar', values: { drawn: 12 } }])
    expect(b.errColumn).toEqual({ key: 'errColumn', values: { column: 'SD' } })
  })
  it('points_err / line_err 仍是「每行一个点」', () => {
    for (const code of ['points_err', 'line_err']) expect(basisOf({ title_code: code, facts: facts(), error_column: 'e' })!.main[0]!.key).toBe('rowsErr')
  })
})

describe('多个 y 各配各的误差列', () => {
  it('两列以上：errColumns，列名数组原样（界面各加引号）', () => {
    const b = basisOf({ title_code: 'line_err', facts: facts(), error_columns: [{ column: 'a_sd' }, { column: 'b_sd' }] })!
    expect(b.errColumn).toEqual({ key: 'errColumns', values: { columns: ['a_sd', 'b_sd'] } })
  })
  it('多个 y 的主句换成按系列数的句子（`series_facts`）；一项 / 单 y 仍是「每行一个点」；bar_err 仍是「每行一根柱」', () => {
    const two = [{ column: 'a_sd' }, { column: 'b_sd' }]
    const sf = { rows: 6, eligible_rows: 6, rows_any: 6, rows_all: 6, series: [{ y: 'a', n: 6, x_values: 6 }, { y: 'b', n: 6, x_values: 6 }] }
    expect(basisOf({ title_code: 'line_err', facts: facts(), series_facts: sf, error_columns: two })!.main).toEqual([{ key: 'seriesEqual', values: { k: 2, n: 6 } }])
    expect(basisOf({ title_code: 'line_err', facts: facts(), error_columns: [{ column: 'a_sd' }] })!.main[0]!.key).toBe('rowsErr')
    expect(basisOf({ title_code: 'line_err', facts: facts(), error_column: 'e' })!.main[0]!.key).toBe('rowsErr')
    expect(basisOf({ title_code: 'bar_err', facts: facts(), error_column: 'e' })!.main[0]!.key).toBe('rowsBar')
  })
  it('旧响应：多个误差列却没有 series_facts——不拿旧的第一列数字顶替，只剩「误差线取自…」那半句', () => {
    const two = [{ column: 'a_sd' }, { column: 'b_sd' }]
    const b = basisOf({ title_code: 'line_err', facts: facts(), error_columns: two })!
    expect(b.main).toEqual([])
    expect(b.errColumn).toEqual({ key: 'errColumns', values: { columns: ['a_sd', 'b_sd'] } })
  })
  it('只有一项 error_columns：按单列写', () => {
    expect(basisOf({ title_code: 'line_err', facts: facts(), error_columns: [{ column: 'a_sd' }] })!.errColumn).toEqual({ key: 'errColumn', values: { column: 'a_sd' } })
  })
  it('不是现成误差列的画法：不写「取自…」；旧响应没有别的可写，整句不显示', () => {
    expect(basisOf({ title_code: 'line_band', facts: facts(), error_columns: [{ column: 'a' }, { column: 'b' }] })).toBeNull()
    const sf = { rows: 6, eligible_rows: 6, rows_any: 6, rows_all: 6, series: [{ y: 'a', n: 6, x_values: 6 }, { y: 'b', n: 6, x_values: 6 }] }
    expect(basisOf({ title_code: 'line_band', series_facts: sf, error_columns: [{ column: 'a' }, { column: 'b' }] })!.errColumn).toBeNull()
  })
})
