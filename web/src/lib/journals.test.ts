/**
 * 内置期刊标准库（TS 侧）。与 Python 的 `tests/test_journal_catalog.py` 跑**同一份**
 * `tests/golden/journal_vectors.json`（夹具期刊，非真实数据）。
 */
import { describe, expect, it } from 'vitest'
import vectors from '../../../tests/golden/journal_vectors.json'
import shipped from '@journals'
import {
  JOURNAL_BASE_PROFILE_ID,
  JOURNAL_ERROR_CODES,
  JournalCatalogError,
  journalOverride,
  journalProfile,
  journalSources,
  listJournals,
  loadCatalog,
  validateCatalog,
} from './journals'
import { listProfiles, loadProfile, validateSpecShape } from './profile'

interface Op {
  path: string[]
  value?: unknown
  delete?: boolean
}
const V = vectors as unknown as {
  catalog: unknown
  list: unknown
  base_profile_id: string
  cases: Array<{
    journal_id: string
    override: unknown
    sources: unknown
    merged_fields: Record<string, unknown>
  }>
  unknown_journal_code: string
  invalid: Array<{ name: string; code: string; ops: Op[] }>
}

const fixture = () => structuredClone(V.catalog) as Record<string, unknown>

/** 与 scripts/gen_journal_vectors.py 的 apply_ops 同义 */
function applyOps(cat: unknown, ops: Op[]): unknown {
  const out = structuredClone(cat) as Record<string, unknown>
  for (const op of ops) {
    let node = out as Record<string, unknown>
    for (const p of op.path.slice(0, -1)) node = node[p] as Record<string, unknown>
    const last = op.path[op.path.length - 1]
    if (op.delete) delete node[last]
    else node[last] = structuredClone(op.value)
  }
  return out
}

function codeOf(fn: () => unknown): string {
  try {
    fn()
  } catch (e) {
    if (e instanceof JournalCatalogError) return e.code
    throw e
  }
  return 'NO_ERROR'
}

describe('随包的期刊库', () => {
  it('PR 1 是合法的空库', () => {
    expect(loadCatalog().schema).toBe(1)
    expect(Object.keys((shipped as { publishers: object }).publishers)).toEqual([])
    expect(Object.keys((shipped as { journals: object }).journals)).toEqual([])
    expect(listJournals()).toEqual([])
  })

  it('journal-base-v1 不进规范下拉，但取得到且是中性底', () => {
    expect(V.base_profile_id).toBe(JOURNAL_BASE_PROFILE_ID)
    expect(listProfiles().map((p) => p.profile_id)).not.toContain(JOURNAL_BASE_PROFILE_ID)
    const base = loadProfile(JOURNAL_BASE_PROFILE_ID)
    expect(base.profile_id).toBe(JOURNAL_BASE_PROFILE_ID)
    expect(base.allowed_aspect_ratios).toEqual([])
    expect(base.font_family.latin).toBe('')
    expect(base.font_family.latin_accepted).toEqual([])
    expect(base.cjk_fallback.required).toBe(false)
    expect(base.min_effective_font_size_pt).toBe(0)
    expect(base.absolute_min_font_size_pt).toBe(0)
    expect(validateSpecShape(base)).toBeNull()
  })
})

describe('golden 向量（与 Python 同一份）', () => {
  it('列表', () => {
    expect(listJournals(fixture())).toEqual(V.list)
  })

  for (const c of V.cases) {
    it(`折叠 ${c.journal_id}`, () => {
      const cat = fixture()
      expect(journalOverride(c.journal_id, cat)).toEqual(c.override)
      expect(journalSources(c.journal_id, cat)).toEqual(c.sources)
      const merged = journalProfile(c.journal_id, cat) as unknown as Record<string, unknown>
      for (const [path, want] of Object.entries(c.merged_fields)) {
        let node: unknown = merged
        for (const part of path.split('.')) node = (node as Record<string, unknown>)[part]
        expect(node).toEqual(want)
      }
    })
  }

  for (const c of V.invalid) {
    it(`失败向量 ${c.name} → ${c.code}`, () => {
      expect(codeOf(() => validateCatalog(applyOps(fixture(), c.ops)))).toBe(c.code)
    })
  }

  it('每个校验失败码都至少有一条向量', () => {
    const covered = new Set(V.invalid.map((c) => c.code))
    expect(covered).toEqual(new Set(JOURNAL_ERROR_CODES.filter((c) => c !== 'journal_unknown')))
  })

  it('未知期刊', () => {
    for (const fn of [journalOverride, journalSources, journalProfile]) {
      expect(codeOf(() => fn('nope', fixture()))).toBe(V.unknown_journal_code)
    }
  })
})

describe('折叠语义', () => {
  it('期刊没写的项不补：fx-delta 就是 journal-base-v1 本身', () => {
    const cat = fixture()
    expect(journalOverride('fx-delta', cat)).toEqual({})
    const p = journalProfile('fx-delta', cat)
    const base = loadProfile(JOURNAL_BASE_PROFILE_ID)
    expect({ ...p, derived_from: undefined, journal: undefined }).toEqual({
      ...base,
      derived_from: undefined,
      journal: undefined,
    })
  })

  it('期刊 rules 覆盖出版社 rules，其余继承；出处范围随之', () => {
    const cat = fixture()
    expect(journalOverride('fx-beta', cat)).toMatchObject({ widths_mm: { single: 85, double: 180 } })
    expect(journalSources('fx-beta', cat)['widths_mm.double'].scope).toBe('journal')
    expect(journalSources('fx-alpha', cat)['widths_mm.double'].scope).toBe('publisher')
  })

  it('只写了 latin_accepted 时 latin 不被补，且不拿课题组规范填坐标轴', () => {
    const p = journalProfile('fx-beta', fixture())
    const lab = loadProfile('lab-publication-v1')
    expect(p.font_family.latin).toBe('')
    expect(p.axis_policy).not.toEqual(lab.axis_policy)
    expect(p.derived_from).toBe(JOURNAL_BASE_PROFILE_ID)
  })

  it('链接形态：html 锚 / pdf 页 / 裸 url', () => {
    const s = journalSources('fx-alpha', fixture())
    expect(s['widths_mm.single'].link.endsWith('/artwork#:~:text=FIXTURE%20single')).toBe(true)
    expect(s.min_raster_dpi.link.endsWith('/guide.pdf#page=12')).toBe(true)
    expect(s['widths_mm.double'].link).toBe(s['widths_mm.double'].url)
  })
})
