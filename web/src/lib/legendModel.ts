import type { Manifest, ManifestElement } from '@/lib/api'
import type { PanelObject, PanelOverride } from '@/types/document'

/**
 * 图例条目模型的前端投影（ADR 0034）。引擎那侧是
 * `engine/overrides.LegendEntries`；这里只回答界面要问的几件事：
 * 一个图例有哪些项、显示顺序是什么、每一项此刻跟随源还是自定义、
 * 「恢复跟随」要改哪几条 override。
 *
 * **不在这里判断样式**：示意线长什么样由 manifest 的 `handle_*` 字段说了算，
 * 这里只搬运。
 */

/** 与 `engine/overrides.LEGEND_ENTRY_STYLE_PROPS` 严格同源（顺序也比）。 */
export const LEGEND_ENTRY_STYLE_PROPS = [
  'handle_color',
  'handle_linestyle',
  'handle_linewidth',
  'handle_marker',
  'handle_markersize',
] as const

/** 与 `engine/overrides.LEGEND_BINDINGS` 严格同源（顺序也比）。 */
export const LEGEND_BINDINGS = ['follow_source', 'custom'] as const
export type LegendBinding = (typeof LEGEND_BINDINGS)[number]

/** 图例项的身份（manifest 元素上的 `legend_entry`）。 */
export interface LegendEntryInfo {
  /** 原始序号——`texts_j` 的 j，重排不改它 */
  index: number
  /** 图中源对象的 gid；缺席 = 没有源（脚本用了代理 artist） */
  source_gid?: string
  /** 脚本原样的绑定：脚本自己改过示意线的项是 custom */
  binding_default?: LegendBinding
}

/** 一条图例项在界面上的状态。 */
export interface LegendEntryView {
  element: ManifestElement
  info: LegendEntryInfo
  /** 当前显示的文字（override 优先） */
  text: string
  /** 此刻的绑定；`null` = 没有源 */
  binding: LegendBinding | null
  hidden: boolean
  /** 源对象的元素（有源且它还在 manifest 里时） */
  source: ManifestElement | null
}

const ENTRY_RE = /\.texts_(\d+)$/

/** 这个图例的所有项（按**原始序号**）。图例标题不是项。 */
export function legendEntryElements(manifest: Manifest, legendGid: string): ManifestElement[] {
  const prefix = `${legendGid}.texts_`
  return manifest.elements
    .filter((e) => e.role === 'legend_text' && e.gid.startsWith(prefix) && ENTRY_RE.test(e.gid))
    .sort((a, b) => entryIndexOf(a) - entryIndexOf(b))
}

export function entryIndexOf(el: ManifestElement): number {
  if (el.legend_entry) return el.legend_entry.index
  const m = el.gid.match(ENTRY_RE)
  return m ? Number(m[1]) : -1
}

/** 这个图例项属于哪个图例（gid）；不是图例项回 null。 */
export function legendGidOfEntry(el: ManifestElement): string | null {
  if (el.role !== 'legend_text') return null
  const m = el.gid.match(/^(.*)\.texts_\d+$/)
  return m ? m[1] : null
}

function currentValue(panel: PanelObject, el: ManifestElement, prop: string): unknown {
  const ov = panel.overrides.find((o) => o.gid === el.gid && o.prop === prop)
  if (ov) return ov.value
  return el.editable.find((f) => f.prop === prop)?.value
}

/**
 * 显示顺序（原始序号的排列）：override 优先，其次 manifest 的 `entry_order`，
 * 再其次自然序。序号越界或重复的忽略，缺漏的按原序补尾——与引擎
 * `_set_legend_entry_order` 同一条规整规则。
 */
export function legendDisplayOrder(
  panel: PanelObject,
  legend: ManifestElement,
  count: number,
): number[] {
  const raw = currentValue(panel, legend, 'entry_order')
  const out: number[] = []
  if (Array.isArray(raw)) {
    for (const v of raw) {
      const i = Number(v)
      if (Number.isInteger(i) && i >= 0 && i < count && !out.includes(i)) out.push(i)
    }
  }
  for (let i = 0; i < count; i++) if (!out.includes(i)) out.push(i)
  return out
}

