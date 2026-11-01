/**
 * 期刊选择器与出处卡的**判据层**（ADR 0155 §3b / §4；组件只渲染这里算出来的东西）。
 *
 *   选期刊   `pickJournal`：中性底 `journal-base-v1` + 该期刊折出的覆盖 + `journalRef` + 精简出处，一份绑定；
 *            同时算出画布页宽该跟到哪一栏（只读 `derivePageColumns`，没写栏宽就不动页宽）
 *   搜索     `searchJournals`：名称 / 缩写（aliases）/ ISSN；`groupByPublisher` 按出版社分组
 *   出处卡   `fieldLabel` / `valueText` / `cardSentence` / `highlightQuote`：一句话 + 原文摘录里命中的数加 `<mark>`
 *
 * 期刊没写的项一律说「该期刊未规定」，**不拿任何别的规范补**；`typical` / `recommended` / `range` 的值显示原话，
 * 不显示成一个像判据的数。
 */
import { currentLocale, t as translate } from '@/i18n'
import {
  JOURNAL_BASE_PROFILE_ID,
  SEVERITY_BUMP,
  aliasKey,
  catalogRev,
  journalOverride,
  journalProfile,
  journalSources,
  listJournals,
  type JournalCatalog,
  type JournalFieldSource,
  type JournalSummary,
} from './journals'
import { PAGE_MM_MAX, PAGE_MM_MIN } from './pageRange'
import { derivePageColumns, type PageColumnSpec } from './profile'
import { bindingFor, builtinEntryById, type JournalPrev, type JournalRef, type SpecBinding } from './specBinding'

const jt = (key: string, values?: Record<string, unknown>) =>
  translate(`journal.${key}`, { ns: 'dialogs', ...(values ?? {}) })

/* ------------------------------- 搜索与分组 ------------------------------- */

const issnKey = (s: string) => aliasKey(s).replace(/[-\s]/g, '')

/** 名称 / 缩写 / ISSN 命中（NFKC 后小写、子串）；空查询 = 全部。ISSN 带不带连字符都认。 */
export function searchJournals(list: JournalSummary[], query: string): JournalSummary[] {
  const q = aliasKey(query.trim())
  if (!q) return list
  const qIssn = issnKey(q)
  return list.filter(
    (j) =>
      aliasKey(j.label).includes(q) ||
      j.aliases.some((a) => aliasKey(a).includes(q)) ||
      (qIssn !== '' && j.issn !== '' && issnKey(j.issn).includes(qIssn)),
  )
}

export interface PublisherGroup {
  publisher: string
  label: string
  journals: JournalSummary[]
}

/** 按出版社分组，组与组内都按名字排（localeCompare，不随库里 id 的字典序）。 */
export function groupByPublisher(list: JournalSummary[]): PublisherGroup[] {
  const by = new Map<string, PublisherGroup>()
  for (const j of list) {
    const g = by.get(j.publisher) ?? { publisher: j.publisher, label: j.publisher_label, journals: [] }
    g.journals.push(j)
    by.set(j.publisher, g)
  }
  const cmp = (a: string, b: string) => a.localeCompare(b, currentLocale())
  return [...by.values()]
    .sort((a, b) => cmp(a.label, b.label))
    .map((g) => ({ ...g, journals: [...g.journals].sort((a, b) => cmp(a.label, b.label)) }))
}

/* --------------------------------- 选期刊 --------------------------------- */

const inRange = (w: unknown): w is number =>
  typeof w === 'number' && Number.isFinite(w) && w >= PAGE_MM_MIN && w <= PAGE_MM_MAX

/**
 * 页宽跟到哪一栏：该期刊自己的栏位里**离当前页宽最近**的那一档（同距取窄的）。
 * 一档都没有（期刊没规定栏宽）= null，页宽不动。判据只有「栏位」这一种形状（`derivePageColumns`）。
 */
export function nearestColumnWidth(columns: PageColumnSpec[], currentW: number): number | null {
  let best: number | null = null
  for (const c of columns) {
    if (!inRange(c.width_mm)) continue
    if (
      best === null ||
      Math.abs(c.width_mm - currentW) < Math.abs(best - currentW) ||
      (Math.abs(c.width_mm - currentW) === Math.abs(best - currentW) && c.width_mm < best)
    ) {
      best = c.width_mm
    }
  }
  return best
}

export interface JournalPick {
  binding: SpecBinding
  /** 画布页宽该改成多少（mm）；null = 期刊没规定栏宽，页宽不动 */
  pageW: number | null
  label: string
}

/** 精简出处里不需要的 `overrides` 去掉（出版社原条目只在期刊改写时有，卡片不展示），体积留在几 KB */
function slimSources(src: Record<string, JournalFieldSource>): Record<string, JournalFieldSource> {
  return Object.fromEntries(Object.entries(src).map(([k, v]) => [k, { ...v, overrides: null }]))
}

