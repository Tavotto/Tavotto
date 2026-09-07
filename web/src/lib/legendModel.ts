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

/* -------------------------------------------------------------------------
 * 图例位置：内 / 外两带（ADR 0034 的 2026-09-07 修订）
 *
 * 「图例摆在哪」在引擎里是三条 prop（`loc` 预设、`loc_frac` 拖动、`loc_anchor`
 * 锚点），但在界面上是**一个**控件——内 / 外只是「有没有锚点」两种说法。
 * 这里是那个控件的模型：读出此刻的摆法、把一次点击翻成对文档的一次修改。
 * ---------------------------------------------------------------------- */

/** 引擎侧的锚点 prop 名（`engine/manifest._legend_fields`）。 */
export const LEGEND_ANCHOR_PROP = 'loc_anchor'
/** 位置控件承接掉的两条字段——同一属性不出两套控件。 */
export const LEGEND_POSITION_PROPS = ['loc', LEGEND_ANCHOR_PROP] as const

/**
 * 位置控件**拥有**的全部落位 prop——`legendPlacementPlan` 只碰这三条，
 * 「恢复到脚本」也就必须清这三条。**唯一出处**，重置那一侧不许再抄一份。
 *
 * 与上面那份 `LEGEND_POSITION_PROPS` 答的是两个问题，别合并：
 *
 *   * `LEGEND_POSITION_PROPS`：**通用列表里让出哪些字段**。只能是 editable
 *     字段（`loc_frac` 不是，它是 `drag_prop`，本来就没有行）；
 *   * `LEGEND_PLACEMENT_PROPS`：**这个控件写过哪些 override**。拖动留下的
 *     `loc_frac` 归它——`legendPlacementPlan` 每次都把那条删掉，那么「把这个
 *     控件还原到脚本」也该把它删掉。
 *
 * 合并式的重置只清 `loc` 是本轮评审抓到的缺陷：`loc_anchor` 被这个控件承接
 * 之后在通用列表里没有第二个入口，清不掉它 = 图例回不到图内。
 */
export const LEGEND_PLACEMENT_PROPS = ['loc', LEGEND_ANCHOR_PROP, 'loc_frac'] as const

/** 锚点：父容器（宿主子图 / 整张图）分数坐标里的一个点。 */
export type LegendAnchor = [number, number]

/** 图例此刻摆在哪。`anchor === null` = 没有锚框 = 图例在容器内侧。 */
export interface LegendPlacement {
  loc: string
  anchor: LegendAnchor | null
}

/**
 * 常用外侧位：一个 id 一组 (loc, anchor)。
 *
 * **纯界面预设**，不是同源对：引擎收任意 (loc, anchor) 组合，这张表只是
 * 「大多数人想要的那六个」。加一条不需要动引擎，删一条也不会让谁失去能力
 * ——自定义锚点那两个数字始终在。
 *
 * 数字取自 matplotlib 社区的通行写法（`1.02` = 子图右边缘往外 2%）；
 * 下方那条留 `-0.08` 是给 x 轴标签让路。
 */
export const LEGEND_OUTSIDE_PRESETS: readonly {
  id: string
  loc: string
  anchor: LegendAnchor
}[] = [
  { id: 'rightTop', loc: 'upper left', anchor: [1.02, 1] },
  { id: 'rightCenter', loc: 'center left', anchor: [1.02, 0.5] },
  { id: 'rightBottom', loc: 'lower left', anchor: [1.02, 0] },
  { id: 'topCenter', loc: 'lower center', anchor: [0.5, 1.02] },
  { id: 'bottomCenter', loc: 'upper center', anchor: [0.5, -0.08] },
  { id: 'leftCenter', loc: 'center right', anchor: [-0.02, 0.5] },
]

/** manifest / override 里的值 → 锚点；不是「两个有限数」的一律当成没有锚点。 */
export function toLegendAnchor(value: unknown): LegendAnchor | null {
  if (!Array.isArray(value) || value.length < 2) return null
  const x = Number(value[0])
  const y = Number(value[1])
  return Number.isFinite(x) && Number.isFinite(y) ? [x, y] : null
}

