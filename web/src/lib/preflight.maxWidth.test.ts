import { describe, expect, it } from 'vitest'
import vectors from '../../../tests/golden/preflight_vectors.json'
import { runSpec, type PreflightSpec } from './preflight'
import { loadProfile, type JournalOverride } from './profile'

/**
 * 只写了最大宽度（max）的期刊也要判页宽（与 engine/preflight.py 同源；整条向量对账在 preflight.golden.test.ts，
 * 这里把判据的几个边按行为钉住）。
 */
const base = (vectors as { cases: { name: string; spec: unknown }[] }).cases.find(
  (c) => c.name === 'journal-max-over',
)!.spec as PreflightSpec

const col = (id: string, w: number, qualifier = 'exact') => ({
  id,
  label: { zh: id, en: id },
  width_mm: w,
  qualifier,
})

function pageWidthIssue(columns: ReturnType<typeof col>[], width: number) {
  const journal = { page_columns: columns, widths_mm: { single: null, double: null } }
  const profile = loadProfile('journal-base-v1', journal as unknown as JournalOverride)
  const spec = { ...base, page: { ...base.page, w_mm: width } } as PreflightSpec
  return runSpec(spec, profile).find((i) => i.id === 'page-width')
}

const JACS = [col('single', 84.7, 'max'), col('double', 177.8, 'max')]

describe('page-width 认 max 栏位', () => {
  it('超过最大的 max 才报，并说「最大宽度」', () => {
    const issue = pageWidthIssue(JACS, 228.6)!
    expect(issue.message.key).toBe('preflight.pageWidthMax')
    expect(issue.message.values).toEqual({ actual: '228.6', max: '177.8' })
    expect(issue.detail.columns).toEqual([
      { id: 'single', width_mm: 84.7, qualifier: 'max' },
      { id: 'double', width_mm: 177.8, qualifier: 'max' },
    ])
  })
  it('不超不报，恰在容差边上放行', () => {
    expect(pageWidthIssue(JACS, 60)).toBeUndefined()
    expect(pageWidthIssue(JACS, 177.8)).toBeUndefined()
    expect(pageWidthIssue(JACS, 178.3)).toBeUndefined()
    expect(pageWidthIssue(JACS, 178.4)).toBeDefined()
  })
  it('exact 与 max 并存：任一满足即合格，typical 不参与', () => {
    const cols = [col('c1', 90), col('cap', 140, 'max'), col('typ', 200, 'typical')]
    expect(pageWidthIssue(cols, 90)).toBeUndefined()
    expect(pageWidthIssue(cols, 120)).toBeUndefined()
    expect(pageWidthIssue(cols, 150)).toBeDefined()
    expect(pageWidthIssue(cols, 200)).toBeDefined()
  })
  it('只有 exact：沿用原文案与 detail 形状', () => {
    const issue = pageWidthIssue([col('a', 90), col('b', 180)], 100)!
    expect(issue.message.key).toBe('preflight.pageWidth')
    expect(issue.detail.columns).toEqual([
      { id: 'a', width_mm: 90 },
      { id: 'b', width_mm: 180 },
    ])
  })
})
