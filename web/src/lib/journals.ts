/**
 * 内置期刊标准库（TypeScript 侧）—— 期刊 = 覆盖 + 出处（ADR 0154）。
 *
 * 数据来自 `@journals` 别名指向的 `src/tavotto/profiles/journals.json`，与 Python 的
 * `engine/journals.py` 读的是**同一个文件**；加载 / 校验 / 折叠是它的同名同义实现
 * （严格同源对），靠 `tests/golden/journal_vectors.json` 对齐，pytest 与 vitest 各跑一遍
 * （向量里连同各闭集常量一起比）。
 *
 * 两类规则共用一层外壳：键是 `journal-base-v1` 字段路径的折成 `JournalOverride` 进预检；
 * 键是 `info.<id>` 的只是展示项（同样强制 src / quote），不折进 override。
 * 期刊没写的项不补（界面标「该期刊未规定」）。
 */
import catalogDoc from '@journals'
import {
  DEEP_KEYS,
  loadProfile,
  mergeJournalInto,
  validateSpecShape,
  type JournalOverride,
  type PublicationProfile,
} from './profile'

/** 期刊覆盖叠在它上面（不是叠在课题组规范上）。同源对：`journals.py` 的 `BASE_PROFILE_ID`。 */
export const JOURNAL_BASE_PROFILE_ID = 'journal-base-v1'
const SCHEMA = 1
const SOURCE_KINDS = ['html', 'pdf']
export const COVERAGES = ['publisher_general', 'journal_specific', 'single_journal_sample'] as const
export const QUALIFIERS = ['exact', 'max', 'min', 'typical', 'range', 'recommended'] as const
export const STAGES = ['submission', 'production', 'any'] as const
/** 求值器按「等于」判的路径只收 exact */
export const EXACT_ONLY_PATHS = [
  'widths_mm.single',
  'widths_mm.double',
  'allowed_aspect_ratios',
  'line_widths_pt',
] as const
/** 展示项闭集（`info.<id>`）。同源对：`journals.py` 的 `INFO_IDS`。 */
export const INFO_IDS = [
  'one_half_mm',
  'max_height_mm',
  'width_range_mm',
  'width_range_single_mm',
  'width_range_double_mm',
  'width_single_mm',
  'width_double_mm',
  'min_width_mm',
  'min_line_width_pt',
  'dpi_by_kind',
  'dpi_photo',
  'dpi_halftone',
  'dpi_combination',
  'color_mode',
  'formats_raw',
  'formats_images_raw',
  'font_names_raw',
  'text_height_mm',
  'panel_label',
] as const
const INFO_PREFIX = 'info.'
/** 规则键 → 它启用的检查项（期刊填了值、底里是 suggestion 就抬到 warn）。同源对：`SEVERITY_BUMP`。 */
export const SEVERITY_BUMP: Record<string, string> = {
  'widths_mm.single': 'page-width',
  'widths_mm.double': 'page-width',
  allowed_aspect_ratios: 'page-aspect',
  min_effective_font_size_pt: 'font-too-small',
  absolute_min_font_size_pt: 'font-below-absolute-floor',
  max_font_size_pt: 'font-too-large',
  min_raster_dpi: 'raster-dpi',
  line_widths_pt: 'line-width-off-preset',
  'font_family.latin_accepted': 'font-family-substituted',
  'cjk_fallback.required': 'cjk-fallback-missing',
  'axis_policy.tick_direction': 'tick-direction',
  'axis_policy.enclosed_spines': 'spines-not-enclosed',
  'axis_policy.label_format': 'axis-label-format',
  'axis_policy.label_format_regex': 'axis-label-format',
  'axis_policy.max_tick_labels': 'tick-label-count',
  'legend_policy.frame': 'legend-frame',
  'legend_policy.min_font_size_pt': 'legend-font-size',
  'legend_policy.max_font_size_pt': 'legend-font-size',
  text_weight_policy: 'text-weight-policy',
}
export const BUMPED_SEVERITY = 'warn'
/** 底里值是 null 的字段类型本来无从比；这几个得是数。同源对：`journals.py` 的 `KIND_OVERRIDES`。 */
export const KIND_OVERRIDES: Record<string, string> = {
  'widths_mm.single': 'number',
  'widths_mm.double': 'number',
  'axis_policy.tick_direction': 'string',
  'axis_policy.label_format': 'string',
  'axis_policy.label_format_regex': 'string',
  'legend_policy.frame': 'boolean',
}
/**
 * 折叠规则：qualifier 为 exact，或等于该路径的方向，才折进 override；其余只留在
 * `journalSources`（`affects: 'info'`）。同源对：`journals.py` 的 `QUALIFIER_DIRECTION`。
 */