/**
 * 选一本期刊 → 要写进文档的绑定（存**覆盖**，不只存 id：库更新不会悄悄改掉已定稿项目的结论）+ 页宽联动。
 * `follow` 是项目级表态，跟着项目走。
 */
export function pickJournal(
  journalId: string,
  catalog: JournalCatalog,
  ctx: { pageW: number; follow?: boolean; prev?: JournalPrev },
): JournalPick {
  const summary = listJournals(catalog).find((j) => j.journal_id === journalId)
  const sources = journalSources(journalId, catalog)
  const retrieved = Object.values(sources)
    .map((s) => s.retrieved)
    .sort()
    .at(-1) ?? ''
  const base = builtinEntryById(JOURNAL_BASE_PROFILE_ID)
  if (!summary || !base) throw new Error(`journal: ${journalId}`)
  const ref: JournalRef = { id: journalId, catalogRev: catalogRev(catalog), retrieved, label: summary.label }
  const binding = bindingFor(base, {
    journal: journalOverride(journalId, catalog) as Record<string, unknown>,
    journalRef: ref,
    journalSources: slimSources(sources),
    journalPrev: ctx.prev,
    follow: ctx.follow,
  })
  const pageW = nearestColumnWidth(derivePageColumns(journalProfile(journalId, catalog)), ctx.pageW)
  return { binding, pageW, label: summary.label }
}

/* -------------------------------- 出处卡文字 ------------------------------- */

/** 单位认路径：`widths_mm.*` 全是 mm，其余看末段（`*_mm` / `*_pt` / 含 `dpi`）。 */
function unitOf(path: string): string {
  if (path.startsWith('widths_mm.')) return 'mm'
  const last = path.split('.').at(-1) ?? ''
  if (/_mm$/.test(last)) return 'mm'
  if (/_pt$/.test(last)) return 'pt'
  if (/dpi/.test(last)) return 'dpi'
  return ''
}

/** 规则路径 → 字段名（界面词表 `journal.field.*`）；词表里没有的用路径末段原样（开集兜底，不抛）。 */
export function fieldLabel(path: string): string {
  const key = `field.${path.replace(/\./g, '_')}`
  const text = jt(key)
  return text === `journal.${key}` || text === key ? (path.split('.').at(-1) ?? path) : text
}

const labelOf = (c: PageColumnSpec) => (currentLocale() === 'en-US' ? c.label.en : c.label.zh)

const fmt = (n: number) => String(Math.round(n * 100) / 100)

const QUOTED_QUALIFIERS = ['typical', 'recommended', 'range']

/**
 * 一个值给人看的样子。`typical` / `recommended` / `range` 显示**原话**（`mark`，没有就整条 `quote`）——
 * 不显示成一个像硬判据的数；`min` / `max` 前面带 ≥ / ≤；栏位 `page_columns` 列出全部「名字 + 宽度」。
 */
export function valueText(path: string, s: JournalFieldSource): string {
  if (s.unset) return jt('unset')
  if (QUOTED_QUALIFIERS.includes(s.qualifier)) return s.mark || s.quote
  const v = s.value
  if (path === 'page_columns' && Array.isArray(v)) {
    return (v as PageColumnSpec[]).map((c) => `${labelOf(c)} ${fmt(c.width_mm)} mm`).join(' / ')
  }
  const unit = unitOf(path)
  const bound = s.qualifier === 'min' ? '≥ ' : s.qualifier === 'max' ? '≤ ' : ''
  if (typeof v === 'number') return `${bound}${fmt(v)}${unit ? ` ${unit}` : ''}`
  if (typeof v === 'boolean') return jt(v ? 'yes' : 'no')
  if (Array.isArray(v)) {
    if (v.length === 0) return jt('noLimit')
    const list = v.map((x) => (typeof x === 'object' ? JSON.stringify(x) : String(x))).join(' / ')
    return unit && v.every((x) => typeof x === 'number') ? `${list} ${unit}` : list
  }
  if (v && typeof v === 'object') return JSON.stringify(v)
  return v == null ? '' : String(v)
}

/** 出处卡的结论（底栏第一行）：「字段 值」，如「位图分辨率 ≥ 300 dpi」。栏位的值自带名字，不再加字段名。 */
export function cardConclusion(path: string, s: JournalFieldSource): string {
  const field = path === 'page_columns' ? '' : fieldLabel(path)
  return [field, valueText(path, s)].filter(Boolean).join(' ')
}

