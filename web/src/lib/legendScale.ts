import type { ManifestElement } from './api'
import type { Rect4 } from './axesLayout'
import type { PanelObject, PanelOverride } from '@/types/document'

/**
 * 图例**整体缩放**（像拖子图手柄那样拖图例的角）落成哪几条 override。
 *
 * matplotlib 的图例盒尺寸 = 文字字号 × 一组「以字号为单位」的构建期参数：
 * 边距 `borderpad`、行距 `labelspacing`、示意线长 `handlelength`、线与字的间距
 * `handletextpad`、列距 `columnspacing`——乘的都是 `Legend._fontsize`。而引擎的
 * 图例 `fontsize` override 只改每条文字的字号（`legendmodel._set_legend_fontsize`），
 * `_fontsize` 不动：只写字号的话字变大了、边距和示意线原样，框不是等比放大。
 *
 * 所以整体缩放 s 倍 = 文字字号、标题字号 **与** 这五个间距参数各乘 s。全部是已有的、
 * 可写回、可重放的属性（属性页的「图例」卡与「间距」卡上就是它们），引擎不需要
 * 新属性，写回事务的语义原样成立。
 *
 * 不缩放的：`ncol`（不是尺寸）、标记大小（matplotlib 自己改字号时也不缩，
 * 由 `markerscale` 管）、边框线宽（与子图边框线宽保持一致更重要）。
 */
export const LEGEND_SCALE_PROPS = [
  'fontsize',
  'title_fontsize',
  'borderpad',
  'labelspacing',
  'handlelength',
  'handletextpad',
  'columnspacing',
] as const

/** 字号按 0.1 pt 落值，间距按 0.01 个字号落值（与属性页的步进同一量级） */
const ROUND: Record<string, number> = { fontsize: 10, title_fontsize: 10 }
const roundFor = (prop: string, v: number) => {
  const k = ROUND[prop] ?? 100
  return Math.round(v * k) / k
}

export interface LegendScaleBase {
  prop: string
  /** 当前值：标量，或（撤销留下的）逐条字号列表 */
  value: number | number[]
  min: number | null
  max: number | null
}

/** 当前值：优先文档里尚未渲染回来的 override，否则 manifest 上报的值 */
function currentValue(panel: PanelObject, el: ManifestElement, prop: string): unknown {
  const ov = panel.overrides.find((o) => o.gid === el.gid && o.prop === prop)
  if (ov) return ov.value
  return el.editable.find((f) => f.prop === prop)?.value
}

const isNum = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v)

/**
 * 这个图例能参与整体缩放的那几条属性与它们此刻的值。
 * 引擎没上报的属性不碰；没有标题时不写标题字号（写了也看不见，只多一条 override）。
 * 字号拿不到就返回空——没有字号就没有「整体」可言。
 */
export function legendScaleBases(panel: PanelObject, el: ManifestElement): LegendScaleBase[] {
  const out: LegendScaleBase[] = []
  for (const prop of LEGEND_SCALE_PROPS) {
    const field = el.editable.find((f) => f.prop === prop)
    if (!field) continue
    if (prop === 'title_fontsize') {
      const title = currentValue(panel, el, 'title')
      if (typeof title !== 'string' || !title.trim()) continue
    }
    const v = currentValue(panel, el, prop)
    const value = isNum(v) ? v : Array.isArray(v) && v.length && v.every(isNum) ? (v as number[]) : null
    if (value == null) continue
    out.push({
      prop,
      value,
      min: isNum(field.min) ? field.min : null,
      max: isNum(field.max) ? field.max : null,
    })
  }
  return out.some((b) => b.prop === 'fontsize') ? out : []
}

/** 倍数夹进每条属性的取值范围（属性页的 min / max）与一个理智区间 */
export function clampLegendScale(bases: readonly LegendScaleBase[], s: number): number {
  let lo = 0.25
  let hi = 4
  for (const b of bases) {
    for (const v of Array.isArray(b.value) ? b.value : [b.value]) {
      if (v <= 0) continue
      if (b.min != null) lo = Math.max(lo, b.min / v)
      if (b.max != null) hi = Math.min(hi, b.max / v)
    }
  }
  if (!Number.isFinite(s)) return 1
  return hi < lo ? 1 : Math.min(hi, Math.max(lo, s))
}

export function legendScalePatches(
  gid: string,
  bases: readonly LegendScaleBase[],
  s: number,
): PanelOverride[] {
  return bases.map((b) => ({
    gid,
    prop: b.prop,
    value: Array.isArray(b.value)
      ? b.value.map((v) => roundFor(b.prop, v * s))
      : roundFor(b.prop, b.value * s),
  }))
}

export type LegendCorner = 'nw' | 'ne' | 'sw' | 'se'

/** 拖 `corner` 时不动的那个角（对角）在框上的位置（figure 分数、top-origin） */
export function fixedCornerOf(box: Rect4, corner: LegendCorner): [number, number] {
  const [x, y, w, h] = box
  return [corner.includes('e') ? x : x + w, corner.includes('s') ? y : y + h]
}

/**
 * 拖 `corner` 这个角、对角不动时，框缩放后的样子（figure 分数、top-origin）。
 * 倍数取光标位移在**对角线方向上的投影**（内容像素空间里算，分数坐标的 x / y
 * 单位不一样长）：斜着拖、横着拖、竖着拖都顺手，不会一个方向灵一个方向不灵。
 */
export function scaledLegendBox(
  box: Rect4,
  corner: LegendCorner,
  dfx: number,
  dfy: number,
  layout: { width: number; height: number },
): { s: number; fixed: [number, number] } {
  const [, , w, h] = box
  const sx = corner.includes('e') ? 1 : -1
  const sy = corner.includes('s') ? 1 : -1
  const W = w * layout.width
  const H = h * layout.height
  const nw = W + sx * dfx * layout.width
  const nh = H + sy * dfy * layout.height
  const d2 = W * W + H * H
  const s = d2 > 0 ? (nw * W + nh * H) / d2 : 1
  return { s, fixed: fixedCornerOf(box, corner) }
}

/** 绕不动点缩放 s 倍之后的框 */
export function boxAfterScale(box: Rect4, fixed: [number, number], s: number): Rect4 {
  const [x, y, w, h] = box
  return [fixed[0] + (x - fixed[0]) * s, fixed[1] + (y - fixed[1]) * s, w * s, h * s]
}