export const QUALIFIER_DIRECTION: Record<string, string> = {
  min_effective_font_size_pt: 'min',
  absolute_min_font_size_pt: 'min',
  min_raster_dpi: 'min',
  'legend_policy.min_font_size_pt': 'min',
  max_font_size_pt: 'max',
  'legend_policy.max_font_size_pt': 'max',
}
function folds(path: string, rule: JournalRule): boolean {
  const q = rule.qualifier ?? 'exact'
  return q === 'exact' || q === QUALIFIER_DIRECTION[path]
}
/** 同源对：`profiles.py` 的 `DEFAULT_SEVERITY` 与 `profile.ts` 的同名常量 */
const DEFAULT_SEVERITY = 'warn'
/** 规则键里不许覆盖的身份 / 展示字段 */
const NOT_RULES = ['profile_id', 'version', 'label', 'source', 'listed']

// eslint-disable-next-line no-control-regex
const URL_RE = /^https:\/\/[^\x00-\x20\x7f]+$/ // 无 m 标志：$ 只匹配串尾
// eslint-disable-next-line no-control-regex
const ANCHOR_RE = /^#:~:text=[^\x00-\x20\x7f]+$/
const DATE_RE = /^(\d{4})-(\d{2})-(\d{2})$/

export const JOURNAL_ERROR_CODES = [
  'journal_catalog_schema',
  'journal_source_invalid',
  'journal_source_coverage',
  'journal_publisher_unknown',
  'journal_alias_conflict',
  'journal_rule_value_missing',
  'journal_rule_src_missing',
  'journal_rule_src_unknown',
  'journal_rule_quote_empty',
  'journal_rule_anchor_invalid',
  'journal_rule_path_unknown',
  'journal_rule_value_type',
  'journal_rule_qualifier_invalid',
  'journal_rule_stage_invalid',
  'journal_rule_mark_invalid',
  'journal_rule_unset_invalid',
  'journal_merge_invalid',
  'journal_unknown',
] as const
export type JournalErrorCode = (typeof JOURNAL_ERROR_CODES)[number]

export class JournalCatalogError extends Error {
  readonly code: JournalErrorCode
  constructor(code: JournalErrorCode, message: string) {
    super(message)
    this.name = 'JournalCatalogError'
    this.code = code
  }
}

export type Qualifier = (typeof QUALIFIERS)[number]
export type Stage = (typeof STAGES)[number]
export type Coverage = (typeof COVERAGES)[number]

export interface JournalSource {
  url: string
  kind: 'html' | 'pdf'
  retrieved: string
  coverage?: Coverage
  archive_url?: string
  page?: number
  /** single_journal_sample 的出版社来源：只属于这一本期刊 */
  journal?: string
}

export interface JournalRule {
  value?: unknown
  value_alt?: unknown
  src: string
  quote: string
  anchor?: string
  qualifier?: Qualifier
  stage?: Stage
  mark?: string
  derivation?: string
  unset?: true
}

export interface JournalEntry {
  label: string
  publisher: string
  issn?: string
  aliases?: string[]
  sources?: Record<string, JournalSource>
  rules?: Record<string, JournalRule>
}

export interface PublisherEntry {
  label: string
  sources?: Record<string, JournalSource>
  rules?: Record<string, JournalRule>
}

export interface JournalCatalog {
  schema: number
  catalog_rev: number
  publishers: Record<string, PublisherEntry>
  journals: Record<string, JournalEntry>
}

export interface JournalSummary {
  journal_id: string
  label: string
  publisher: string
  publisher_label: string
  issn: string
  aliases: string[]
}

