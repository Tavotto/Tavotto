/** 依据句里与误差线有关的部分：现成的误差列（`points_err` / `line_err`）与算出来的误差线缺了几处。 */
import { describe, expect, it } from 'vitest'
import { basisOf } from './dataBasis'

type C = Parameters<typeof basisOf>[0]
const facts = (o: Partial<NonNullable<C['facts']>> = {}) => ({ rows: 12, drawn: 12, groups: 4, n_min: 3, n_max: 3, x_values: 4, ...o })

describe('现成的误差列', () => {
  it.each(['points_err', 'line_err'])('%s：「按表里的 N 行绘制，每行一个点」+「误差线取自「列」列」，不按分组数句子', (code) => {
    const b = basisOf({ title_code: code, facts: facts(), error_column: 'SD of 值' })!
    expect(b.main).toEqual([{ key: 'rowsErr', values: { drawn: 12 } }])
    expect(b.errColumn).toEqual({ key: 'errColumn', values: { column: 'SD of 值' } })
    expect(b.errMissing).toBeNull()
  })

  it('没画的行那一句照旧；没有 facts 仍不显示', () => {
    expect(basisOf({ title_code: 'points_err', facts: facts({ skipped_missing: 2, skipped_other: 0 }), unplotted: { count: 2 }, error_column: 'e' })!.skipped).toEqual({ key: 'skippedMissing', values: { count: 2 } })
    expect(basisOf({ title_code: 'points_err', error_column: 'e' })).toBeNull()
  })

  it('别的画法即使带了 error_column 也不写这一句（只认两种新画法）', () => {
    expect(basisOf({ title_code: 'points_mean', facts: facts(), error_column: 'e' })!.errColumn).toBeNull()
  })
})

describe('算出来的误差线缺了几处', () => {
  it('bar_error 等写「算不出误差线」，line_band 写「算不出误差带」；只有一处时点名，位置标签原样带出', () => {
    const missing = ['A', 'B', 'C']
    const bar = basisOf({ title_code: 'bar_error', facts: facts(), error_missing_for: missing })!
    expect(bar.errMissing).toEqual({ key: 'errMissing', values: { count: 3 } })
    expect(bar.missingFor).toEqual(missing)
    expect(basisOf({ title_code: 'line_band', facts: facts(), error_missing_for: ['x=1'] })!.errMissing).toEqual({ key: 'bandMissingOne', values: { label: 'x=1' } })
    expect(basisOf({ title_code: 'bar_error', facts: facts(), error_missing_for: ['C'] })!.errMissing).toEqual({ key: 'errMissingOne', values: { label: 'C' } })
    expect(basisOf({ title_code: 'line_band', facts: facts(), error_missing_for: ['1', '2'] })!.errMissing).toEqual({ key: 'bandMissing', values: { count: 2 } })
  })

  it('没有缺的：没有这一句', () => {
    const b = basisOf({ title_code: 'bar_error', facts: facts() })!
    expect(b.errMissing).toBeNull()
    expect(b.missingFor).toEqual([])
    expect(basisOf({ title_code: 'bar_error', facts: facts(), error_missing_for: [] })!.errMissing).toBeNull()
  })
})
