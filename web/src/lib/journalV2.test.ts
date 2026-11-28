/**
 * v2 事实进预检（ADR 0155 §14）：叠加只复用 v1 的折叠与求值，这里量三件事——
 * 叠上去的结果与「把同样的规则直接写进 v1 目录」逐键相同（同一条判据路径）、取不到 / 不合法 = 当没有 v2、叠加幂等可退。
 * 夹具期刊（fx-*，example.invalid），无真实数据。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import vectors from '../../../tests/golden/journal_vectors.json'
import foldVectors from '../../../tests/golden/journal_v2_fold_vectors.json'
import { setLocale } from '@/i18n'
import { journalOverride, listJournals, loadCatalog, type JournalCatalog } from './journals'
import { fieldLabel, pickJournal, valueText } from './journalPick'
import {
  applyV2,
  confirmEntries,
  fetchJournalV2,
  fetchJournalV2Index,
  fetchJournalV2Outcome,
  isV2Source,
  pickV2OnlyJournal,
  v2OnlySummaries,
  type V2Patch,
} from './journalV2'

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
  vi.unstubAllGlobals()
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

  it('绑定里已显式写的严重级别不被 v2 的自动抬升盖掉（含降级）；没写过才抬，去掉 v2 项时撤回', () => {
    const fontPatch = patch({
      rules: { 'font_family.latin_accepted': { value: ['Helvetica'], src: 'v2:s1:journal', quote: 'Use Helvetica.', qualifier: 'exact', strength: 'recommended', stage: 'any' } },
    })
    const before = bind()
    for (const level of ['error', 'suggestion']) {
      const explicit = structuredClone(before)
      explicit.journal = { ...(explicit.journal as object), severity: { 'font-family-substituted': level } }
      const out = applyV2(explicit, fontPatch)
      expect((out.journal as Record<string, Record<string, unknown>>).severity['font-family-substituted']).toBe(level)
      const back = applyV2(out, patch({ rules: {}, sources: {} }))
      expect((back.journal as Record<string, Record<string, unknown>>).severity['font-family-substituted']).toBe(level)
    }
    // 没写过：抬到 warn，去掉后撤回
    const plain = structuredClone(before)
    const sev = (plain.journal as Record<string, Record<string, unknown>>).severity
    if (sev) delete sev['font-family-substituted']
    const up = applyV2(plain, fontPatch)
    expect((up.journal as Record<string, Record<string, unknown>>).severity?.['font-family-substituted']).toBe('warn')
    expect(applyV2(up, fontPatch)).toBe(up)
    const down = applyV2(up, patch({ rules: {}, sources: {} }))
    expect((down.journal as Record<string, Record<string, unknown>>).severity?.['font-family-substituted']).toBeUndefined()
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
  it('署名（ADR 0213 三期）：渲染前再校验一遍，单条不合规只丢那一条；形状不对 = 没有；老引擎不带 = 空', async () => {
    answer(200, { ...patch(), thanks: { a: '  李明 ', b: 'x@y', c: 'https://z', d: 'a\u202eb', e: 7 } })
    expect((await fetchJournalV2('fx-beta'))?.thanks).toEqual({ a: '李明' })
    answer(200, { ...patch(), thanks: ['李明'] })
    expect((await fetchJournalV2('fx-beta'))?.thanks).toEqual({})
    answer(200, patch())
    expect((await fetchJournalV2('fx-beta'))?.thanks).toEqual({})
  })
  it('确认清单里形状不对的行被丢掉，其余保留', async () => {
    const good = { id: 'a', subject: 'min_line_width', relation: 'gte', value: 2, unit: 'pt', condition: '', quote: 'q', stage: 'unknown', source: V2_SRC }
    answer(200, patch({ confirm: [good, { ...good, id: 'b', relation: 'between' }, { ...good, id: 'c', value: 'x' }] as never }))
    expect((await fetchJournalV2('fx-beta'))?.confirm.map((r) => r.id)).toEqual(['a'])
  })
})

describe('confirmEntries', () => {
  it('文件格式按大写显示（TIFF / EPS），补丁里的数据不动', () => {
    const row = { id: 'a', subject: 'file_format', relation: 'in' as const, value: ['tiff', 'eps'], unit: null, condition: '', quote: 'q', stage: 'unknown', source: V2_SRC }
    const [path, s] = confirmEntries(row)
    expect(valueText(path, s)).toBe('TIFF / EPS')
    expect(row.value).toEqual(['tiff', 'eps'])
  })
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

describe('v2-only 期刊（选刊列表，ADR 0155 §14）', () => {
  const entry = (id: string, over: Record<string, unknown> = {}) => ({
    id,
    label: `Label ${id}`,
    publisher: 'fx-pub',
    publisher_label: 'Fixture Publisher',
    issn: '',
    eissn: '',
    aliases: [] as string[],
    ...over,
  })

  it('列表只收 v1 目录里没有的刊（按 id）；字段补成选刊列表认得的形状，eissn 在没有 issn 时顶上', () => {
    const v1 = listJournals(catalog)[0].journal_id
    const out = v2OnlySummaries([entry(v1), entry('zz-only', { eissn: '1234-5678', aliases: ['ZZ'] })], catalog)
    expect(out.map((j) => j.journal_id)).toEqual(['zz-only'])
    expect(out[0]).toMatchObject({ label: 'Label zz-only', publisher: 'fx-pub', publisher_label: 'Fixture Publisher', issn: '1234-5678', aliases: ['ZZ'] })
  })

  it('index 取不到 / 形状不对 = null（列表里就没有这些刊，不报错）', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('{}', { status: 503 })))
    expect(await fetchJournalV2Index()).toBeNull()
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ journals: 'x' }), { status: 200 })))
    expect(await fetchJournalV2Index()).toBeNull()
    vi.stubGlobal('fetch', vi.fn(async () => Promise.reject(new Error('offline'))))
    expect(await fetchJournalV2Index()).toBeNull()
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(JSON.stringify({ catalog_rev: 3, journals: [entry('a'), { id: 1 }] }), { status: 200 })),
    )
    expect((await fetchJournalV2Index())?.map((j) => j.id)).toEqual(['a'])
  })

  it('取单刊 v2 的结果分终态：ready / 额度 / 其余都当没网（不抛）', async () => {
    const reply = (status: number, body: unknown) => vi.fn(async () => new Response(JSON.stringify(body), { status }))
    vi.stubGlobal('fetch', reply(200, patch()))
    expect((await fetchJournalV2Outcome('zz-only')).state).toBe('ready')
    vi.stubGlobal('fetch', reply(429, { code: 'quota_hour', retry_after_s: 60 }))
    expect(await fetchJournalV2Outcome('zz-only')).toEqual({ state: 'quota', patch: null })
    for (const status of [403, 404, 409, 503]) {
      vi.stubGlobal('fetch', reply(status, { code: 'x' }))
      expect(await fetchJournalV2Outcome('zz-only')).toEqual({ state: 'offline', patch: null })
    }
    vi.stubGlobal('fetch', vi.fn(async () => Promise.reject(new Error('offline'))))
    expect(await fetchJournalV2Outcome('zz-only')).toEqual({ state: 'offline', patch: null })
    // 200 但补丁不合法：同样当取不到
    vi.stubGlobal('fetch', reply(200, { rules: 1 }))
    expect(await fetchJournalV2Outcome('zz-only')).toEqual({ state: 'offline', patch: null })
  })

  it('选 v2-only 刊：绑定只含 v2 无条件事实折出的规则，v1 没有的项不补默认；带条件的只在「需要你确认」', () => {
    const row = {
      id: 'c1',
      subject: 'raster_resolution',
      relation: 'gte' as const,
      value: 1200,
      unit: 'dpi',
      condition: 'line art',
      quote: 'Line art: 1200 dpi.',
      stage: 'production',
      source: V2_SRC,
    }
    const p = patch({ confirm: [row] })
    const { binding, label } = pickV2OnlyJournal(entry('zz-only'), p, { pageW: 85 })
    expect(label).toBe('Label zz-only')
    expect(binding.journalRef).toEqual({ id: 'zz-only', catalogRev: 2, retrieved: '2026-11-15', label: 'Label zz-only' })
    // 只有 v2 那一项；没有别的规则被补上
    expect(binding.journal).toEqual({ min_raster_dpi: 450 })
    expect(Object.keys(binding.journalSources!)).toEqual(['min_raster_dpi'])
    expect(isV2Source(binding.journalSources!.min_raster_dpi)).toBe(true)
    expect(binding.journalSources!.min_raster_dpi.overrides).toBeNull()
    // 带条件的行不进绑定
    expect(JSON.stringify(binding)).not.toContain('Line art')
  })

  it('v2 没有任何无条件事实：绑定照样建（只有中性底），不编造规则', () => {
    const { binding } = pickV2OnlyJournal(entry('zz-only'), patch({ rules: {}, sources: {}, confirm: [] }), { pageW: 85 })
    expect(binding.journalRef?.id).toBe('zz-only')
    expect(binding.journal).toBeUndefined()
    expect(binding.journalSources ?? {}).toEqual({})
  })

  it('离线重开：取不到 v2 时 applyV2 原样返回，已存的绑定照判', () => {
    const { binding } = pickV2OnlyJournal(entry('zz-only'), patch(), { pageW: 85 })
    expect(applyV2(binding, null)).toBe(binding)
    // 联网后再叠同一份 = 幂等，不变脏
    expect(applyV2(binding, patch())).toBe(binding)
  })
})