/** 一条生效字段的出处（出处卡与 proof report 用）。同形：`journals.py` 的 `journal_sources`。 */
export interface JournalFieldSource {
  value: unknown
  value_alt: unknown
  qualifier: Qualifier
  stage: Stage
  mark: string
  derivation: string
  unset: boolean
  quote: string
  src: string
  rule_scope: 'publisher' | 'journal'
  source_scope: 'publisher' | 'journal'
  coverage: Coverage
  affects: 'preflight' | 'info'
  url: string
  link: string
  anchor: string
  kind: 'html' | 'pdf'
  page: number | null
  retrieved: string
  archive_url: string
  /** 期刊改写了出版社的值时，出版社原条目；否则 null */
  overrides: Omit<JournalFieldSource, 'overrides'> | null
}

type Obj = Record<string, unknown>
const isObj = (v: unknown): v is Obj => typeof v === 'object' && v !== null && !Array.isArray(v)
const nonBlank = (v: unknown): v is string => typeof v === 'string' && v.trim() !== ''
const has = (o: object, k: string) => Object.prototype.hasOwnProperty.call(o, k)
const isNum = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v)
const isIntValued = (v: unknown): v is number => isNum(v) && Number.isInteger(v)
const inList = (list: readonly string[], v: unknown) => typeof v === 'string' && list.includes(v)
const isInfo = (path: string) => path.startsWith(INFO_PREFIX)

function kindOf(v: unknown): string {
  if (v === null || v === undefined) return 'null'
  if (typeof v === 'boolean') return 'boolean'
  if (typeof v === 'number') return 'number'
  if (typeof v === 'string') return 'string'
  if (Array.isArray(v)) return 'array'
  return 'object'
}

const fail = (code: JournalErrorCode, msg: string): never => {
  throw new JournalCatalogError(code, msg)
}

/** 撞名比较的归一：NFKC 后小写。同源对：`journals.py` 的 `_alias_key`。 */
export function aliasKey(name: string): string {
  return name.normalize('NFKC').trim().toLowerCase()
}

function isIsoDate(v: unknown): boolean {
  if (typeof v !== 'string') return false
  const m = DATE_RE.exec(v)
  if (!m) return false
  const [y, mo, d] = [Number(m[1]), Number(m[2]), Number(m[3])]
  const dt = new Date(Date.UTC(y, mo - 1, d))
  return dt.getUTCFullYear() === y && dt.getUTCMonth() === mo - 1 && dt.getUTCDate() === d
}

function baseProfile(): PublicationProfile {
  const base = loadProfile(JOURNAL_BASE_PROFILE_ID)
  if (base.profile_id !== JOURNAL_BASE_PROFILE_ID) {
    throw new JournalCatalogError('journal_catalog_schema', `缺 ${JOURNAL_BASE_PROFILE_ID}`)
  }
  return base
}

function checkPath(path: unknown, base: Obj, where: string): string[] {
  if (typeof path !== 'string' || !path) {
    return fail('journal_rule_path_unknown', `${where}: 规则键必须是非空字符串`)
  }
  if (isInfo(path)) {
    if (!inList(INFO_IDS, path.slice(INFO_PREFIX.length))) {
      fail('journal_rule_path_unknown', `${where}: ${path} 不在 info 闭集里`)
    }
    return [path]
  }
  const parts = path.split('.')
  const top = parts[0]
  if (NOT_RULES.includes(top) || !has(base, top)) {
    return fail('journal_rule_path_unknown', `${where}: ${path} 不是可覆盖的字段`)
  }
  if ((DEEP_KEYS as readonly string[]).includes(top)) {
    const sub = base[top]
    if (parts.length !== 2 || !isObj(sub) || !has(sub, parts[1])) {
      return fail('journal_rule_path_unknown', `${where}: ${path} 必须是 ${top}.<底里存在的键>`)
    }
  } else if (parts.length !== 1) {
    return fail('journal_rule_path_unknown', `${where}: ${path} 不是可深合并的子对象`)
  }
  return parts
}

