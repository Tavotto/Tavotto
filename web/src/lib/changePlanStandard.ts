/**
 * 改图助手的「当前标准」：随助手来源的方案请求一起发给后端（`CreateChangePlanBody.standard`），
 * 让模型知道这份排版按哪套规范 / 哪本期刊判、此刻「问题」里有什么。
 *
 * 全部从客户端已有的东西组：规范绑定（`lib/specBinding.resolveDocumentSpec`）+ 问题清单（`validationStore`，
 * 与「问题」页签同一份）。**不新判断任何东西**：规则值取自生效规范、问题文字取自界面同一条消息描述符。
 */
import { formatMessage } from '@/i18n'
import type { ChangePlanStandard } from '@/lib/api'
import { effectiveScope, issuesInScope } from '@/lib/problemList'
import type { PublicationProfile } from '@/lib/profile'
import { profileOutputName } from '@/lib/profileText'
import { resolveDocumentSpec, type SpecBinding, type SpecCatalogEntry } from '@/lib/specBinding'
import type { ValidationIssue } from '@/lib/validation'
import { useDocumentStore } from '@/store/documentStore'
import { toCatalog, useProfileStore } from '@/store/profileStore'
import { useUiStore } from '@/store/uiStore'
import { useValidationStore } from '@/store/validationStore'

export const STANDARD_MAX_NAME = 80
export const STANDARD_MAX_RULES = 30
export const STANDARD_MAX_ISSUES = 20
export const STANDARD_MAX_ISSUE_TEXT = 200

type RuleValue = number | string | string[]

const cut = (s: string, n: number): string => (s.length > n ? `${s.slice(0, n - 1)}…` : s)

const isNum = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v)

/** 生效规范里对出图要紧的那几项，值保持朴素（数字 / 文本 / 文本数组）；规范没规定的键不出现 */
export function standardRules(p: PublicationProfile): Record<string, RuleValue> {
  const out: Record<string, RuleValue> = {}
  const num = (k: string, v: unknown) => {
    if (isNum(v) && v > 0) out[k] = v
  }
  const strs = (k: string, v: unknown) => {
    if (Array.isArray(v)) {
      const xs = v.filter((x): x is string => typeof x === 'string' && x !== '')
      if (xs.length) out[k] = xs
    }
  }
  num('single_column_width_mm', p.widths_mm?.single)
  num('double_column_width_mm', p.widths_mm?.double)
  num('default_font_size_pt', p.default_font_size_pt)
  num('min_font_size_pt', p.min_effective_font_size_pt)
  num('absolute_min_font_size_pt', p.absolute_min_font_size_pt)
  num('max_font_size_pt', p.max_font_size_pt)
  num('min_line_width_pt', p.min_line_width_pt)
  num('min_raster_dpi', p.min_raster_dpi)
  num('export_dpi_default', p.preferred_formats?.export_dpi_default)
  if (p.font_family?.latin) out.latin_font = p.font_family.latin
  strs('latin_fonts_accepted', p.font_family?.latin_accepted)
  if (p.cjk_fallback?.required) out.cjk_font_required = 'true'
  strs('cjk_fonts_preferred', p.cjk_fallback?.preferred)
  strs('vector_formats', p.preferred_formats?.vector)
  strs('raster_formats', p.preferred_formats?.raster)
  if (Array.isArray(p.line_widths_pt) && p.line_widths_pt.length) out.line_widths_pt = p.line_widths_pt.filter(isNum).map(String)
  num('legend_min_font_size_pt', p.legend_policy?.min_font_size_pt)
  if (Array.isArray(p.allowed_aspect_ratios) && p.allowed_aspect_ratios.length) {
    out.allowed_aspect_ratios = p.allowed_aspect_ratios.map((a) => a.id).filter(Boolean)
  }
  if (p.axis_policy?.tick_direction) out.tick_direction = p.axis_policy.tick_direction
  return Object.fromEntries(Object.entries(out).slice(0, STANDARD_MAX_RULES))
}

const SEVERITY_ORDER = { error: 0, warn: 1, info: 2 } as const
type IssueSeverity = keyof typeof SEVERITY_ORDER

/** 问题等级 → 方案请求里的三档；「验不了」不当成问题发 */
function severityOf(s: ValidationIssue['severity']): IssueSeverity | null {
  if (s === 'error') return 'error'
  if (s === 'warn') return 'warn'
  if (s === 'suggestion') return 'info'
  return null
}

/** 问题页签里看到的那几条（文字与界面同一条消息描述符），错误在前、最多 20 条、每条 ≤ 200 字 */
/** Tavotto 自己的状态（渲染没跟上、图内修改还没应用）不是图本身的问题，Agent 改不了它——不交给模型（真机 2026-11-27：被当成脚本待办） */
const APP_STATE_RULES = new Set(['unapplied-override', 'stale-render'])

export function standardIssues(issues: readonly ValidationIssue[]): ChangePlanStandard['issues'] {
  return issues
    .filter((i) => !APP_STATE_RULES.has(i.ruleCode))
    .flatMap((i, idx) => {
      const severity = severityOf(i.severity)
      const text = formatMessage(i.message).replace(/\s+/g, ' ').trim()
      return severity && text ? [{ severity, text: cut(text, STANDARD_MAX_ISSUE_TEXT), idx }] : []
    })
    .sort((a, b) => SEVERITY_ORDER[a.severity] - SEVERITY_ORDER[b.severity] || a.idx - b.idx)
    .slice(0, STANDARD_MAX_ISSUES)
    .map(({ severity, text }) => ({ severity, text }))
}

/** 纯函数：绑定 + 规范目录 + 问题清单 → `standard` */
export function buildStandard(
  binding: SpecBinding | null | undefined,
  catalog: SpecCatalogEntry[],
  issues: readonly ValidationIssue[],
): ChangePlanStandard {
  const resolved = resolveDocumentSpec(binding, catalog)
  const ref = binding?.journalRef
  const entry = resolved.profileId ? catalog.find((e) => e.id === resolved.profileId) : undefined
  const name = ref ? ref.label : profileOutputName(resolved.profile, entry)
  return {
    name: cut(name, STANDARD_MAX_NAME),
    chosen: !!ref,
    ...(ref ? { journal_id: ref.id } : {}),
    rules: standardRules(resolved.profile),
    issues: standardIssues(issues),
  }
}

/** 此刻这块面板的 `standard`：绑定读当前文档，问题按「问题」页签对这张图的范围裁 */
export function currentStandard(panelId: string | null | undefined): ChangePlanStandard {
  const ds = useDocumentStore.getState()
  const all = useValidationStore.getState().issues
  const scope = effectiveScope(useUiStore.getState().problemScope, panelId ?? null)
  const scoped = issuesInScope(all, scope, panelId ?? null, ds.activeCanvasId)
  return buildStandard(ds.doc.profile, toCatalog(useProfileStore.getState().specs), scoped)
}