/** 每一项的界面状态，按显示顺序。 */
export function legendEntryViews(
  panel: PanelObject,
  manifest: Manifest,
  legend: ManifestElement,
): LegendEntryView[] {
  const entries = legendEntryElements(manifest, legend.gid)
  const byIndex = new Map(entries.map((e) => [entryIndexOf(e), e]))
  const count = entries.length ? Math.max(...entries.map(entryIndexOf)) + 1 : 0
  const order = legendDisplayOrder(panel, legend, count)
  const views: LegendEntryView[] = []
  for (const j of order) {
    const el = byIndex.get(j)
    if (!el) continue // 空文字的项引擎不登记
    views.push(entryView(panel, manifest, el))
  }
  return views
}

export function entryView(
  panel: PanelObject,
  manifest: Manifest,
  el: ManifestElement,
): LegendEntryView {
  const info: LegendEntryInfo = el.legend_entry ?? { index: entryIndexOf(el) }
  const bindingField = el.editable.find((f) => f.prop === 'binding')
  const binding = bindingField ? (entryBinding(panel, el) ?? null) : null
  const source = info.source_gid
    ? (manifest.elements.find((e) => e.gid === info.source_gid) ?? null)
    : null
  return {
    element: el,
    info,
    text: String(currentValue(panel, el, 'text') ?? ''),
    binding,
    hidden: currentValue(panel, el, 'visible') === false,
    source,
  }
}

/**
 * 此刻的绑定。判据与引擎 `LegendEntries.effective_binding` 同一条：
 * 任何一条 handle_* override 在 → custom；否则看 binding override；
 * 否则脚本原样。manifest 的 `binding` 字段值是引擎按同一规则算出来的——
 * 这里再算一遍是为了在**渲染还没回来的那几百毫秒**里就能答对
 * （用户刚改了颜色，徽标不该等下一帧才变成「自定义」）。
 */
export function entryBinding(panel: PanelObject, el: ManifestElement): LegendBinding | null {
  const info = el.legend_entry
  if (!info?.source_gid) return null
  if (hasStyleOverride(panel, el.gid)) return 'custom'
  const ov = panel.overrides.find((o) => o.gid === el.gid && o.prop === 'binding')
  if (ov && (LEGEND_BINDINGS as readonly unknown[]).includes(ov.value)) {
    return ov.value as LegendBinding
  }
  return info.binding_default ?? 'custom'
}

export function hasStyleOverride(panel: PanelObject, gid: string): boolean {
  return panel.overrides.some(
    (o) => o.gid === gid && (LEGEND_ENTRY_STYLE_PROPS as readonly string[]).includes(o.prop),
  )
}

/**
 * 「恢复跟随图中对象」要对文档做的事：删掉全部 handle_* override；脚本原样
 * 是 custom 的项还要写一条 `binding = follow_source`，脚本原样是跟随的则把
 * binding override 一起删掉（回到「没表态」，而不是留一条等价的显式值）。
 * 纯函数：调用方把它落进**一次** commit。
 */
export function restoreFollowPlan(
  el: ManifestElement,
): { remove: { gid: string; prop: string }[]; set: PanelOverride[] } {
  const info = el.legend_entry
  const remove = LEGEND_ENTRY_STYLE_PROPS.map((prop) => ({ gid: el.gid, prop }))
  if (info?.binding_default === 'custom') {
    return { remove, set: [{ gid: el.gid, prop: 'binding', value: 'follow_source' }] }
  }
  return { remove: [...remove, { gid: el.gid, prop: 'binding' }], set: [] }
}

/** 这个属性是不是图例项示意线的样式（改它会脱开跟随） */
export function isLegendHandleProp(prop: string): boolean {
  return (LEGEND_ENTRY_STYLE_PROPS as readonly string[]).includes(prop)
}