function checkSources(sources: unknown, where: string): Record<string, JournalSource> {
  if (sources == null) return {}
  if (!isObj(sources)) return fail('journal_source_invalid', `${where}: sources 必须是对象`)
  for (const [sid, raw] of Object.entries(sources)) {
    const w = `${where}.sources.${sid}`
    if (!isObj(raw)) return fail('journal_source_invalid', `${w}: 必须是对象`)
    if (typeof raw.url !== 'string' || !URL_RE.test(raw.url) || raw.url.includes('#')) {
      fail('journal_source_invalid', `${w}: url 必须是不带 # 的 https:// 链接`)
    }
    if (!inList(SOURCE_KINDS, raw.kind)) {
      fail('journal_source_invalid', `${w}: kind 必须是 ${SOURCE_KINDS.join('|')}`)
    }
    if (!isIsoDate(raw.retrieved)) {
      fail('journal_source_invalid', `${w}: retrieved 必须是 ISO 日期 YYYY-MM-DD`)
    }
    if (has(raw, 'archive_url') && !(typeof raw.archive_url === 'string' && URL_RE.test(raw.archive_url))) {
      fail('journal_source_invalid', `${w}: archive_url 必须是 https:// 链接`)
    }
    if (has(raw, 'coverage') && !inList(COVERAGES, raw.coverage)) {
      fail('journal_source_invalid', `${w}: coverage 必须是 ${COVERAGES.join('|')}`)
    }
    const page = raw.page
    if (page != null && !(isIntValued(page) && page >= 1)) {
      fail('journal_source_invalid', `${w}: page 必须是正整数`)
    }
    if (
      has(raw, 'journal') &&
      (raw.coverage !== 'single_journal_sample' || !nonBlank(raw.journal))
    ) {
      fail('journal_source_invalid', `${w}: journal 只能写在 single_journal_sample 来源上`)
    }
  }
  return sources as Record<string, JournalSource>
}

function checkRules(
  rules: unknown,
  visible: Record<string, JournalSource>,
  base: Obj,
  where: string,
  publisherScope: boolean,
  publisherRules: Record<string, JournalRule> = {},
  journalId: string | null = null,
): Record<string, JournalRule> {
  if (rules == null) return {}
  if (!isObj(rules)) return fail('journal_catalog_schema', `${where}: rules 必须是对象`)
  for (const [path, raw] of Object.entries(rules)) {
    const w = `${where}.rules[${path}]`
    const parts = checkPath(path, base, w)
    const info = isInfo(path)
    if (!isObj(raw)) return fail('journal_catalog_schema', `${w}: 规则必须是对象`)
    const unset = raw.unset
    if (has(raw, 'unset') && unset !== true) {
      fail('journal_rule_unset_invalid', `${w}: unset 只能是 true`)
    }
    if (unset) {
      if (publisherScope) fail('journal_rule_unset_invalid', `${w}: 出版社规则不能 unset`)
      if ((raw.value !== null && raw.value !== undefined) || has(raw, 'value_alt')) {
        fail('journal_rule_unset_invalid', `${w}: unset 的规则不能带 value`)
      }
      if (!has(publisherRules, path)) {
        fail('journal_rule_unset_invalid', `${w}: 出版社没有 ${path} 这条可 unset`)
      }
    } else if (raw.value === null || raw.value === undefined) {
      fail('journal_rule_value_missing', `${w}: 缺 value`)
    }
    if (!nonBlank(raw.src)) fail('journal_rule_src_missing', `${w}: 缺 src`)
    if (!has(visible, raw.src as string)) {
      fail('journal_rule_src_unknown', `${w}: src=${String(raw.src)} 不在 sources 里`)
    }
    if (!nonBlank(raw.quote)) fail('journal_rule_quote_empty', `${w}: quote 不能为空`)
    if (has(raw, 'anchor') && !(typeof raw.anchor === 'string' && ANCHOR_RE.test(raw.anchor))) {
      fail('journal_rule_anchor_invalid', `${w}: anchor 必须以 #:~:text= 开头`)
    }
    const qualifier = has(raw, 'qualifier') ? raw.qualifier : 'exact'
    if (
      !inList(QUALIFIERS, qualifier) ||
      ((EXACT_ONLY_PATHS as readonly string[]).includes(path) && qualifier !== 'exact')
    ) {
      fail('journal_rule_qualifier_invalid', `${w}: qualifier=${String(qualifier)} 不允许`)
    }
    if (has(raw, 'stage') && !inList(STAGES, raw.stage)) {
      fail('journal_rule_stage_invalid', `${w}: stage 必须是 ${STAGES.join('|')}`)
    }
    if (has(raw, 'mark') && !(nonBlank(raw.mark) && (raw.quote as string).includes(raw.mark))) {
      fail('journal_rule_mark_invalid', `${w}: mark 必须是 quote 里的一段原文`)
    }
    if (has(raw, 'derivation') && !nonBlank(raw.derivation)) {
      fail('journal_rule_mark_invalid', `${w}: derivation 不能为空串`)
    }
    if (publisherScope) {
      const src = visible[raw.src as string]
      if ((src.coverage ?? 'publisher_general') === 'single_journal_sample') {
        fail('journal_source_coverage', `${w}: single_journal_sample 的来源不能挂在出版社 rules 上`)
      }
    }
    const ownerJournal = visible[raw.src as string].journal
    if (ownerJournal !== undefined && ownerJournal !== journalId) {
      fail('journal_source_coverage', `${w}: 来源 ${String(raw.src)} 只属于期刊 ${ownerJournal}`)
    }
    if (info || unset) continue
    let leaf: unknown = base
    for (const p of parts) leaf = (leaf as Obj)[p]
    const want = KIND_OVERRIDES[path] ?? kindOf(leaf)
    for (const key of ['value', 'value_alt']) {
      if (has(raw, key) && want !== 'null' && kindOf(raw[key]) !== want) {
        fail('journal_rule_value_type', `${w}: ${key} 应为 ${want}，实际 ${kindOf(raw[key])}`)
      }
    }
  }
  return rules as Record<string, JournalRule>
}

