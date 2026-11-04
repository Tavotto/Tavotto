/**
 * 内置期刊标准库（TS 侧）。与 Python 的 `tests/test_journal_catalog.py` 跑**同一份**
 * `tests/golden/journal_vectors.json`（夹具期刊，非真实数据；向量里连闭集常量一起比）。
 */
import { createHash } from 'node:crypto'
import { readdirSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import vectors from '../../../tests/golden/journal_vectors.json'
import shipped from '@journals'
import {
  ADVISORY_PATHS,
  BUMPED_SEVERITY,
  COVERAGES,
  EXACT_ONLY_PATHS,
  INFO_IDS,
  JOURNAL_BASE_PROFILE_ID,
  JOURNAL_ERROR_CODES,
  JournalCatalogError,
  KIND_OVERRIDES,
  QUALIFIER_DIRECTION,
  QUALIFIERS,
  SEVERITY_BUMP,
  STAGES,
  catalogRev,
  journalOverride,
  journalProfile,
  journalSources,
  listJournals,
  loadCatalog,
  validateCatalog,
} from './journals'
import {
  ADVISORY_ROLES,
  derivePageColumns,
  listProfiles,
  loadProfile,
  validateSpecShape,
} from './profile'
import { runSpec } from './preflight'

interface Op {
  path: string[]
  value?: unknown
  delete?: boolean
}
const V = vectors as unknown as {
  catalog: unknown
  catalog_rev: number
  list: unknown
  base_profile_id: string
  constants: Record<string, unknown>
  cases: Array<{
    journal_id: string
    override: unknown
    sources: unknown
    merged_fields: Record<string, unknown>
    page_columns: unknown
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

type Rules = { rules?: Record<string, { snapshot?: { file: string } }> }

describe('随包的期刊库', () => {
  it('随包的是一份合法的种子库：几十本头部期刊 + 出版社通用标准，没有测试夹具、没有假期刊', () => {
    expect(loadCatalog().schema).toBe(1)
    expect(catalogRev()).toBeGreaterThanOrEqual(1)
    const cat = shipped as {
      publishers: Record<string, unknown>
      journals: Record<string, { label: string; publisher: string }>
    }
    const ids = Object.keys(cat.journals)
    expect(ids.length).toBeGreaterThanOrEqual(30)
    expect(ids.length).toBeLessThanOrEqual(100)
    expect(listJournals().length).toBe(ids.length)
    // 夹具期刊只许在 golden 向量里；种子里一本假的都不能有（PR 1 复审遗留：7 本 -sample）
    expect(ids.filter((id) => id.startsWith('fx-') || id.endsWith('-sample'))).toEqual([])
    expect(JSON.stringify(shipped)).not.toContain('example.invalid')
    // 每本期刊都能折成覆盖、折成可预检的规范、列出处（加载已全量校验，这里再走一遍求值路径）
    for (const id of ids) {
      expect(journalProfile(id).profile_id).toBe(JOURNAL_BASE_PROFILE_ID)
      journalOverride(id)
      journalSources(id)
    }
  })

  it('种子引用的每张快照图都随包：文件在、是 webp、文件名就是内容的 sha256（内容寻址）', () => {
    const dir = resolve(__dirname, '../../public/assets/journal-catalog/snapshots')
    const files = new Set<string>()
    for (const o of [
      ...Object.values((shipped as { publishers: Record<string, Rules> }).publishers),
      ...Object.values((shipped as { journals: Record<string, Rules> }).journals),
    ]) {
      for (const r of Object.values(o.rules ?? {})) if (r.snapshot) files.add(r.snapshot.file)
    }
    expect(files.size).toBeGreaterThan(0)
    for (const f of files) {
      const bytes = readFileSync(resolve(dir, f.replace(/^snapshots\//, '')))
      expect(bytes.subarray(0, 4).toString('latin1')).toBe('RIFF')
      expect(bytes.subarray(8, 12).toString('latin1')).toBe('WEBP')
      expect(f).toBe(`snapshots/${createHash('sha256').update(bytes).digest('hex')}.webp`)
    }
    // 包里没有没人引用的快照（不白占体积）
    const onDisk = readdirSync(dir)
    expect(onDisk.filter((n) => !files.has(`snapshots/${n}`))).toEqual([])
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
    expect(base.widths_mm.single).toBeNull()
    expect(base.widths_mm.double).toBeNull()
    expect(validateSpecShape(base)).toBeNull()
    const lab = loadProfile('lab-publication-v1')
    for (const k of Object.keys(lab.severity)) expect(base.severity).toHaveProperty(k)
  })
})

describe('golden 向量（与 Python 同一份）', () => {
  it('闭集常量与 Python 逐项相同', () => {
    expect(V.constants).toEqual({
      info_ids: [...INFO_IDS],
      qualifiers: [...QUALIFIERS],
      stages: [...STAGES],
      coverages: [...COVERAGES],
      exact_only_paths: [...EXACT_ONLY_PATHS],
      severity_bump: SEVERITY_BUMP,
      bumped_severity: BUMPED_SEVERITY,
      kind_overrides: KIND_OVERRIDES,
      qualifier_direction: QUALIFIER_DIRECTION,
      advisory_paths: ADVISORY_PATHS,
      advisory_qualifiers: ['recommended', 'typical'],
      advisory_check: 'font-size-recommended',
      advisory_roles: [...ADVISORY_ROLES],
      error_codes: [...JOURNAL_ERROR_CODES],
    })
  })

  it('列表与 catalog_rev', () => {
    expect(listJournals(fixture())).toEqual(V.list)
    expect(catalogRev(fixture())).toBe(V.catalog_rev)
  })

  for (const c of V.cases) {
    it(`折叠 ${c.journal_id}`, () => {
      const cat = fixture()
      expect(journalOverride(c.journal_id, cat)).toEqual(c.override)
      expect(journalSources(c.journal_id, cat)).toEqual(c.sources)
      const merged = journalProfile(c.journal_id, cat) as unknown as Record<string, unknown>
      expect(derivePageColumns(merged as never)).toEqual(c.page_columns)
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

  it('期刊 rules 覆盖出版社 rules：两种归属与被覆盖的原值', () => {
    const cat = fixture()
    const dbl = journalSources('fx-beta', cat)['widths_mm.double']
    expect([dbl.rule_scope, dbl.source_scope]).toEqual(['journal', 'journal'])
    expect(dbl.overrides?.value).toBe(175)
    const font = journalSources('fx-beta', cat).min_effective_font_size_pt
    expect([font.rule_scope, font.source_scope]).toEqual(['journal', 'publisher'])
    expect(journalSources('fx-beta', cat)['widths_mm.single'].overrides).toBeNull()
  })

  it('info 项只展示不折叠；unset 只对该刊撤掉出版社那条', () => {
    const cat = fixture()
    expect(journalSources('fx-beta', cat)['info.one_half_mm'].affects).toBe('info')
    expect(journalOverride('fx-beta', cat)).not.toHaveProperty('info')
    expect(journalOverride('fx-alpha', cat)).toHaveProperty('min_raster_dpi')
    expect(journalOverride('fx-epsilon', cat)).not.toHaveProperty('min_raster_dpi')
    expect(journalSources('fx-epsilon', cat).min_raster_dpi.unset).toBe(true)
    expect(journalProfile('fx-epsilon', cat).min_raster_dpi).toBe(300)
  })

  it('严重度随规则抬升：写了的检查 suggestion→warn，显式写的优先，只抬不降', () => {
    const cat = fixture()
    const base = loadProfile(JOURNAL_BASE_PROFILE_ID)
    const alpha = journalProfile('fx-alpha', cat)
    expect(alpha.severity['line-width-off-preset']).toBe('warn')
    expect(alpha.severity['page-width']).toBe('error')
    const beta = journalProfile('fx-beta', cat)
    expect(beta.severity['font-family-substituted']).toBe('warn')
    expect(beta.severity['tick-direction']).toBe('suggestion')
    expect(beta.severity['font-too-small']).toBe(base.severity['font-too-small'])
  })

  it('限定词折叠：typical / recommended / 方向不符的不进 override，exact 与方向相符的 min/max 才折', () => {
    const cat = fixture()
    const over = journalOverride('fx-zeta', cat)
    expect(over.legend_policy).toEqual({ max_font_size_pt: 9 })
    expect(over.min_raster_dpi).toBe(450)
    for (const gone of ['min_effective_font_size_pt', 'max_font_size_pt', 'font_family']) {
      expect(over).not.toHaveProperty(gone)
    }
    const src = journalSources('fx-zeta', cat)
    expect(src.min_effective_font_size_pt.affects).toBe('advice')
    expect(src['font_family.latin_accepted'].affects).toBe('info')
    expect(src.max_font_size_pt.affects).toBe('info')
    expect(src.min_raster_dpi.affects).toBe('preflight')
  })

  it('期刊自有栏位：page_columns 为准，widths_mm 由 exact 栏位派生；没写则由 widths_mm 派生', () => {
    const cat = fixture()
    const three = journalOverride('fx-cols-inherit', cat)
    expect(three.widths_mm).toEqual({ single: 57, double: 184 })
    expect(three.page_columns?.map((c) => c.id)).toEqual(['col1', 'col2', 'col3'])
    expect(journalOverride('fx-cols', cat).widths_mm).toEqual({
      tolerance_mm: 2,
      single: 90,
      double: null,
    })
    expect(derivePageColumns(loadProfile('lab-publication-v1')).map((c) => c.id)).toEqual([
      'single',
      'double',
    ])
    expect(derivePageColumns(loadProfile(JOURNAL_BASE_PROFILE_ID))).toEqual([])
  })

  it('建议性提示带期刊名与原话，不变成硬下限', () => {
    const cat = fixture()
    const over = journalOverride('fx-adv', cat)
    expect(over).not.toHaveProperty('min_effective_font_size_pt')
    const roles = Object.fromEntries((over.advisories ?? []).map((a) => [a.role, a]))
    expect(roles.body_min.journal).toBe('Fixture Advice Press')
    expect(roles.body_min.quote).toContain('FIXTURE 7 pt')
    expect(roles.script_min.value_pt).toBe(6)
    expect(journalProfile('fx-adv', cat).min_effective_font_size_pt).toBe(0)
  })

  it('栏宽 null 在 TS 求值器里同样表示没规定', () => {
    const spec = {
      page: { w_mm: 123, h_mm: 80 },
      panels: [],
      texts: [],
      objects: [],
    } as unknown as Parameters<typeof runSpec>[0]
    const plain = journalProfile('fx-delta', fixture())
    expect(runSpec(spec, plain).map((i) => i.id)).not.toContain('page-width')
  })

  it('page 为 12.0 与 12 同义，链接形态：html 锚 / pdf 页 / 裸 url', () => {
    const s = journalSources('fx-alpha', fixture())
    expect(s['widths_mm.single'].link.endsWith('/artwork#:~:text=FIXTURE%20single')).toBe(true)
    expect(s.min_raster_dpi.link.endsWith('/guide.pdf#page=12')).toBe(true)
    expect(s.min_raster_dpi.page).toBe(12)
    expect(s['widths_mm.double'].link).toBe(s['widths_mm.double'].url)
  })
})

describe('出版社只给「建议」栏宽时不收窄页宽（#1745 Codex P2）', () => {
  it('APS 原文允许单栏 / 1.5 栏 / 双栏、只给了单栏宽度：PRL 不把 85 mm 当唯一合规宽度', () => {
    const spec = journalProfile('physical-review-letters') as { widths_mm?: { single?: number | null } }
    expect(spec.widths_mm?.single ?? null).toBeNull()
  })
})
