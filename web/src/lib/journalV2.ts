/**
 * v2 来源事实进预检（ADR 0155 §14 修订，2026-11-17 用户拍板）。
 *
 * 后端 `POST /api/journal-facts/preflight`（`journalfacts_fold.py`）把一本刊的 v2 事实折成 **v1 同形**的规则与来源，
 * 以及一份「需要你确认」清单。这里只做三件事，**不判任何东西**：
 *   1. `fetchJournalV2`：取（取不到 = null，预检照 v1 判，不报错）；
 *   2. `applyV2`：把 v2 规则按项叠到绑定上（v2 优先，没写的项沿用 v1），折叠与严重级别全走 `journals.ts` 现有那一套；
 *   3. `confirmEntries`：把清单行变成出处卡认得的条目。
 * 叠加是幂等的：同一份 v2 再叠一次得到同一个绑定（打开项目自动更新时靠它判「有没有变」）。
 */
import {
  JOURNAL_BASE_PROFILE_ID,
  SCHEMA,
  SEVERITY_BUMP,
  foldInto,
  journalOverride,
  journalSources,
  listJournals,
  ruleFolds,
  sourceLink,
  validateCatalog,
  type JournalFieldSource,
  type JournalRule,
  type JournalSource,
  type JournalSnapshot,
  type JournalSummary,
} from '@/lib/journals'
import { bindingFor, builtinEntryById, type JournalPrev, type JournalRef, type SpecBinding } from '@/lib/specBinding'
import { currentLocale, t as translate } from '@/i18n'
import type { FigureDocument } from '@/types/document'

type Binding = NonNullable<FigureDocument['profile']>
type Obj = Record<string, unknown>

/** v2 来源 id 的前缀；同源对：`journalfacts_fold.py` 的 `V2_SOURCE_PREFIX` */
export const V2_SOURCE_PREFIX = 'v2:'

export const isV2Source = (s: Pick<JournalFieldSource, 'src'> | undefined): boolean =>
  !!s && typeof s.src === 'string' && s.src.startsWith(V2_SOURCE_PREFIX)

export interface V2ConfirmRow {
  id: string
  subject: string
  relation: 'gte' | 'lte' | 'eq' | 'in'
  /** 数值要求是数；色彩模式 / 文件格式是名单 */
  value: number | string[]
  unit: string | null
  /** 原文条件，逐字（没有 condition_text 时是名称条件的原样拼接） */
  condition: string
  quote: string
  strength?: string
  stage: string
  anchor?: string
  mark?: string
  derivation?: string
  snapshot?: JournalSnapshot
  source: JournalSource
}

export interface V2Patch {
  catalog_rev: number
  rules: Record<string, JournalRule>
  sources: Record<string, JournalSource>
  confirm: V2ConfirmRow[]
}

const isObj = (v: unknown): v is Obj => typeof v === 'object' && v !== null && !Array.isArray(v)

/** 只收通过 v1 校验器的补丁：写成一本临时刊过 `validateCatalog`；不过 = 当没有 v2。 */
function miniCatalog(patch: Pick<V2Patch, 'rules' | 'sources'>) {
  return {
    schema: SCHEMA,
    catalog_rev: 1,
    publishers: { v2: { label: 'v2' } },
    journals: { v2: { label: 'v2', publisher: 'v2', sources: patch.sources, rules: patch.rules } },
  }
}

function checkPatch(raw: unknown): V2Patch | null {
  if (!isObj(raw) || !isObj(raw.rules) || !isObj(raw.sources) || !Array.isArray(raw.confirm)) return null
  if (typeof raw.catalog_rev !== 'number') return null
  const patch = raw as unknown as V2Patch
  try {
    if (Object.keys(patch.rules).length) validateCatalog(miniCatalog(patch))
  } catch {
    return null
  }
  const rows = patch.confirm.filter(
    (r) =>
      isObj(r) &&
      typeof r.id === 'string' &&
      typeof r.subject === 'string' &&
      (r.relation === 'in'
        ? Array.isArray(r.value) && r.value.length > 0 && r.value.every((x) => typeof x === 'string') && r.unit == null
        : ['gte', 'lte', 'eq'].includes(r.relation as string) && typeof r.value === 'number' && typeof r.unit === 'string') &&
      typeof r.condition === 'string' &&
      typeof r.quote === 'string' &&
      isObj(r.source) &&
      typeof r.source.url === 'string' &&
      /^https:\/\//.test(r.source.url) &&
      typeof r.source.retrieved === 'string',
  )
  return { ...patch, confirm: rows }
}

