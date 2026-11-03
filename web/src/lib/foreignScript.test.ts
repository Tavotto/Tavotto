import { describe, expect, it } from 'vitest'
import { foreignLanguageOf } from './foreignScript'

/** 与后端 `sourcelang.extension_hint`（tests/test_sourcelang.py::test_extension_hint_is_suffix_only）同一组用例 */
describe('foreignLanguageOf ↔ sourcelang.extension_hint', () => {
  it.each([
    ['a.R', 'r'],
    ['a.r', 'r'],
    ['/Users/me/dir/fig.R', 'r'],
    ['C:\\work\\fig.r', 'r'],
    ['a.m', 'matlab'],
    ['dir.with.dots/a.m', 'matlab'],
    ['a.py', null],
    ['a.Rmd', null],
    ['a.qmd', null],
    ['a.mlx', null],
    ['a.rds', null],
    ['a.M', null],
    ['.R', null],
    ['dir.R/', null],
    ['fig', null],
  ] as const)('%s → %s', (name, expected) => {
    expect(foreignLanguageOf(name)).toBe(expected)
  })
})