interface EffectiveRule {
  rule: JournalRule
  src: JournalSource
  sid: string
  ruleScope: 'publisher' | 'journal'
  sourceScope: 'publisher' | 'journal'
  coverage: Coverage
  unset: boolean
  overrides: EffectiveRule | null
}

/** 出版社 rules 先、期刊 rules 后覆盖；每条附上 source、两种归属与被覆盖的出版社原值。 */
function effectiveRules(cat: JournalCatalog, journalId: string): Map<string, EffectiveRule> {
  const journal = cat.journals[journalId]
  const pub = cat.publishers[journal.publisher]
  const out = new Map<string, EffectiveRule>()
  const layers: Array<
    ['publisher' | 'journal', PublisherEntry | JournalEntry, Record<string, JournalSource>, Coverage]
  > = [
    ['publisher', pub, {}, 'publisher_general'],
    ['journal', journal, pub.sources ?? {}, 'journal_specific'],
  ]
  for (const [scope, owner, fallback, defaultCov] of layers) {
    const own = owner.sources ?? {}
    for (const [path, rule] of Object.entries(owner.rules ?? {})) {
      const sid = rule.src
      const inOwn = has(own, sid)
      const src = inOwn ? own[sid] : fallback[sid]
      out.set(path, {
        rule,
        src,
        sid,
        ruleScope: scope,
        sourceScope: inOwn ? scope : 'publisher',
        coverage: src.coverage ?? (inOwn ? defaultCov : 'publisher_general'),
        unset: rule.unset === true,
        overrides: out.get(path) ?? null,
      })
    }
  }
  return out
}

function fold(path: string, value: unknown, into: Obj): void {
  const parts = path.split('.')
  let node = into
  for (const p of parts.slice(0, -1)) {
    if (!isObj(node[p])) node[p] = {}
    node = node[p] as Obj
  }
  node[parts[parts.length - 1]] = structuredClone(value)
}

