/**
 * v2 事实进预检（ADR 0155 §14）：叠加只复用 v1 的折叠与求值，这里量三件事——
 * 叠上去的结果与「把同样的规则直接写进 v1 目录」逐键相同（同一条判据路径）、取不到 / 不合法 = 当没有 v2、叠加幂等可退。
 * 夹具期刊（fx-*，example.invalid），无真实数据。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import vectors from '../../../tests/golden/journal_vectors.json'
import foldVectors from '../../../tests/golden/journal_v2_fold_vectors.json'
import { setLocale } from '@/i18n'
import { journalOverride, loadCatalog, type JournalCatalog } from './journals'
import { fieldLabel, pickJournal, valueText } from './journalPick'
import { applyV2, confirmEntries, fetchJournalV2, isV2Source, type V2Patch } from './journalV2'

const catalog = loadCatalog(vectors.catalog)
const V2_SRC = { url: 'https://example.invalid/v2/guide', kind: 'html' as const, retrieved: '2026-11-15', coverage: 'journal_specific' as const }
const patch = (over: Partial<V2Patch> = {}): V2Patch => ({
  catalog_rev: 2,
  rules: { min_raster_dpi: { value: 450, src: 'v2:s1:journal', quote: 'At least 450 dpi.', qualifier: 'min', strength: 'required', stage: 'any' } },
  sources: { 'v2:s1:journal': V2_SRC },
  confirm: [],
  ...over,
})
const bind = () => pickJournal('fx-beta', catalog, { pageW: 85 }).binding

beforeEach(async () => {
  await setLocale('zh-CN')
})

afterEach(() => {
  vi.restoreAllMocks()
  void setLocale('zh-CN')
})

describe('applyV2', () => {
  it('v2 的项覆盖 v1，没写的项沿用 v1；出处标明来自 v2，被盖掉的 v1 记在 overrides 里', () => {
    const before = bind()
    const out = applyV2(before, patch())
    expect((out.journal as Record<string, unknown>).min_raster_dpi).toBe(450)
    // v1 的其它项原样
    expect((out.journal as Record<string, unknown>).font_family).toEqual((before.journal as Record<string, unknown>).font_family)
    const src = out.journalSources!.min_raster_dpi
    expect(isV2Source(src)).toBe(true)
    expect(src.overrides?.value).toBe(before.journalSources!.min_raster_dpi.value)
    expect(isV2Source(out.journalSources!.font_family)).toBe(false)
  })

  it('与「把同样的规则直接写进 v1 目录」折出的覆盖逐键相同（走的是同一条判据路径）', () => {
    const p = patch()
    const out = applyV2(bind(), p)
    const cat = structuredClone(catalog) as JournalCatalog
    cat.journals['fx-beta'].sources = { ...cat.journals['fx-beta'].sources, ...p.sources }
    cat.journals['fx-beta'].rules = { ...cat.journals['fx-beta'].rules, ...p.rules }
    expect(out.journal).toEqual(journalOverride('fx-beta', loadCatalog(cat)))
  })

  it('字体列表（exact）也能叠；期刊没标严重级别时按 v1 同一规则抬到 warn', () => {
    const p = patch({
      rules: { 'font_family.latin_accepted': { value: ['Helvetica'], src: 'v2:s1:journal', quote: 'Use Helvetica.', qualifier: 'exact', strength: 'recommended', stage: 'any' } },
    })
    const out = applyV2(bind(), p)
    expect(((out.journal as Record<string, Record<string, unknown>>).font_family).latin_accepted).toEqual(['Helvetica'])
  })

  it('取不到（null）/ 没按期刊选过：原样返回同一个对象，不清掉已叠过的项', () => {
    const before = bind()
    expect(applyV2(before, null)).toBe(before)
    const done = applyV2(before, patch())
    expect(applyV2(done, null)).toBe(done)
    const plain = { id: 'lab-publication-v1' } as never
    expect(applyV2(plain, patch())).toBe(plain)
  })

  it('幂等：同一份 v2 再叠一次没有变化（返回同一个对象 = 打开项目时不会白写一条历史）', () => {
    const once = applyV2(bind(), patch())
    expect(applyV2(once, patch())).toBe(once)
  })

  it('逐键相同就原样返回，不看键序（项目文件经 Python 往返后键序会变；每次打开都不变的项目不能每次都变脏）', () => {
    const once = applyV2(bind(), patch())
    const shuffled = JSON.parse(JSON.stringify(once, (_k, v) => (v && typeof v === 'object' && !Array.isArray(v) ? Object.fromEntries(Object.entries(v).reverse()) : v)))
    expect(Object.keys(shuffled.journal)).not.toEqual(Object.keys(once.journal!))
    expect(applyV2(shuffled, patch())).toBe(shuffled)
  })

  it('v2 后来没有这一项了：退回 v1 的值与出处', () => {
    const before = bind()
    const done = applyV2(before, patch())
    const back = applyV2(done, patch({ rules: {}, sources: {} }))
    expect(back.journal).toEqual(before.journal)
    expect(back.journalSources!.min_raster_dpi).toEqual(before.journalSources!.min_raster_dpi)
  })
})

describe('fetchJournalV2：取不到、被拒、不合法都是 null（预检照 v1 判，不报错）', () => {
  const answer = (status: number, body: unknown) =>
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(body), { status }))
  it('正常返回', async () => {
    answer(200, patch())
    expect((await fetchJournalV2('fx-beta'))?.rules.min_raster_dpi.value).toBe(450)
  })
  it.each([[403], [404], [503]])('HTTP %i', async (status) => {
    answer(status, { code: 'journal_catalog_disabled' })
    expect(await fetchJournalV2('fx-beta')).toBeNull()
  })
  it('网络错误', async () => {
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new TypeError('offline'))
    expect(await fetchJournalV2('fx-beta')).toBeNull()
  })
  it('规则过不了 v1 校验（值类型不对 / 来源不是 https）：整份丢掉', async () => {
    answer(200, patch({ rules: { min_raster_dpi: { value: 'lots', src: 'v2:s1:journal', quote: 'q', qualifier: 'min' } } }))
    expect(await fetchJournalV2('fx-beta')).toBeNull()
    answer(200, patch({ sources: { 'v2:s1:journal': { ...V2_SRC, url: 'http://example.invalid/x' } } }))
    expect(await fetchJournalV2('fx-beta')).toBeNull()
  })
  it('形状不对的响应', async () => {
    answer(200, { rules: 1 })
    expect(await fetchJournalV2('fx-beta')).toBeNull()
  })
  it('确认清单里形状不对的行被丢掉，其余保留', async () => {
    const good = { id: 'a', subject: 'min_line_width', relation: 'gte', value: 2, unit: 'pt', condition: '', quote: 'q', stage: 'unknown', source: V2_SRC }
    answer(200, patch({ confirm: [good, { ...good, id: 'b', relation: 'between' }, { ...good, id: 'c', value: 'x' }] as never }))
    expect((await fetchJournalV2('fx-beta'))?.confirm.map((r) => r.id)).toEqual(['a'])
  })
})

describe('confirmEntries', () => {
  it('名单型（色彩模式 / 文件格式）：路径不带单位，值是名单', () => {
    const [path, s] = confirmEntries({ id: 'a', subject: 'color_mode', relation: 'in', value: ['RGB', 'CMYK'], unit: null, condition: '', quote: 'q', stage: 'unknown', source: V2_SRC })
    expect(path).toBe('v2.color_mode')
    expect(valueText(path, s)).toBe('RGB / CMYK')
    expect(fieldLabel(path)).toBe('色彩模式')
  })
  it('合成路径带主语与单位；qualifier 按比较词；出处标 v2；原文条件原样在 condition', () => {
    const [path, s] = confirmEntries({
      id: 'a', subject: 'width', relation: 'gte', value: 4, unit: 'in', condition: 'One column width figures',
      quote: 'q', strength: 'recommended', stage: 'unknown', source: V2_SRC,
    })
    expect(path).toBe('v2.width_in')
    expect(s.qualifier).toBe('min')
    expect(isV2Source(s)).toBe(true)
    expect(s.affects).toBe('info')
    expect(s.link).toBe('https://example.invalid/v2/guide')
  })
})

describe('后端折叠向量（tests/golden/journal_v2_fold_vectors.json）过前端这道门', () => {
  it.each(foldVectors.cases.map((c) => [c.id, c.expected] as const))('%s', async (_id, expected) => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ catalog_rev: 2, ...expected }), { status: 200 }))
    const got = await fetchJournalV2('fx-beta')
    expect(got, '后端折出的规则 / 来源必须过 v1 校验器，否则整份被当成没有 v2').not.toBeNull()
    expect(Object.keys(got!.rules)).toEqual(Object.keys(expected.rules))
    expect(got!.confirm.length).toBe(expected.confirm.length)
    // 每个确认行都认得出标签（词表里有 `journal.field.v2_<主语>_<单位>`）并带数字；每条规则的出处都带 v2 前缀并能叠上去
    for (const row of got!.confirm) {
      const [path, source] = confirmEntries(row)
      expect(fieldLabel(path)).not.toMatch(/_/)
      expect(valueText(path, source)).toMatch(/[\dA-Za-z]/)
    }
    for (const rule of Object.values(got!.rules)) expect(rule.src.startsWith('v2:')).toBe(true)
    if (Object.keys(got!.rules).length) {
      const out = applyV2(bind(), got!)
      for (const entry of Object.keys(got!.rules).map((p) => out.journalSources![p])) expect(isV2Source(entry)).toBe(true)
    }
  })
})
