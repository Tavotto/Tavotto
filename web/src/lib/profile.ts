/**
 * 出版规范 profile（TypeScript 侧读取）。
 *
 * **规则常量一个字都不在这里写**：整份规范来自 `@profiles` 别名指向的
 * `src/tavotto/profiles/publication.json`——与 Python 的 `engine/profiles.py`
 * 读的是同一个文件（别名配置在 vite.config.ts 与 vitest.config.ts，各一处）。
 * 抄一份到 TS 里的代价是「双栏 150mm 改一处、另一处照旧放行」，用户会拿到两个
 * 互相矛盾的体检结论。
 *
 * 这里只做：类型、取用、journal 覆盖合并、等级查询。检查逻辑在 `preflight.ts`。
 */
import doc from '@profiles'
import { PAGE_MM_MAX, PAGE_MM_MIN } from './pageRange'

export type Severity = 'error' | 'warn' | 'not_verifiable' | 'suggestion'

export const SEVERITIES: Severity[] = ['error', 'warn', 'not_verifiable', 'suggestion']

/** 检查项没在 profile 的 severity 表里登记时的兜底等级（与 Python 侧同值）。
 *  刻意不是 suggestion：新加的检查忘了登记，用户会以为它通过了。 */
export const DEFAULT_SEVERITY: Severity = 'warn'

/**
 * profile 里**没写**字号下限时的兜底（pt）。与默认规范里那个数同值，而且
 * 前端只有这一处 —— 求值器、导出面板、设置页一个字都不许自己写。
 *
 * 严格同源对：`src/tavotto/engine/profiles.py` 的 `FALLBACK_MIN_FONT_SIZE_PT`，
 * 看护 `tests/test_profile_store.py::test_font_floor_fallback_is_one_number_on_both_sides`。
 *
 * **它不是「规范的下限」**：规范的下限在 profile 自己身上。这一条只在那两个键
 * 缺席时兜底（文档里带着的旧快照、外部导入的 spec）——那时宁可按默认规范判，
 * 也不许算出 NaN 然后**静默放行**（`x < NaN` 恒假，那是最坏的那种"通过"）。
 */
export const FALLBACK_MIN_FONT_SIZE_PT = 8.0

/** 字号下限判据的数值容差（pt），含等号：`eff >= floor - tol` 通过。同源对：engine/profiles.py `FONT_FLOOR_TOL_PT`。 */
export const FONT_FLOOR_TOL_PT = 0.005

/** 规则限定词闭集（期刊库与栏位 `page_columns[].qualifier` 共用）。同源对：`profiles.py` 的 `QUALIFIERS`。 */
export const QUALIFIERS = ['exact', 'max', 'min', 'typical', 'range', 'recommended'] as const
/** 建议性提示的检查 id / 角色闭集。同源对：`profiles.py` 的 `ADVISORY_CHECKS` / `ADVISORY_ROLES`。 */
export const ADVISORY_CHECKS = ['font-size-recommended'] as const
export const ADVISORY_ROLES = ['body_min', 'script_min'] as const

/** 期刊自有栏位（有序）。 */
export interface PageColumnSpec {
  id: string
  label: { zh: string; en: string }
  width_mm: number
  qualifier?: (typeof QUALIFIERS)[number]
}

/** 建议性提示（期刊库折叠生成；只产生 suggestion 级提示，不是判据）。 */
export interface Advisory {
  check: (typeof ADVISORY_CHECKS)[number]
  role: (typeof ADVISORY_ROLES)[number]
  value_pt: number
  qualifier?: string
  journal: string
  quote: string
}

export interface AspectRatio {
  id: string
  w: number
  h: number
}