/** 取单刊 v2 的终态：`ready`（补丁可用）/ `quota`（每设备每小时额度用完）/ `offline`（其余一切：离线、被拒、开关关、补丁不合法） */
export interface V2Outcome {
  state: 'ready' | 'quota' | 'offline'
  patch: V2Patch | null
}

/** 取一本刊的 v2 折叠结果，带终态（选 v2-only 刊要据此说话）。永不抛。 */
export async function fetchJournalV2Outcome(journalId: string, signal?: AbortSignal): Promise<V2Outcome> {
  try {
    const response = await fetch('/api/journal-facts/preflight', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ journal_id: journalId, locale: currentLocale() }),
      signal,
    })
    if (response.status === 429) return { state: 'quota', patch: null }
    if (!response.ok) return { state: 'offline', patch: null }
    const patch = checkPatch(await response.json())
    return patch ? { state: 'ready', patch } : { state: 'offline', patch: null }
  } catch {
    return { state: 'offline', patch: null }
  }
}

/** 取一本刊的 v2 折叠结果。任何失败（离线 / 被拒 / 开关关 / 校验不过）都是 null：预检按 v1 或已有快照照常判。 */
export async function fetchJournalV2(journalId: string, signal?: AbortSignal): Promise<V2Patch | null> {
  return (await fetchJournalV2Outcome(journalId, signal)).patch
}

/** 已签名 v2 index 里的一本刊（`POST /api/journal-facts/index`；只有名称 / 出版社 / ISSN / 别名，没有任何事实） */
export interface V2IndexEntry {
  id: string
  label: string
  publisher: string
  publisher_label: string
  issn: string
  eissn: string
  aliases: string[]
}

const str = (v: unknown): v is string => typeof v === 'string'

/**
 * 取 v2 index 的刊列表（选刊列表用：只读公开的已签名 index，不逐刊拉包、不占每设备每小时的额度）。
 * 取不到（离线又没缓存 / 被拒 / 开关关 / 形状不对）= null：列表里就没有 v2-only 的刊，别的照旧，不报错。
 */
export async function fetchJournalV2Index(signal?: AbortSignal): Promise<V2IndexEntry[] | null> {
  try {
    const response = await fetch('/api/journal-facts/index', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ locale: currentLocale() }),
      signal,
    })
    if (!response.ok) return null
    const raw: unknown = await response.json()
    if (!isObj(raw) || !Array.isArray(raw.journals)) return null
    return raw.journals.filter(
      (j): j is V2IndexEntry =>
        isObj(j) &&
        str(j.id) &&
        str(j.label) &&
        str(j.publisher) &&
        str(j.publisher_label) &&
        str(j.issn) &&
        str(j.eissn) &&
        Array.isArray(j.aliases) &&
        j.aliases.every(str),
    )
  } catch {
    return null
  }
}

/** v2 index 里 v1 目录**没有**的刊（按 id），补成选刊列表认得的 `JournalSummary`（没有 issn 时 eissn 顶上）。 */
export function v2OnlySummaries(entries: readonly V2IndexEntry[] | null, v1Catalog: unknown): JournalSummary[] {
  if (!entries?.length) return []
  const have = new Set(listJournals(v1Catalog).map((j) => j.journal_id))
  return entries
    .filter((e) => !have.has(e.id))
    .map((e) => ({
      journal_id: e.id,
      label: e.label,
      publisher: e.publisher,
      publisher_label: e.publisher_label,
      issn: e.issn || e.eissn,
      aliases: [...e.aliases],
    }))
}

