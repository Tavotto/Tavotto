/**
 * 内置期刊标准库（TypeScript 侧）—— 期刊 = 覆盖 + 出处（ADR 0154）。
 *
 * 数据来自 `@journals` 别名指向的 `src/tavotto/profiles/journals.json`，与 Python 的
 * `engine/journals.py` 读的是**同一个文件**；加载 / 校验 / 折叠是它的同名同义实现
 * （严格同源对），靠 `tests/golden/journal_vectors.json` 对齐，pytest 与 vitest 各跑一遍。
 * 失败码闭集 `JOURNAL_ERROR_CODES` 与 Python 的 `ERROR_CODES` 逐字相同。
 *
 * 期刊只记它自己写了的项：规则键是 `journal-base-v1` 里的字段路径，折成 `JournalOverride`
 * 后走 `mergeJournalInto`——求值器一行不改。期刊没写的项不补（界面标「该期刊未规定」）。
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
/** 规则键里不许覆盖的身份 / 展示字段 */
const NOT_RULES = ['profile_id', 'version', 'label', 'source', 'listed']

export const JOURNAL_ERROR_CODES = [
  'journal_catalog_schema',
  'journal_source_invalid',
  'journal_publisher_unknown',
  'journal_alias_conflict',
  'journal_rule_value_missing',
  'journal_rule_src_missing',
  'journal_rule_src_unknown',
  'journal_rule_quote_empty',
  'journal_rule_path_unknown',
  'journal_rule_value_type',
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

export interface JournalSource {
  url: string
  kind: 'html' | 'pdf'
  retrieved: string
  archive_url?: string
  page?: number
}

export interface JournalRule {
  value: unknown
  src: string
  quote: string
  anchor?: string
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
  quote: string
  src: string
  scope: 'publisher' | 'journal'
  url: string
  link: string
  anchor: string
  kind: 'html' | 'pdf'
  page: number | null
  retrieved: string
  archive_url: string
}

type Obj = Record<string, unknown>
const isObj = (v: unknown): v is Obj => typeof v === 'object' && v !== null && !Array.isArray(v)
const nonBlank = (v: unknown): v is string => typeof v === 'string' && v.trim() !== ''
const has = (o: object, k: string) => Object.prototype.hasOwnProperty.call(o, k)

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
    if (!nonBlank(raw.url) || raw.url.includes('#')) {
      fail('journal_source_invalid', `${w}: url 必须非空且不带 #`)
    }
    if (!SOURCE_KINDS.includes(raw.kind as string)) {
      fail('journal_source_invalid', `${w}: kind 必须是 ${SOURCE_KINDS.join('|')}`)
    }
    if (!nonBlank(raw.retrieved)) fail('journal_source_invalid', `${w}: 缺 retrieved`)
    if (has(raw, 'archive_url') && !nonBlank(raw.archive_url)) {
      fail('journal_source_invalid', `${w}: archive_url 不能为空串`)
    }
    const page = raw.page
    if (page != null && !(typeof page === 'number' && Number.isInteger(page) && page >= 1)) {
      fail('journal_source_invalid', `${w}: page 必须是正整数`)
    }
  }
  return sources as Record<string, JournalSource>
}

function checkRules(
  rules: unknown,
  visible: Record<string, JournalSource>,
  base: Obj,
  where: string,
): Record<string, JournalRule> {
  if (rules == null) return {}
  if (!isObj(rules)) return fail('journal_catalog_schema', `${where}: rules 必须是对象`)
  for (const [path, raw] of Object.entries(rules)) {
    const w = `${where}.rules[${path}]`
    const parts = checkPath(path, base, w)
    if (!isObj(raw)) return fail('journal_catalog_schema', `${w}: 规则必须是对象`)
    if (raw.value === null || raw.value === undefined) {
      fail('journal_rule_value_missing', `${w}: 缺 value`)
    }
    if (!nonBlank(raw.src)) fail('journal_rule_src_missing', `${w}: 缺 src`)
    if (!has(visible, raw.src as string)) {
      fail('journal_rule_src_unknown', `${w}: src=${String(raw.src)} 不在 sources 里`)
    }
    if (!nonBlank(raw.quote)) fail('journal_rule_quote_empty', `${w}: quote 不能为空`)
    if (has(raw, 'anchor') && !nonBlank(raw.anchor)) {
      fail('journal_catalog_schema', `${w}: anchor 不能为空串`)
    }
    let leaf: unknown = base
    for (const p of parts) leaf = (leaf as Obj)[p]
    const want = kindOf(leaf)
    if (want !== 'null' && kindOf(raw.value) !== want) {
      fail('journal_rule_value_type', `${w}: value 应为 ${want}，实际 ${kindOf(raw.value)}`)
    }
  }
  return rules as Record<string, JournalRule>
}

