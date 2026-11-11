/**
 * 「按期刊调整样式」（用户 2026-11-11 拍板：入口在左栏样式面板，四项都调）：把画布上的字号 / 字体 / 线宽
 * 按项目所选期刊的要求算成一组**样式改动**（`StyleEdit`）。
 *
 * - 规范只检查、永远不改图（ADR 0029）：这里只算，不写；写是样式面板上用户点了那颗按钮，走样式绑定那条
 *   唯一的写路径（`styleBinding`），一次提交、可撤销。
 * - 样式按**角色**统一（刻度 / 图例 / 轴标签……），所以每个角色给一个值：样式里设了就以它为准，没设就取图上
 *   这个角色最常见的那个值，再夹进期刊的 [下限, 上限]。已经在范围内的角色不动——不压低更大的、不抬高更小的。
 * - 线宽只抬不压（期刊只给下限）；0 宽 = 有意不画，不算违规。
 * - 字体：当前拉丁字体不在期刊列的字体里时，换成列表里第一个本机画得出的；一个都画不出就不换（不换成画不出的字）。
 *
 * 只读文档绑定里存的精简出处（ADR 0155 §4）；期刊写的「推荐 / 通常 / 区间」也算（这是用户主动要的调整，
 * 不是判据）。期刊明说「本刊不适用」（`unset`）的项不算。
 */
import type { JournalFieldSource } from './journals'
import { figurePrimaryValue } from './figureFontChoice'
import type { StyleProfileData } from './stylePresets'

export interface JournalStyleTargets {
  journal: string
  /** 字号下限（pt）；期刊没写 = null */
  minPt: number | null
  /** 字号上限（pt） */
  maxPt: number | null
  /** 期刊接受的拉丁字体；没写 = null */
  latin: string[] | null
  /** 线宽下限（pt） */
  minLinePt: number | null
}

const num = (s: JournalFieldSource | undefined): number | null =>
  s && !s.unset && typeof s.value === 'number' && Number.isFinite(s.value) && s.value > 0 ? s.value : null

export function journalStyleTargets(
  binding: { journalRef?: { label: string }; journalSources?: Record<string, JournalFieldSource> } | null | undefined,
): JournalStyleTargets | null {
  if (!binding?.journalRef || !binding.journalSources) return null
  const src = binding.journalSources
  const latinSrc = src['font_family.latin_accepted']
  const latin =
    latinSrc && !latinSrc.unset && Array.isArray(latinSrc.value) && latinSrc.value.length
      ? latinSrc.value.filter((x): x is string => typeof x === 'string' && !!x.trim())
      : null
  const t: JournalStyleTargets = {
    journal: binding.journalRef.label,
    minPt: num(src.min_effective_font_size_pt),
    maxPt: num(src.max_font_size_pt),
    latin: latin?.length ? latin : null,
    minLinePt: num(src['info.min_line_width_pt']),
  }
  // 上下限写反了（数据错）：不调字号，宁可不动也不把字夹成一个莫名其妙的值
  if (t.minPt !== null && t.maxPt !== null && t.minPt > t.maxPt) {
    t.minPt = null
    t.maxPt = null
  }
  return t.minPt === null && t.maxPt === null && t.latin === null && t.minLinePt === null ? null : t
}

/** 字号类：角色 → 属性 */
export const FONT_SIZE_SLOTS: readonly [string, string][] = [
  ['text', 'fontsize'],
  ['title', 'fontsize'],
  ['axis_label', 'fontsize'],
  ['ticks', 'fontsize'],
  ['legend', 'fontsize'],
  ['colorbar', 'tick_fontsize'],
]
/** 线宽类（柱形描边不在里面：0 = 不描边是常见写法，抬上去等于给每根柱子加边） */
export const LINE_WIDTH_SLOTS: readonly [string, string][] = [
  ['line', 'linewidth'],
  ['errorbar', 'linewidth'],
  ['axes', 'spine_linewidth'],
  ['ticks', 'width'],
]
/** 带字体的角色 */
export const FONT_FAMILY_ROLES: readonly string[] = ['text', 'title', 'axis_label', 'ticks', 'legend_text']

export type JournalStyleEdit =
  | { kind: 'element'; role: string; prop: string; value: unknown; from: unknown }
  | { kind: 'annotation'; prop: 'sizePt'; value: number; from: number }