export interface PublicationProfile {
  profile_id: string
  version: string
  label: string
  source?: string
  /** false = 只是别的东西的底（如期刊底 journal-base-v1），不进规范下拉；同源对 profiles.py `list_profiles` */
  listed?: boolean
  /** single / double 为 null = 该规范没规定栏宽（如期刊底 journal-base-v1） */
  widths_mm: {
    single: number | null
    double: number | null
    tolerance_mm: number
    allow_custom: boolean
  }
  /** 期刊自有栏位；缺省 / 空 = 由 widths_mm 派生单栏 / 双栏（见 `derivePageColumns`） */
  page_columns?: PageColumnSpec[]
  allowed_aspect_ratios: AspectRatio[]
  aspect_tolerance: number
  font_family: { latin: string; latin_accepted: string[]; latin_substitutes_flagged: string[] }
  cjk_fallback: { required: boolean; preferred: string[]; accepted: string[] }
  default_font_size_pt: number
  min_effective_font_size_pt: number
  absolute_min_font_size_pt: number
  max_font_size_pt: number
  /** 上下标最小字号：求值器分不出上下标，只进建议性提示，不进判据 */
  min_script_font_size_pt?: number | null
  advisories?: Advisory[]
  min_raster_dpi: number
  preferred_formats: {
    vector: string[]
    raster: string[]
    export_default: string[]
    export_dpi_default: number
  }
  line_widths_pt: number[]
  line_width_tolerance_pt: number
  axis_policy: {
    tick_direction: string | null
    enclosed_spines: boolean
    minor_ticks: string
    label_format: string | null
    label_format_regex: string | null
    frame_linewidth_pt: number[]
    max_tick_labels: number
  }
  legend_policy: { frame: boolean | null; min_font_size_pt: number; max_font_size_pt: number }
  palette_policy: {
    recommended: string
    url: string
    by_semantic: Record<string, string[]>
    discouraged_colormaps: string[]
    auto_recolor: boolean
  }
  text_weight_policy: Record<string, string>
  severity: Record<string, Severity>
  /** journal 覆盖合并后才有：这份 profile 是从哪个 profile 派生的 */
  derived_from?: string
  journal?: JournalOverride
}

/** 期刊自定义覆盖：只覆盖点名的键，其余继承（与 Python 的 _DEEP_KEYS 同源）。 */
export interface JournalOverride {
  widths_mm?: Partial<PublicationProfile['widths_mm']>
  legend_policy?: Partial<PublicationProfile['legend_policy']>
  axis_policy?: Partial<PublicationProfile['axis_policy']>
  font_family?: Partial<PublicationProfile['font_family']>
  cjk_fallback?: Partial<PublicationProfile['cjk_fallback']>
  severity?: Record<string, Severity>
  preferred_formats?: Partial<PublicationProfile['preferred_formats']>
  min_effective_font_size_pt?: number
  absolute_min_font_size_pt?: number
  min_raster_dpi?: number
  allowed_aspect_ratios?: AspectRatio[]
  line_widths_pt?: number[]
  page_columns?: PageColumnSpec[]
  advisories?: Advisory[]
}

interface ProfileDocument {
  schema: number
  default_profile: string
  profiles: Record<string, PublicationProfile>
}

const DOC = doc as unknown as ProfileDocument

/** journal 覆盖里允许深合并的子对象（其余键整体替换）。与 Python 侧同源。 */
export const DEEP_KEYS = [
  'widths_mm',
  'legend_policy',
  'axis_policy',
  'font_family',
  'cjk_fallback',
  'severity',
  'preferred_formats',
] as const

export const DEFAULT_PROFILE_ID = DOC.default_profile

export interface ProfileSummary {
  profile_id: string
  version: string
  label: string
  source: string
}

/** 界面下拉用的摘要，默认 profile 排在最前。 */
export function listProfiles(): ProfileSummary[] {
  return Object.values(DOC.profiles)
    .filter((p) => p.listed !== false)
    .map((p) => ({
      profile_id: p.profile_id,
      version: p.version,
      label: p.label,
      source: p.source ?? '',
    }))
    .sort(
      (a, b) =>
        Number(a.profile_id !== DEFAULT_PROFILE_ID) - Number(b.profile_id !== DEFAULT_PROFILE_ID),
    )
}

export function hasProfile(id: string | null | undefined): boolean {
  return !!id && id in DOC.profiles
}

/**
 * 取一份可直接喂给 preflight 的 profile。
 * 不认识的 id **退回默认 profile**（旧文档里可能存着一个已经删掉的 id——
 * 那时应该照常出检查结果，而不是整个导出对话框崩掉）。
 */
export function loadProfile(
  profileId?: string | null,
  journal?: JournalOverride | null,
): PublicationProfile {
  const base = (profileId && DOC.profiles[profileId]) || DOC.profiles[DEFAULT_PROFILE_ID]
  return mergeJournalInto(base as PublicationProfile, journal)
}

