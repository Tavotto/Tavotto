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
  widths_mm: { single: number; double: number; tolerance_mm: number; allow_custom: boolean }
  allowed_aspect_ratios: AspectRatio[]
  aspect_tolerance: number
  font_family: { latin: string; latin_accepted: string[]; latin_substitutes_flagged: string[] }
  cjk_fallback: { required: boolean; preferred: string[]; accepted: string[] }
  default_font_size_pt: number
  min_effective_font_size_pt: number
  absolute_min_font_size_pt: number
  max_font_size_pt: number
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
}

interface ProfileDocument {
  schema: number
  default_profile: string
  profiles: Record<string, PublicationProfile>
}

const DOC = doc as unknown as ProfileDocument

/** journal 覆盖里允许深合并的子对象（其余键整体替换）。与 Python 侧同源。 */
const DEEP_KEYS = [
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
  if (Math.abs(widthMm - single) <= tol) return 'single'
  if (Math.abs(widthMm - double) <= tol) return 'double'
  return null
}