interface EffectiveRule {
  rule: JournalRule
  src: JournalSource
  sid: string
  scope: 'publisher' | 'journal'
}

/** 出版社 rules 先、期刊 rules 后覆盖；每条附上它解析到的 source 与来源范围。 */
function effectiveRules(cat: JournalCatalog, journalId: string): Map<string, EffectiveRule> {
  const journal = cat.journals[journalId]
  const pub = cat.publishers[journal.publisher]
  const out = new Map<string, EffectiveRule>()
  const layers: Array<['publisher' | 'journal', PublisherEntry | JournalEntry, Record<string, JournalSource>]> = [
    ['publisher', pub, {}],
    ['journal', journal, pub.sources ?? {}],
  ]
  for (const [scope, owner, fallback] of layers) {
    const own = owner.sources ?? {}
    for (const [path, rule] of Object.entries(owner.rules ?? {})) {
      const sid = rule.src
      const inOwn = has(own, sid)
      out.set(path, {
        rule,
        src: inOwn ? own[sid] : fallback[sid],
        sid,
        scope: inOwn ? scope : 'publisher',
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

function overrideOf(cat: JournalCatalog, journalId: string): JournalOverride {
  const out: Obj = {}
  for (const [path, eff] of effectiveRules(cat, journalId)) fold(path, eff.rule.value, out)
  return out as JournalOverride
}

/** 校验一份期刊库文档；通过原样返回，否则抛带 `code` 的 `JournalCatalogError`（顺序同 Python）。 */
export function validateCatalog(catalog: unknown): JournalCatalog {
  if (!isObj(catalog) || catalog.schema !== SCHEMA) {
    fail('journal_catalog_schema', `期刊库 schema 必须是 ${SCHEMA}`)
  }
  const cat = catalog as unknown as JournalCatalog
  if (!isObj(cat.publishers) || !isObj(cat.journals)) {
    fail('journal_catalog_schema', '期刊库缺 publishers / journals 对象')
  }
  const base = baseProfile() as unknown as Obj
  for (const [pid, pub] of Object.entries(cat.publishers)) {
    const w = `publisher ${pid}`
    if (!isObj(pub) || !nonBlank(pub.label)) fail('journal_catalog_schema', `${w}: 缺 label`)
    const sources = checkSources(pub.sources, w)
    checkRules(pub.rules, sources, base, w)
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
      const key = name.trim().toLowerCase()
      const prev = seen.get(key)
      if (prev !== undefined && prev !== jid) {
        fail('journal_alias_conflict', `${w}: ${name} 与 ${prev} 撞名`)
      }
      seen.set(key, jid)
    }
    if (has(jr, 'issn') && !nonBlank(jr.issn)) fail('journal_catalog_schema', `${w}: issn 不能为空串`)
    const own = checkSources(jr.sources, w)
    const pubSources = cat.publishers[jr.publisher].sources ?? {}
    checkRules(jr.rules, { ...pubSources, ...own }, base, w)
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

/** 某本期刊折成嵌套的 `JournalOverride`；期刊没写的项不出现。 */
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
  if (source.kind === 'pdf' && source.page) return `${source.url}#page=${source.page}`
  return source.url
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
  for (const [path, { rule, src, sid, scope }] of entries) {
    out[path] = {
      value: structuredClone(rule.value),
      quote: rule.quote,
      src: sid,
      scope,
      url: src.url,
      link: sourceLink(src, rule.anchor),
      anchor: rule.anchor ?? '',
      kind: src.kind,
      page: src.page ?? null,
      retrieved: src.retrieved,
      archive_url: src.archive_url ?? '',
    }
  }
  return out
}