/**
 * journal 覆盖的**唯一合并实现**（前端侧）。`base` 可以是内置规范、用户自建
 * 规范，也可以是文档里那份快照——三者形状相同，合并规则就该只有一份。
 * Python 侧的同一件事在 `engine/profiles.merge_journal()`。
 */
export function mergeJournalInto(
  base: PublicationProfile,
  journal?: JournalOverride | null,
): PublicationProfile {
  const cloned = structuredClone(base) as PublicationProfile
  if (!journal || Object.keys(journal).length === 0) return cloned
  const merged = { ...cloned } as Record<string, unknown>
  for (const [key, value] of Object.entries(journal)) {
    if (value == null) continue
    if ((DEEP_KEYS as readonly string[]).includes(key) && !Array.isArray(value)) {
      merged[key] = {
        ...(cloned[key as keyof PublicationProfile] as object),
        ...(value as object),
      }
    } else {
      merged[key] = structuredClone(value)
    }
  }
  // 覆盖不许换身份：profile_id / version 永远是被覆盖的那一份的
  merged.profile_id = cloned.profile_id
  merged.version = cloned.version
  merged.derived_from = cloned.profile_id
  merged.journal = structuredClone(journal)
  return merged as unknown as PublicationProfile
}

export function severityOf(profile: PublicationProfile, checkId: string): Severity {
  const value = profile.severity?.[checkId]
  return SEVERITIES.includes(value as Severity) ? (value as Severity) : DEFAULT_SEVERITY
}

/** proof report / 界面标题里的身份戳（与 Python 的 profiles.stamp 同形）。 */
export function profileStamp(profile: PublicationProfile) {
  return {
    profile_id: profile.profile_id,
    profile_version: profile.version,
    label: profile.label,
    ...(profile.journal ? { journal: profile.journal, derived_from: profile.derived_from } : {}),
  }
}

/** 页面宽属于单栏 / 双栏 / 都不是。导出对话框直接显示这个判定。 */
export function columnOf(
  profile: PublicationProfile,
  widthMm: number,
): 'single' | 'double' | null {
  const { single, double, tolerance_mm: tol } = profile.widths_mm
  if (single != null && Math.abs(widthMm - single) <= tol) return 'single'
  if (double != null && Math.abs(widthMm - double) <= tol) return 'double'
  return null
}

/**
 * 下游读栏位的**唯一形状**：写了 `page_columns` 就用它，否则由 `widths_mm` 派生
 * 「单栏 / 双栏」两档（没规定的那档不出现）。同源对：`profiles.py` 的 `derive_page_columns`。
 */
export function derivePageColumns(profile: Partial<PublicationProfile>): PageColumnSpec[] {
  const cols = profile.page_columns
  if (Array.isArray(cols) && cols.length) return cols.filter((c) => isPlainObject(c))
  const widths = (profile.widths_mm ?? {}) as Record<string, unknown>
  const out: PageColumnSpec[] = []
  for (const [id, zh, en] of [
    ['single', '单栏', 'Single column'],
    ['double', '双栏', 'Double column'],
  ] as const) {
    const w = widths[id]
    if (isNumber(w)) out.push({ id, label: { zh, en }, width_mm: w, qualifier: 'exact' })
  }
  return out
}

function validatePageColumns(cols: unknown): string | null {
  if (cols == null) return null
  if (!Array.isArray(cols)) return 'page_columns 必须是数组'
  const seen = new Set<string>()
  for (const [i, c] of cols.entries()) {
    const w = `page_columns[${i}]`
    if (!isPlainObject(c)) return `${w} 必须是对象`
    if (typeof c.id !== 'string' || !c.id.trim() || seen.has(c.id)) return `${w}.id 必须是非空且不重复的字符串`
    seen.add(c.id)
    const label = c.label
    if (
      !isPlainObject(label) ||
      !['zh', 'en'].every((k) => typeof label[k] === 'string' && (label[k] as string).trim())
    ) {
      return `${w}.label 必须是 {zh, en} 两个非空字符串`
    }
    if (!isNumber(c.width_mm) || c.width_mm < PAGE_MM_MIN || c.width_mm > PAGE_MM_MAX) {
      return `${w}.width_mm 必须在 ${PAGE_MM_MIN}–${PAGE_MM_MAX} mm 内`
    }
    const q = 'qualifier' in c ? c.qualifier : 'exact'
    if (!(QUALIFIERS as readonly unknown[]).includes(q)) return `${w}.qualifier 不在闭集里`
  }
  return null
}