/** Elsevier 一类的建议性字号：「X 说明 7 pt 为正文最小字号，6 pt 为上下标最小字号」。 */
export function adviceSentence(journal: string, entries: Record<string, JournalFieldSource>): string | null {
  const num = (s?: JournalFieldSource) => (typeof s?.value === 'number' ? fmt(s.value) : '')
  const body = entries.min_effective_font_size_pt
  const script = entries.min_script_font_size_pt
  const b = body?.affects === 'advice' ? num(body) : ''
  const sc = script?.affects === 'advice' ? num(script) : ''
  if (b && sc) return jt('card.adviceBoth', { journal, body: b, script: sc })
  if (b) return jt('card.adviceBody', { journal, body: b })
  if (sc) return jt('card.adviceScript', { journal, script: sc })
  return null
}

export interface QuoteSegment {
  text: string
  hit: boolean
}

/** 数字边界：`7` 不命中 `17` / `7.5` */
function numberRange(quote: string, n: unknown): [number, number] | null {
  if (typeof n !== 'number' || !Number.isFinite(n)) return null
  const re = new RegExp(`(?<![\\d.])${String(n).replace('.', '\\.')}(?![\\d]|\\.\\d)`)
  const m = re.exec(quote)
  return m ? [m.index, m.index + m[0].length] : null
}

/**
 * 摘录里要标 `<mark>` 的那一段：数据里给了 `mark`（必为 quote 的子串）就用它，不猜；
 * 没给才按 `value`（再 `value_alt`）在原文里找那个数；栏位一类对象值不猜。找不到 = 整段不标。
 */
export function highlightQuote(quote: string, s: Pick<JournalFieldSource, 'mark' | 'value' | 'value_alt'>): QuoteSegment[] {
  let range: [number, number] | null = null
  if (s.mark) {
    const i = quote.indexOf(s.mark)
    if (i >= 0) range = [i, i + s.mark.length]
  }
  range ??= numberRange(quote, s.value) ?? numberRange(quote, s.value_alt)
  if (!range) return [{ text: quote, hit: false }]
  return [
    { text: quote.slice(0, range[0]), hit: false },
    { text: quote.slice(range[0], range[1]), hit: true },
    { text: quote.slice(range[1]), hit: false },
  ].filter((x) => x.text !== '')
}

/** 来源域名（出处卡第二行）；URL 非法（库里不会有）退回原串。 */
export function sourceHost(url: string): string {
  try {
    return new URL(url).hostname
  } catch {
    return url
  }
}

/* ---------------------------- 检查项 ↔ 期刊规则 ---------------------------- */

/** 预检的检查 id → 它背后的期刊规则路径（`SEVERITY_BUMP` 反查；建议性字号提示单列）。 */
export function pathsForCheck(checkId: string): string[] {
  if (checkId === 'font-size-recommended') return ['min_effective_font_size_pt', 'min_script_font_size_pt']
  return Object.entries(SEVERITY_BUMP)
    .filter(([, check]) => check === checkId)
    .map(([path]) => path)
}

/** 某检查在这本期刊上有出处的那几条（advice 只收 affects=advice 的）。 */
export function sourcesForCheck(
  checkId: string,
  sources: Record<string, JournalFieldSource> | undefined,
): [string, JournalFieldSource][] {
  if (!sources) return []
  const advice = checkId === 'font-size-recommended'
  return pathsForCheck(checkId)
    .filter((p) => p in sources && (advice ? sources[p].affects === 'advice' : true))
    .map((p) => [p, sources[p]])
}

/* --------------------------- 「期刊要求」清单的行 --------------------------- */

/** 期刊「要紧的几项」：没有出处就明说「该期刊未规定」，不补。 */
export const KEY_PATH_GROUPS: string[][] = [
  ['page_columns', 'widths_mm.single', 'widths_mm.double'],
  ['min_effective_font_size_pt', 'min_script_font_size_pt'],
  ['min_raster_dpi'],
  ['line_widths_pt'],
]

export interface RequirementRow {
  path: string
  label: string
  /** null = 期刊没规定（没有出处，不补） */
  source: JournalFieldSource | null
}

const rank = (path: string) => {
  const gi = KEY_PATH_GROUPS.findIndex((g) => g.includes(path))
  if (gi >= 0) return gi * 10 + KEY_PATH_GROUPS[gi].indexOf(path)
  return path.startsWith('info.') ? 1000 : 500
}

/**
 * 清单：要紧的几项先（缺的补一行「未规定」），再是期刊写了的其余项，展示项（info.*）垫底。
 * `severity.*` 是判据严重度的调节，不是期刊的要求，不进清单。
 */
export function requirementRows(sources: Record<string, JournalFieldSource>): RequirementRow[] {
  const rows: RequirementRow[] = Object.keys(sources)
    .filter((path) => !path.startsWith('severity.'))
    .map((path) => ({ path, label: fieldLabel(path), source: sources[path] }))
  for (const group of KEY_PATH_GROUPS) {
    if (group.some((p) => p in sources)) continue
    rows.push({ path: group[0], label: fieldLabel(group[0]), source: null })
  }
  return rows.sort((a, b) => rank(a.path) - rank(b.path) || (a.path < b.path ? -1 : 1))
}
