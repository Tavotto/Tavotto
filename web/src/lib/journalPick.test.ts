/**
 * 期刊选择器 / 出处卡的判据层（ADR 0155 PR 2）。用的全是夹具期刊（`fx-*`、`example.invalid`，
 * 来自 golden 向量），不含任何真实期刊数据。
 */
import { beforeEach, describe, expect, it } from 'vitest'
import vectors from '../../../tests/golden/journal_vectors.json'
import { setLocale } from '@/i18n'
import { journalProfile, journalSources, listJournals, loadCatalog, type JournalFieldSource } from './journals'
import {
  adviceSentence,
  cardConclusion,
  groupByPublisher,
  highlightQuote,
  nearestColumnWidth,
  pickJournal,
  requirementRows,
  searchJournals,
  sourcesForCheck,
  valueText,
} from './journalPick'
import { derivePageColumns } from './profile'

const CAT = loadCatalog(vectors.catalog)
const ALL = listJournals(CAT)

/** 自写的小目录：Science 式自有栏位 + Springer 式「只展示不判」的 174 mm */
const MINI = loadCatalog({
  schema: 1,
  catalog_rev: 1,
  publishers: {
    'fx-multi': {
      label: 'Fixture Multi',
      sources: { m1: { url: 'https://example.invalid/fx-multi/a', kind: 'html', retrieved: '2000-01-02' } },
    },
  },
  journals: {
    'fx-sci': {
      label: 'Fixture Sci',
      publisher: 'fx-multi',
      sources: { m1: { url: 'https://example.invalid/fx-sci/a', kind: 'html', retrieved: '2000-01-02' } },
      rules: {
        page_columns: {
          value: [
            { id: 'col2', label: { zh: '2 栏', en: '2 columns' }, width_mm: 121, qualifier: 'exact' },
            { id: 'col3', label: { zh: '3 栏', en: '3 columns' }, width_mm: 184, qualifier: 'exact' },
          ],
          src: 'm1',
          quote: 'FIXTURE 2 columns 121 mm, 3 columns 184 mm',
        },
      },
    },
    'fx-disp': {
      label: 'Fixture Display',
      publisher: 'fx-multi',
      sources: { m1: { url: 'https://example.invalid/fx-disp/a', kind: 'html', retrieved: '2000-01-02' } },
      rules: {
        'info.width_double_mm': { value: 174, src: 'm1', quote: 'FIXTURE double column 174 mm', qualifier: 'typical' },
      },
    },
  },
})

beforeEach(async () => {
  await setLocale('zh-CN')
})

describe('选择器搜索与分组', () => {
  it('名称、缩写、ISSN（带不带连字符都认）都能搜到；大小写与全角不敏感', () => {
    expect(searchJournals(ALL, 'fixture alpha').map((j) => j.journal_id)).toEqual(['fx-alpha'])
    expect(searchJournals(ALL, 'fxa').map((j) => j.journal_id)).toEqual(['fx-alpha'])
    expect(searchJournals(ALL, '0000-0000').map((j) => j.journal_id)).toEqual(['fx-alpha'])
    expect(searchJournals(ALL, '00000000').map((j) => j.journal_id)).toEqual(['fx-alpha'])
    expect(searchJournals(ALL, 'ＦｘＡ').map((j) => j.journal_id)).toEqual(['fx-alpha'])
  })

  it('空查询给全部；没有匹配给空', () => {
    expect(searchJournals(ALL, '  ')).toHaveLength(ALL.length)
    expect(searchJournals(ALL, 'nosuchjournal')).toEqual([])
  })

  it('按出版社分组，同一出版社的期刊在同一组', () => {
    const groups = groupByPublisher(ALL)
    const press = groups.find((g) => g.publisher === 'fx-press')!
    expect(press.journals.map((j) => j.journal_id).sort()).toEqual(
      ALL.filter((j) => j.publisher === 'fx-press').map((j) => j.journal_id).sort(),
    )
    expect(new Set(groups.map((g) => g.publisher)).size).toBe(groups.length)
  })
})