function validateAdvisories(adv: unknown): string | null {
  if (adv == null) return null
  if (!Array.isArray(adv)) return 'advisories 必须是数组'
  for (const [i, a] of adv.entries()) {
    const w = `advisories[${i}]`
    if (!isPlainObject(a)) return `${w} 必须是对象`
    if (
      !(ADVISORY_CHECKS as readonly unknown[]).includes(a.check) ||
      !(ADVISORY_ROLES as readonly unknown[]).includes(a.role)
    ) {
      return `${w}.check / role 不在闭集里`
    }
    if (!isNumber(a.value_pt) || a.value_pt <= 0) return `${w}.value_pt 必须是正数`
    for (const k of ['journal', 'quote']) {
      if (typeof a[k] !== 'string' || !(a[k] as string).trim()) return `${w}.${k} 必须是非空字符串`
    }
  }
  return null
}

const OBJECT_KEYS = [
  'widths_mm',
  'font_family',
  'cjk_fallback',
  'axis_policy',
  'legend_policy',
  'palette_policy',
  'severity',
  'preferred_formats',
] as const
const NUMBER_KEYS = [
  'default_font_size_pt',
  'min_effective_font_size_pt',
  'absolute_min_font_size_pt',
  'min_raster_dpi',
] as const
const REQUIRED_KEYS = [
  'profile_id',
  'version',
  'widths_mm',
  'allowed_aspect_ratios',
  'font_family',
  'cjk_fallback',
  'default_font_size_pt',
  'min_effective_font_size_pt',
  'absolute_min_font_size_pt',
  'min_raster_dpi',
  'preferred_formats',
  'line_widths_pt',
  'axis_policy',
  'legend_policy',
  'palette_policy',
  'severity',
] as const

const isPlainObject = (v: unknown): v is Record<string, unknown> =>
  typeof v === 'object' && v !== null && !Array.isArray(v)
const isNumber = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v)

/**
 * 一份规范的**形状**是否合法；合法返回 null，否则返回第一条原因。
 *
 * 严格同源对：`src/tavotto/engine/profiles.py` 的 `_validate` / `validate_spec`（判据逐条
 * 对应，不比措辞）。期刊库折叠后的整体校验（`journals.ts`）用它；看护是
 * `tests/golden/journal_vectors.json` 里的 `journal_merge_invalid` 向量，两侧各跑一遍。
 */
export function validateSpecShape(profile: unknown): string | null {
  if (!isPlainObject(profile)) return '规范必须是对象'
  const p = profile
  const missing = REQUIRED_KEYS.filter((k) => !(k in p))
  if (missing.length) return `缺少字段: ${missing.join(', ')}`
  for (const k of OBJECT_KEYS) if (!isPlainObject(p[k])) return `${k} 必须是对象`
  for (const k of NUMBER_KEYS) if (!isNumber(p[k])) return `${k} 必须是数字`
  for (const k of ['allowed_aspect_ratios', 'line_widths_pt'] as const) {
    if (!Array.isArray(p[k])) return `${k} 必须是数组`
  }
  if (!(p.line_widths_pt as unknown[]).every(isNumber)) return 'line_widths_pt 必须全是数字'
  const fmts = p.preferred_formats as Record<string, unknown>
  for (const k of ['vector', 'raster', 'export_default']) {
    const v = fmts[k]
    if (v == null) continue
    if (!Array.isArray(v) || !v.every((x) => typeof x === 'string')) {
      return `preferred_formats.${k} 必须是字符串数组`
    }
  }
  if (fmts.export_dpi_default != null && !isNumber(fmts.export_dpi_default)) {
    return 'preferred_formats.export_dpi_default 必须是数字'
  }
  for (const [k, v] of Object.entries(p.severity as Record<string, unknown>)) {
    if (!SEVERITIES.includes(v as Severity)) return `severity[${k}] 不是合法等级`
  }
  const colErr = validatePageColumns(p.page_columns) ?? validateAdvisories(p.advisories)
  if (colErr) return colErr
  const widths = p.widths_mm as Record<string, unknown>
  for (const k of ['single', 'double']) {
    if (widths[k] == null) continue // null = 没规定；同源对 profiles.py `_validate`
    if (!isNumber(widths[k]) || (widths[k] as number) <= 0) return `widths_mm.${k} 必须是正数或 null`
  }
  return null
}
