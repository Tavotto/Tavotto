import { describe, expect, it } from 'vitest'
import { msg } from '@/i18n'
import { builtinCatalog } from '@/lib/specBinding'
import type { ValidationIssue } from '@/lib/validation'
import { buildStandard, standardIssues, standardRules, STANDARD_MAX_ISSUES, STANDARD_MAX_ISSUE_TEXT, STANDARD_MAX_RULES } from './changePlanStandard'
import { loadProfile } from './profile'

const catalog = builtinCatalog()
const issue = (severity: ValidationIssue['severity'], text: string): ValidationIssue =>
  ({ severity, message: msg(text), ruleCode: 'x' }) as unknown as ValidationIssue

describe('buildStandard', () => {
  it('没选期刊：用生效规范的名字，chosen=false，没有 journal_id；规则只有朴素值', () => {
    const s = buildStandard(null, catalog, [])
    expect(s.chosen).toBe(false)
    expect('journal_id' in s).toBe(false)
    expect(s.name.length).toBeGreaterThan(0)
    expect(s.name.length).toBeLessThanOrEqual(80)
    expect(Object.keys(s.rules).length).toBeGreaterThan(3)
    expect(Object.keys(s.rules).length).toBeLessThanOrEqual(STANDARD_MAX_RULES)
    for (const v of Object.values(s.rules)) expect(['number', 'string'].includes(typeof v) || Array.isArray(v)).toBe(true)
    expect(s.rules.min_font_size_pt).toBeTypeOf('number')
  })

  it('选了期刊：名字取选那一刻的期刊名，chosen=true 带 journal_id', () => {
    const s = buildStandard({ id: catalog[0].id, journalRef: { id: 'nature-x', catalogRev: 1, retrieved: '2026-01-01', label: 'Nature X' } }, catalog, [])
    expect(s).toMatchObject({ name: 'Nature X', chosen: true, journal_id: 'nature-x' })
  })

  it('期刊名过长按 80 截断', () => {
    const s = buildStandard({ id: catalog[0].id, journalRef: { id: 'j', catalogRev: 1, retrieved: '', label: 'N'.repeat(200) } }, catalog, [])
    expect(s.name.length).toBe(80)
  })

  it('问题：错误在前、同级保持原序、「验不了」不发、最多 20 条、每条 ≤ 200 字', () => {
    const list = [issue('suggestion', 's1'), issue('warn', 'w1'), issue('not_verifiable', 'nv'), issue('error', 'e1'), issue('warn', 'w2'), issue('error', 'e2')]
    const s = buildStandard(null, catalog, list)
    expect(s.issues).toEqual([
      { severity: 'error', text: 'e1' },
      { severity: 'error', text: 'e2' },
      { severity: 'warn', text: 'w1' },
      { severity: 'warn', text: 'w2' },
      { severity: 'info', text: 's1' },
    ])
    const many = [...Array.from({ length: 30 }, (_, i) => issue('warn', `w${i}`)), issue('error', 'late-error'), issue('warn', 'y'.repeat(500))]
    const out = buildStandard(null, catalog, many).issues
    expect(out).toHaveLength(STANDARD_MAX_ISSUES)
    expect(out[0].text).toBe('late-error')
    const long = buildStandard(null, catalog, [issue('warn', 'y'.repeat(500))]).issues[0]
    expect(long.text.length).toBe(STANDARD_MAX_ISSUE_TEXT)
  })
})

describe('standardRules', () => {
  it('规范没规定的项不出现（0 / null 不发）', () => {
    const p = loadProfile(catalog[0].id)
    const r = standardRules({ ...p, widths_mm: { ...p.widths_mm, single: null, double: null }, min_line_width_pt: 0 })
    expect('single_column_width_mm' in r).toBe(false)
    expect('min_line_width_pt' in r).toBe(false)
  })
})

describe('不交给模型的 Tavotto 自身状态', () => {
  it('图内修改没应用 / 渲染没跟上：不进 issues（真机：被当成了脚本待办）', () => {
    const mk = (ruleCode: string, text: string) => ({ severity: 'warn', message: msg(text), ruleCode }) as unknown as ValidationIssue
    const out = standardIssues([mk('unapplied-override', '还有 59 条图内修改没应用'), mk('stale-render', '渲染没跟上'), mk('font-min', '字号太小')])
    expect(out).toHaveLength(1)
    expect(out[0].text).toContain('字号太小')
  })
})