describe('出处卡：一句话与高亮', () => {
  const src = (id: string, path: string) => journalSources(id, CAT)[path]

  it('结论是「字段 值」', () => {
    const s = src('fx-beta', 'widths_mm.double')
    expect(cardConclusion('widths_mm.double', s)).toBe(`双栏 ${s.value} mm`)
  })

  it('摘录里命中的数按数据里的 mark 标出；没给 mark 就按数值找，且 7 不会命中 17 或 7.5', () => {
    expect(highlightQuote('width 183 mm', { mark: '', value: 183, value_alt: null })).toEqual([
      { text: 'width ', hit: false },
      { text: '183', hit: true },
      { text: ' mm', hit: false },
    ])
    expect(highlightQuote('font 17 pt or 7.5 pt', { mark: '', value: 7, value_alt: null })).toEqual([
      { text: 'font 17 pt or 7.5 pt', hit: false },
    ])
    // mark 优先，且一字不差
    expect(highlightQuote('字高统一为7 pt', { mark: '字高统一为7 pt', value: 7, value_alt: null })).toEqual([
      { text: '字高统一为7 pt', hit: true },
    ])
    // value_alt（如 Nature 1.5 栏 120 或 136）也能找
    expect(highlightQuote('120 or 136 mm', { mark: '', value: 999, value_alt: 136 })[1]).toEqual({
      text: '136',
      hit: true,
    })
  })

  it('typical / recommended / range 显示原话，不显示成一个像判据的数', () => {
    const zeta = src('fx-zeta', 'min_effective_font_size_pt')
    expect(zeta.qualifier).toBe('typical')
    expect(valueText('min_effective_font_size_pt', zeta)).toBe(zeta.mark || zeta.quote)
    const wide = src('fx-cols', 'page_columns')
    expect(valueText('page_columns', wide)).toContain('90 mm')
  })

  it('min / max 带方向记号；栏位列出全部「名字 + 宽度」', () => {
    expect(valueText('max_font_size_pt', src('fx-zeta', 'max_font_size_pt'))).toBe('≥ 5 pt')
    const cols = src('fx-cols-inherit', 'page_columns')
    expect(valueText('page_columns', cols)).toBe('1 栏 57 mm / 2 栏 121 mm / 3 栏 184 mm')
  })

  it('建议性字号：一张卡里正文与上下标合成一句（Elsevier 式）', () => {
    const entries = Object.fromEntries(sourcesForCheck('font-size-recommended', journalSources('fx-adv', CAT)))
    expect(Object.keys(entries).sort()).toEqual(['min_effective_font_size_pt', 'min_script_font_size_pt'])
    expect(adviceSentence('Elsevier', entries)).toBe(
      'Elsevier 说明 7 pt 为正文最小字号，6 pt 为上下标最小字号',
    )
  })

  it('英文界面：同一张卡换成英文句子', async () => {
    await setLocale('en-US')
    const entries = Object.fromEntries(sourcesForCheck('font-size-recommended', journalSources('fx-adv', CAT)))
    expect(adviceSentence('Elsevier', entries)).toBe(
      'Elsevier states 7 pt as the minimum for body text and 6 pt for sub/superscripts',
    )
  })
})

describe('「该期刊未规定」', () => {
  it('期刊没写的要紧项各出一行 source=null，不拿别的规范补', () => {
    const rows = requirementRows(journalSources('fx-delta', CAT))
    expect(rows.map((r) => r.path)).toEqual([
      'page_columns',
      'min_effective_font_size_pt',
      'min_raster_dpi',
      'line_widths_pt',
    ])
    expect(rows.every((r) => r.source === null)).toBe(true)
  })

  it('写了的项有出处、没写的仍是 null；展示项垫底', () => {
    const byPath = new Map(requirementRows(journalSources('fx-adv-own', CAT)).map((r) => [r.path, r]))
    expect(byPath.get('min_effective_font_size_pt')!.source).not.toBeNull()
    expect(byPath.get('min_raster_dpi')!.source).toBeNull()
    const rows = requirementRows(journalSources('fx-beta', CAT))
    expect(rows.find((r) => r.path === 'widths_mm.double')!.source).not.toBeNull()
    expect(rows.at(-1)!.path.startsWith('info.')).toBe(true)
  })
})

describe('选期刊：页宽跟栏', () => {
  it('自有栏位：取离当前页宽最近的一栏（同距取窄）', () => {
    const cols = derivePageColumns(journalProfile('fx-sci', MINI))
    expect(cols.map((c) => c.width_mm)).toEqual([121, 184])
    expect(nearestColumnWidth(cols, 90)).toBe(121)
    expect(nearestColumnWidth(cols, 180)).toBe(184)
    expect(nearestColumnWidth(cols, (121 + 184) / 2)).toBe(121)
  })

  it('期刊没规定栏宽：页宽不动（null）', () => {
    expect(pickJournal('fx-delta', CAT, { pageW: 90 }).pageW).toBeNull()
  })

  it('Springer 式 174 mm 只展示、不参与：栏位里没有它，页宽不动', () => {
    const pick = pickJournal('fx-disp', MINI, { pageW: 90 })
    expect(pick.pageW).toBeNull()
    expect(journalSources('fx-disp', MINI)['info.width_double_mm'].affects).toBe('info')
  })

  it('绑定存覆盖 + journalRef + 精简出处，底是中性底', () => {
    const pick = pickJournal('fx-sci', MINI, { pageW: 90, follow: true })
    const b = pick.binding
    expect(b.id).toBe('journal-base-v1')
    expect(b.journalRef).toMatchObject({ id: 'fx-sci', catalogRev: 1, label: 'Fixture Sci', retrieved: '2000-01-02' })
    expect(b.journal).toBeTruthy()
    expect(b.follow).toBe(true)
    const stored = b.journalSources as Record<string, JournalFieldSource>
    expect(stored.page_columns.quote).toContain('121 mm')
    expect(stored.page_columns.overrides).toBeNull()
    expect(pick.pageW).toBe(121)
  })
})

describe('要求清单的行', () => {
  it('severity.* 不是期刊的要求，不进清单；单栏排在双栏前；数组值带单位', () => {
    const rows = requirementRows(journalSources('fx-beta', CAT))
    const paths = rows.map((r) => r.path)
    expect(paths.some((p) => p.startsWith('severity.'))).toBe(false)
    expect(paths.indexOf('widths_mm.single')).toBeLessThan(paths.indexOf('widths_mm.double'))
    const lines = rows.find((r) => r.path === 'line_widths_pt')!
    expect(valueText('line_widths_pt', lines.source!)).toBe('0.5 / 1 pt')
  })
})