/**
 * 一个图例此刻的摆法（override 优先）。
 *
 * 引擎不发 `loc_anchor` 时（脚本用了这个模型摆不出来的锚框）回 `anchor: null`
 * ——**能不能改**由字段在不在决定，别拿这里的 null 当「没有锚点」用。
 */
export function legendPlacementOf(panel: PanelObject, legend: ManifestElement): LegendPlacement {
  const locField = legend.editable.find((f) => f.prop === 'loc')
  const anchorField = legend.editable.find((f) => f.prop === LEGEND_ANCHOR_PROP)
  return {
    loc: String((locField ? currentValue(panel, legend, 'loc') : '') ?? ''),
    anchor: anchorField ? toLegendAnchor(currentValue(panel, legend, LEGEND_ANCHOR_PROP)) : null,
  }
}

const ANCHOR_EPS = 1e-6

export function sameLegendAnchor(a: LegendAnchor | null, b: LegendAnchor | null): boolean {
  if (a === null || b === null) return a === b
  return Math.abs(a[0] - b[0]) < ANCHOR_EPS && Math.abs(a[1] - b[1]) < ANCHOR_EPS
}

/** 这个摆法正好是哪个外侧预设；都不是（含内侧、自定义锚点）回 null。 */
export function outsidePresetOf(placement: LegendPlacement): string | null {
  if (!placement.anchor) return null
  const hit = LEGEND_OUTSIDE_PRESETS.find(
    (p) => p.loc === placement.loc && sameLegendAnchor(p.anchor, placement.anchor),
  )
  return hit ? hit.id : null
}

/**
 * 把「用户选了这个摆法」翻成对文档的一次修改。
 *
 * 三条规则，每条都有它的现场：
 *
 *   1. **`loc_frac` 一并删掉**。拖过的图例在引擎里是绝对定位，它压过锚点
 *      （模型把这条优先级写死了，见 `apply_legend_pos_model`）——留着它的话
 *      用户点了预设却看不见任何变化。
 *   2. 选外侧 → 写 `loc` + `loc_anchor`，**一次 commit**。分两步的话中间那帧
 *      会渲染出「loc 换了、锚点还是旧的」的图，撤销栈里也多一条。
 *   3. 选内侧 → 写 `loc`；**此刻确实有锚点才写 `loc_anchor: null`**。
 *      `null` 是一个取值（不要锚框），与「没表态」（用脚本原样的锚框）不是
 *      一回事；此刻本来就没有锚点时写它只会平白留下一条没有作用的 override。
 */
export function legendPlacementPlan(
  panel: PanelObject,
  legends: ManifestElement[],
  next: LegendPlacement,
): { remove: { gid: string; prop: string }[]; set: PanelOverride[] } {
  const remove: { gid: string; prop: string }[] = []
  const set: PanelOverride[] = []
  for (const legend of legends) {
    remove.push({ gid: legend.gid, prop: 'loc_frac' })
    set.push({ gid: legend.gid, prop: 'loc', value: next.loc })
    const supported = legend.editable.some((f) => f.prop === LEGEND_ANCHOR_PROP)
    if (!supported) continue
    if (next.anchor) {
      set.push({ gid: legend.gid, prop: LEGEND_ANCHOR_PROP, value: [...next.anchor] })
    } else if (legendPlacementOf(panel, legend).anchor) {
      set.push({ gid: legend.gid, prop: LEGEND_ANCHOR_PROP, value: null })
    }
  }
  return { remove, set }
}

/**
 * 锚点两个数字的取值范围：**manifest 那条字段说了算**，界面不另写一份
 * ——抄第二份，引擎改了范围界面就会漂。字段不在（不支持）时回 undefined。
 */
export function legendAnchorRange(
  legend: ManifestElement,
): { min?: number; max?: number } | undefined {
  const f = legend.editable.find((x) => x.prop === LEGEND_ANCHOR_PROP)
  return f ? { min: f.min, max: f.max } : undefined
}