/** 折成嵌套覆盖，再按 `SEVERITY_BUMP` 把期刊填了值的检查从 suggestion 抬到 warn。 */
function overrideOf(cat: JournalCatalog, journalId: string): JournalOverride {
  const out: Obj = {}
  const folded: string[] = []
  for (const [path, eff] of effectiveRules(cat, journalId)) {
    if (isInfo(path) || eff.unset || !folds(path, eff.rule)) continue
    fold(path, eff.rule.value, out)
    folded.push(path)
  }
  const baseSev = (baseProfile().severity ?? {}) as Record<string, string>
  for (const path of folded) {
    const check = SEVERITY_BUMP[path]
    if (check === undefined) continue
    const sev = (out.severity ?? {}) as Record<string, string>
    if (has(sev, check)) continue
    if ((baseSev[check] ?? DEFAULT_SEVERITY) === 'suggestion') {
      out.severity = { ...sev, [check]: BUMPED_SEVERITY }
    }
  }
  return out as JournalOverride
}

/** 校验一份期刊库文档；通过原样返回，否则抛带 `code` 的 `JournalCatalogError`（顺序同 Python）。 */
export function validateCatalog(catalog: unknown): JournalCatalog {
  if (!isObj(catalog) || catalog.schema !== SCHEMA) {
    fail('journal_catalog_schema', `期刊库 schema 必须是 ${SCHEMA}`)
  }
  const cat = catalog as unknown as JournalCatalog
  if (!isIntValued(cat.catalog_rev) || cat.catalog_rev < 1) {
    fail('journal_catalog_schema', 'catalog_rev 必须是正整数')
  }
  if (!isObj(cat.publishers) || !isObj(cat.journals)) {
    fail('journal_catalog_schema', '期刊库缺 publishers / journals 对象')
  }
  const base = baseProfile() as unknown as Obj
  for (const [pid, pub] of Object.entries(cat.publishers)) {
    const w = `publisher ${pid}`
    if (!isObj(pub) || !nonBlank(pub.label)) fail('journal_catalog_schema', `${w}: 缺 label`)
    const sources = checkSources(pub.sources, w)
    checkRules(pub.rules, sources, base, w, true)
  }
  for (const [pid, pub] of Object.entries(cat.publishers)) {
    for (const [sid, src] of Object.entries(pub.sources ?? {})) {
      if (src.coverage !== 'single_journal_sample') continue
      const owner = src.journal
      const jr = typeof owner === 'string' && has(cat.journals, owner) ? cat.journals[owner] : null
      if (!isObj(jr) || jr.publisher !== pid) {
        fail(
          'journal_source_coverage',
          `publisher ${pid}.sources.${sid}: single_journal_sample 的来源要写明所属期刊 journal`,
        )
      }
    }
  }
  const seen = new Map<string, string>()
  for (const [jid, jr] of Object.entries(cat.journals)) {
    const w = `journal ${jid}`
    if (!isObj(jr) || !nonBlank(jr.label)) fail('journal_catalog_schema', `${w}: 缺 label`)
    if (typeof jr.publisher !== 'string' || !has(cat.publishers, jr.publisher)) {
      fail('journal_publisher_unknown', `${w}: publisher=${String(jr.publisher)} 不存在`)
    }
    const aliases = jr.aliases ?? []
    if (!Array.isArray(aliases) || !aliases.every(nonBlank)) {
      fail('journal_catalog_schema', `${w}: aliases 必须是非空字符串数组`)
    }
    for (const name of [jid, ...aliases]) {
      const key = aliasKey(name)
      const prev = seen.get(key)
      if (prev !== undefined && prev !== jid) {
        fail('journal_alias_conflict', `${w}: ${name} 与 ${prev} 撞名`)
      }
      seen.set(key, jid)
    }
    if (has(jr, 'issn') && !nonBlank(jr.issn)) fail('journal_catalog_schema', `${w}: issn 不能为空串`)
    const own = checkSources(jr.sources, w)
    const pub = cat.publishers[jr.publisher]
    checkRules(jr.rules, { ...(pub.sources ?? {}), ...own }, base, w, false, pub.rules ?? {}, jid)
  }
  for (const jid of Object.keys(cat.journals)) {
    const override = overrideOf(cat, jid)
    if (Object.keys(override).length === 0) continue
    const reason = validateSpecShape(mergeJournalInto(baseProfile(), override))
    if (reason) fail('journal_merge_invalid', `journal ${jid}: 折叠后的规范不合法: ${reason}`)
  }
  return cat
}

