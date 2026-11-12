import type { Manifest } from '@/lib/api'
import { drawnFigureRect, FIGURE_CLIP_EPS_MM } from '@/lib/preflight'
import type { CropSide } from '@/lib/panelEdgeCrop'

/**
 * 排版里往里拖图边缩小图幅后「文字放不下」的判断与「等比缩小文字」的方案（ADR 0161 修订 2026-11-09）。
 *
 * 往里拖与往外拖同一条规则：只改被拖那一维的 `figure.size_mm`，文字 pt 不变。图幅小到文字撑不下时，
 * matplotlib 会把刻度 / 轴标题 / 图例挤出图幅——这正是预检「元素超出图幅」的判据（`drawnFigureRect`，
 * 与 `engine/preflight.py::element_overflow` 同源），这里只是在新图回来那一刻、按被拖的那一维把它量一遍。
 *
 * 「等比缩小文字」= 页面上的框保持用户拖到的大小，图按**刚好放得下**的图幅出，再整体等比缩进这个框：
 * 只缩必要的那部分，不是按拖动比例一刀切（用户 2026-11-09 选定）。
 */

/** 一维（宽 = 左右两边、高 = 上下两边）上，元素探出图幅的毫米数：两边各取最糟的一个再相加（图自身 mm） */
export function axisOverflowMm(manifest: Manifest | null | undefined, horizontal: boolean): number {
  const size = manifest?.size_mm
  if (!manifest || !size || !(size[0] > 0) || !(size[1] > 0)) return 0
  const [wMm, hMm] = size
  let lo = 0
  let hi = 0
  for (const el of manifest.elements) {
    const r = drawnFigureRect(el)
    if (!r) continue
    const [x, y, w, h] = r
    if (horizontal) {
      lo = Math.max(lo, -x * wMm)
      hi = Math.max(hi, (x + w - 1) * wMm)
    } else {
      lo = Math.max(lo, -y * hMm)
      hi = Math.max(hi, (y + h - 1) * hMm)
    }
  }
  const clip = (v: number) => (v > FIGURE_CLIP_EPS_MM ? v : 0)
  return clip(lo) + clip(hi)
}

export interface TextFitPlan {
  /** 按这个图幅出图（脚本 mm）：被拖那一维 = 刚好放得下的长度，另一维同比放大 */
  sizeMm: [number, number]
  /** 文字缩放倍数（< 1），胶囊 / 提示里说「文字缩到 x%」 */
  scale: number
}

/**
 * 缩小之后要不要提示、点了按什么出图。
 *
 * - 拖之前就探出去的部分不算这次的账（老图本来就有轴标题压边的）：只看这一维**新增**的探出量；
 *   新增量在容差内 = 不提示（null）。
 * - 刚好放得下的长度 ≈ 现在的长度 + 新增探出量（文字的尺寸不随图幅变，挤出去多少就差多少），
 *   且不超过拖之前的长度（那时放得下）。matplotlib 的布局不是严格线性的，估短了的话新图回来会再量一遍、再提示一次。
 * - 另一维按同一倍数放大，页面上的框不变 → 整张图等比缩进框里，文字与线宽一起缩。超出引擎上限就不提供（null）。
 */
export function planTextFit(i: {
  side: CropSide
  /** 拖之前的图幅与那时这一维的探出量 */
  before: readonly [number, number]
  overBefore: number
  /** 缩小后的图幅与此刻这一维的探出量 */
  now: readonly [number, number]
  overNow: number
  maxMm?: number
}): TextFitPlan | null {
  const axis = i.side === 'l' || i.side === 'r' ? 0 : 1
  const extra = i.overNow - i.overBefore
  if (!(extra > FIGURE_CLIP_EPS_MM)) return null
  const cur = i.now[axis]
  const fit = Math.min(Math.max(i.before[axis], cur), cur + extra)
  if (!(fit > cur + 0.01)) return null
  const scale = cur / fit
  const round2 = (v: number) => Math.round(v * 100) / 100
  const sizeMm: [number, number] = [round2(i.now[0] / scale), round2(i.now[1] / scale)]
  if (i.maxMm != null && (sizeMm[0] > i.maxMm || sizeMm[1] > i.maxMm)) return null
  return { sizeMm, scale }
}