/**
 * 选一本只在 v2 里有的刊：中性底 `journal-base-v1` + v2 无条件事实折出的规则（`applyV2`，与 v1 刊同一条路径）。
 * v1 没有这本刊，所以没有任何 v1 项可沿用，也**不补默认**（「未规定不补」）；带条件的事实只在「需要你确认」。
 * 页宽不动（v2 不折栏宽）。绑定存规则与出处全文，离线重开照这份判。
 */
export function pickV2OnlyJournal(
  entry: Pick<V2IndexEntry, 'id' | 'label'>,
  patch: V2Patch,
  ctx: { pageW: number; follow?: boolean; prev?: JournalPrev },
): { binding: SpecBinding; pageW: null; label: string } {
  const base = builtinEntryById(JOURNAL_BASE_PROFILE_ID)
  if (!base) throw new Error(`journal: ${entry.id}`)
  const retrieved =
    [...Object.values(patch.sources), ...patch.confirm.map((r) => r.source)]
      .map((s) => s.retrieved)
      .sort()
      .at(-1) ?? ''
  const ref: JournalRef = { id: entry.id, catalogRev: patch.catalog_rev, retrieved, label: entry.label }
  const bare = bindingFor(base, { journalRef: ref, journalSources: {}, journalPrev: ctx.prev, follow: ctx.follow })
  return { binding: applyV2(bare, patch), pageW: null, label: entry.label }
}

/** 本地端点：已验签的原文截图（只回该刊事实引用过的图） */
export const journalFactSnapshotUrl = (journalId: string, file: string): string =>
  `/api/journal-facts/snapshot?${new URLSearchParams({ journal_id: journalId, file })}`

function sameValue(a: unknown, b: unknown): boolean {
  if (a === b) return true
  if (Array.isArray(a) || Array.isArray(b)) {
    return Array.isArray(a) && Array.isArray(b) && a.length === b.length && a.every((x, i) => sameValue(x, b[i]))
  }
  if (isObj(a) && isObj(b)) {
    const keys = new Set([...Object.keys(a), ...Object.keys(b)])
    for (const key of keys) if (!sameValue(a[key], b[key])) return false
    return true
  }
  return false
}

function deepMerge(into: Obj, from: Obj): void {
  for (const [key, value] of Object.entries(from)) {
    if (isObj(value) && isObj(into[key])) deepMerge(into[key] as Obj, value)
    else into[key] = structuredClone(value)
  }
}

function unsetLeaf(path: string, into: Obj): void {
  const parts = path.split('.')
  let node: Obj = into
  for (const p of parts.slice(0, -1)) {
    if (!isObj(node[p])) return
    node = node[p] as Obj
  }
  delete node[parts[parts.length - 1]]
}

/** 去掉绑定里此前叠上去的 v2 项；那一项原来有 v1 值（记在 `overrides`）就退回 v1。 */
function stripV2(journal: Obj, sources: Record<string, JournalFieldSource>): void {
  for (const [path, entry] of Object.entries(sources)) {
    if (!isV2Source(entry)) continue
    // 这一项当初抬出来的严重级别（不是用户写的）一并撤掉
    const bumped = entry.v2Severity?.check
    if (bumped && isObj(journal.severity)) {
      delete (journal.severity as Obj)[bumped]
      if (!Object.keys(journal.severity as Obj).length) delete journal.severity
    }
    const prev = entry.overrides
    if (prev) {
      sources[path] = { ...prev, overrides: null }
      if (ruleFolds(path, { qualifier: prev.qualifier })) foldInto(path, prev.value, journal)
      else unsetLeaf(path, journal)
    } else {
      delete sources[path]
      unsetLeaf(path, journal)
    }
  }
}

/**
 * 把 v2 规则叠到一份期刊绑定上。`patch` 为 null（取不到）时原样返回——不退回、不清掉已叠过的项，
 * 离线打开的老项目照旧按绑定里存着的判。没按期刊选过（无 `journalRef`）的绑定原样返回。
 */