export interface JournalStyleInput {
  /** 画布此刻跟随的样式内容（没跟随 = null） */
  style: StyleProfileData | null
  /** 图上看到的值（页面 pt；字体是原值）：`role` → `prop` → 各元素的值 */
  observed: Record<string, Record<string, unknown[]>>
  /** 画布标注（不含子图序号）的字号；样式里设了标注字号时以样式为准 */
  annotationSizes: number[]
  /** 本机画得出的字体（`figureFamilyOptions` 的 options 减 unavailable） */
  renderable: readonly string[]
}

const round2 = (v: number) => Math.round(v * 100) / 100

/** 出现最多的那个数；并列取先出现的 */
function mode(values: number[]): number | null {
  if (!values.length) return null
  const count = new Map<number, number>()
  for (const v of values) count.set(v, (count.get(v) ?? 0) + 1)
  let best = values[0]
  for (const v of values) if ((count.get(v) ?? 0) > (count.get(best) ?? 0)) best = v
  return best
}

const nums = (xs: unknown[] | undefined) =>
  (xs ?? []).filter((x): x is number => typeof x === 'number' && Number.isFinite(x))

/** 一个角色的一项要不要改、改成多少：样式值优先，没有就取图上最常见的；有任一值出界才改 */
function sizeEdit(styleValue: unknown, seen: number[], lo: number | null, hi: number | null): { from: number; to: number } | null {
  const own = typeof styleValue === 'number' && Number.isFinite(styleValue) ? styleValue : null
  const all = own !== null ? [own] : seen
  const cur = own ?? mode(seen)
  if (cur === null) return null
  const out = all.some((v) => (lo !== null && v < lo - 1e-6) || (hi !== null && v > hi + 1e-6))
  if (!out) return null
  let to = cur
  if (lo !== null && to < lo) to = lo
  if (hi !== null && to > hi) to = hi
  return { from: round2(cur), to: round2(to) }
}

const familyName = (v: unknown): string | null => {
  const p = figurePrimaryValue(v, null)
  const first = Array.isArray(p) ? p[0] : p
  return typeof first === 'string' && first.trim() ? first.trim() : null
}

export function journalStyleEdits(input: JournalStyleInput, t: JournalStyleTargets | null): JournalStyleEdit[] {
  if (!t) return []
  const out: JournalStyleEdit[] = []
  const el = input.style?.element ?? {}

  if (t.minPt !== null || t.maxPt !== null) {
    for (const [role, prop] of FONT_SIZE_SLOTS) {
      const e = sizeEdit(el[role]?.[prop], nums(input.observed[role]?.[prop]), t.minPt, t.maxPt)
      if (e && e.to !== e.from) out.push({ kind: 'element', role, prop, value: e.to, from: e.from })
    }
    // 画布标注只抬下限：上限是给图内文字的，子图序号 / 大标题按期刊惯例常比它大
    if (t.minPt !== null) {
      const e = sizeEdit(input.style?.annotation?.sizePt, input.annotationSizes, t.minPt, null)
      if (e && e.to !== e.from) out.push({ kind: 'annotation', prop: 'sizePt', value: e.to, from: e.from })
    }
  }

  if (t.minLinePt !== null) {
    for (const [role, prop] of LINE_WIDTH_SLOTS) {
      const seen = nums(input.observed[role]?.[prop]).filter((v) => v > 0)
      const own = el[role]?.[prop]
      const e = sizeEdit(typeof own === 'number' && own > 0 ? own : undefined, seen, t.minLinePt, null)
      if (e && e.to !== e.from) out.push({ kind: 'element', role, prop, value: e.to, from: e.from })
    }
  }

  if (t.latin) {
    const accepted = new Set(t.latin.map((x) => x.toLowerCase()))
    const renderable = new Map(input.renderable.map((x) => [x.toLowerCase(), x]))
    const pick = t.latin.map((x) => renderable.get(x.toLowerCase())).find((x): x is string => !!x)
    if (pick) {
      for (const role of FONT_FAMILY_ROLES) {
        const own = el[role]?.fontfamily
        const names = own !== undefined ? [familyName(own)] : (input.observed[role]?.fontfamily ?? []).map(familyName)
        const known = names.filter((n): n is string => !!n)
        if (!known.length || known.every((n) => accepted.has(n.toLowerCase()))) continue
        out.push({ kind: 'element', role, prop: 'fontfamily', value: pick, from: known.find((n) => !accepted.has(n.toLowerCase())) })
      }
    }
  }
  return out
}