/** 取（并校验）期刊库。`catalog` 给定时校验它（测试 / golden 向量），否则用打进 bundle 的那份。 */
export function loadCatalog(catalog?: unknown): JournalCatalog {
  return validateCatalog(catalog === undefined ? catalogDoc : catalog)
}

function requireJournal(cat: JournalCatalog, journalId: string): void {
  if (!has(cat.journals, journalId)) {
    fail('journal_unknown', `期刊库里没有这本期刊: ${journalId}`)
  }
}

/** 库版本号（正整数）；绑定里存它，「有新版可同步」才有得比。 */
export function catalogRev(catalog?: unknown): number {
  return loadCatalog(catalog).catalog_rev
}

/** 全部期刊的摘要（选择器用），按 journal_id 排序（与 Python 同序）。 */
export function listJournals(catalog?: unknown): JournalSummary[] {
  const cat = loadCatalog(catalog)
  return Object.keys(cat.journals)
    .sort((a, b) => (a < b ? -1 : a > b ? 1 : 0))
    .map((jid) => {
      const jr = cat.journals[jid]
      return {
        journal_id: jid,
        label: jr.label,
        publisher: jr.publisher,
        publisher_label: cat.publishers[jr.publisher].label,
        issn: jr.issn ?? '',
        aliases: [...(jr.aliases ?? [])],
      }
    })
}

/** 某本期刊折成嵌套的 `JournalOverride`；期刊没写的项不出现，`info.*` 与被 unset 的项不折进来。 */
export function journalOverride(journalId: string, catalog?: unknown): JournalOverride {
  const cat = loadCatalog(catalog)
  requireJournal(cat, journalId)
  return overrideOf(cat, journalId)
}

/** `journal-base-v1` + 该期刊的覆盖 = 可直接喂 preflight 的 profile。 */
export function journalProfile(journalId: string, catalog?: unknown): PublicationProfile {
  return mergeJournalInto(baseProfile(), journalOverride(journalId, catalog))
}

/** 「打开原网页」的链接：html 用 text fragment 锚，pdf 用 `#page=N`，否则裸 url。同源对：`source_link`。 */
export function sourceLink(source: JournalSource, anchor?: string | null): string {
  if (anchor) return source.url + anchor
  if (source.kind === 'pdf' && source.page) return `${source.url}#page=${Math.trunc(source.page)}`
  return source.url
}

function fieldSource(eff: EffectiveRule, path: string): Omit<JournalFieldSource, 'overrides'> {
  const { rule, src } = eff
  return {
    value: structuredClone(rule.value ?? null),
    value_alt: structuredClone(rule.value_alt ?? null),
    qualifier: rule.qualifier ?? 'exact',
    stage: rule.stage ?? 'any',
    mark: rule.mark ?? '',
    derivation: rule.derivation ?? '',
    unset: eff.unset,
    quote: rule.quote,
    src: eff.sid,
    rule_scope: eff.ruleScope,
    source_scope: eff.sourceScope,
    coverage: eff.coverage,
    affects: isInfo(path) || !(eff.unset || folds(path, rule)) ? 'info' : 'preflight',
    url: src.url,
    link: sourceLink(src, rule.anchor),
    anchor: rule.anchor ?? '',
    kind: src.kind,
    page: src.page == null ? null : Math.trunc(src.page),
    retrieved: src.retrieved,
    archive_url: src.archive_url ?? '',
  }
}

/** 每条生效字段 → 出处，按路径排序。同形：`journals.py` 的 `journal_sources`。 */
export function journalSources(
  journalId: string,
  catalog?: unknown,
): Record<string, JournalFieldSource> {
  const cat = loadCatalog(catalog)
  requireJournal(cat, journalId)
  const out: Record<string, JournalFieldSource> = {}
  const entries = [...effectiveRules(cat, journalId)].sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
  for (const [path, eff] of entries) {
    out[path] = {
      ...fieldSource(eff, path),
      overrides: eff.overrides ? fieldSource(eff.overrides, path) : null,
    }
  }
  return out
}