export function applyV2(binding: Binding, patch: V2Patch | null): Binding {
  if (!patch || !binding.journalRef) return binding
  const journal = structuredClone((binding.journal ?? {}) as Obj)
  const sources = structuredClone(binding.journalSources ?? {}) as Record<string, JournalFieldSource>
  stripV2(journal, sources)
  if (Object.keys(patch.rules).length) {
    const mini = miniCatalog(patch)
    const fragment = journalOverride('v2', mini) as unknown as Obj
    const fresh = journalSources('v2', mini)
    // 绑定里已显式写的严重级别（含降级）优先：v2 只在用户没写过时才抬，与 `journals.overrideOf` 同一口径
    const sev = isObj(fragment.severity) ? (fragment.severity as Obj) : null
    const explicit = isObj(journal.severity) ? (journal.severity as Obj) : {}
    const derived = new Set<string>()
    if (sev) {
      for (const check of Object.keys(sev)) {
        if (Object.prototype.hasOwnProperty.call(explicit, check)) delete sev[check]
        else derived.add(check)
      }
      if (!Object.keys(sev).length) delete fragment.severity
    }
    for (const path of Object.keys(patch.rules)) {
      const prev = sources[path]
      const { overrides: _drop, ...previous } = prev ?? ({} as JournalFieldSource)
      void _drop
      const check = SEVERITY_BUMP[path]
      sources[path] = {
        ...fresh[path],
        overrides: prev ? previous : null,
        ...(check && derived.has(check) ? { v2Severity: { check } } : {}),
      }
    }
    deepMerge(journal, fragment)
  }
  const next: Binding = { ...binding }
  if (Object.keys(journal).length) next.journal = journal
  else delete next.journal
  next.journalSources = sources
  // 逐键相同（不看键序：项目文件经 Python 往返后键序可能变）就原样返回——每次打开都不变的项目不能每次都变脏
  return sameValue(next.journal, binding.journal) && sameValue(next.journalSources, binding.journalSources) ? binding : next
}

/** 「需要你确认」的一行 → 出处卡条目。路径是合成的 `v2.<主语>_<单位>`（词表 `journal.field.v2_*`），不是 v1 规则路径。 */
export function confirmEntries(row: V2ConfirmRow): [string, JournalFieldSource] {
  const qualifier = row.relation === 'gte' ? 'min' : row.relation === 'lte' ? 'max' : 'exact' // 名单（in）与「恰好」都是 exact
  const source: JournalFieldSource = {
    // 文件格式按大写显示（TIFF / EPS）；只改显示用的这一份，补丁里的数据不动
    value: row.subject === 'file_format' && Array.isArray(row.value) ? row.value.map((v) => v.toUpperCase()) : row.value,
    value_alt: null,
    qualifier,
    ...(row.strength ? { strength: row.strength as JournalFieldSource['strength'] } : {}),
    stage: (['submission', 'production', 'any'].includes(row.stage) ? row.stage : 'any') as JournalFieldSource['stage'],
    mark: row.mark ?? '',
    derivation: row.derivation ?? '',
    snapshot: row.snapshot ?? null,
    unset: false,
    quote: row.quote,
    src: `${V2_SOURCE_PREFIX}${row.id}`,
    rule_scope: 'journal',
    source_scope: 'journal',
    coverage: row.source.coverage ?? 'journal_specific',
    affects: 'info',
    url: row.source.url,
    link: sourceLink(row.source, row.anchor),
    anchor: row.anchor ?? '',
    kind: row.source.kind,
    page: row.source.page ?? null,
    retrieved: row.source.retrieved,
    archive_url: '',
    overrides: null,
  }
  return [row.unit ? `v2.${row.subject}_${row.unit}` : `v2.${row.subject}`, source]
}

/** v2 事实的语气里 v1 不显示的两种：只举例（example）/ 约数（typical）。其余沿用 `journalPick.strengthLabel`。 */
export function v2StrengthNote(strength: string | undefined): string | null {
  if (strength === 'example') return translate('journal.strengthExample', { ns: 'dialogs' })
  if (strength === 'typical') return translate('journal.strengthTypical', { ns: 'dialogs' })
  return null
}
