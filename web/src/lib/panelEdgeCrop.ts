import type { ManifestElement } from '@/lib/api'
import type { CropRect } from '@/types/document'

/**
 * 多图排版里拖面板的一条边 = 裁剪（ADR 0161 §4）的**纯算法**：停靠点 / ⌥ 放行 / 贴合内容。
 *
 * 写的是 `panel.crop`（与裁剪工具同一字段：相对**原图幅**的归一化分数 x / y / w / h，y 向下），
 * 不是 mm——所以「内容边缘」`figure.ink_bbox`（同样是相对图幅的分数、y 向下）可以直接比。
 *
 * 一条边用「深度」说话：这条边从图边往里走了多少（分数）。
 *   l: crop.x · r: 1 − (x + w) · t: crop.y · b: 1 − (y + h)
 * 往外最多回到深度 0（未裁剪）；往里依次是 图边(0) → 内容边缘(inkDepth) → 越过内容边缘。
 * 没裁剪过的图拖边（不按 ⌥）是改图幅，不是裁剪：见 `planEdgeGrow`；按住 ⌥ 才走这里的裁剪。
 */

export type CropSide = 'l' | 'r' | 't' | 'b'

/** 一次拖动此刻落在哪个停靠状态上（胶囊的句子只翻译它，不再判断） */
export type EdgeCropState =
  /** 回到图边：这条边未裁剪 */
  | 'full'
  /** 在图边与内容边缘之间（裁掉的是留白），或没有内容边缘信息时的任意裁剪 */
  | 'trim'
  /** 吸在内容边缘上：紧凑 */
  | 'tight'
  /** 顶在内容边缘上，指针还想往里：不按 ⌥ 就停在这里 */
  | 'blocked'
  /** 按住 ⌥ 越过了内容边缘：会裁掉内容 */
  | 'content'

/** 裁剪框最小边长（分数）；与 `startCropDrag` 的 MIN 同值 */
export const MIN_CROP_FRAC = 0.05

/** 深度比较的浮点容差 */
const EPS = 1e-9

export const depthOf = (c: CropRect, side: CropSide): number =>
  side === 'l' ? c.x : side === 'r' ? 1 - (c.x + c.w) : side === 't' ? c.y : 1 - (c.y + c.h)

/** 内容边缘的四个深度（≥ 0）；`ink_bbox` 缺席或不成形 = null（没有「紧凑」停靠点） */
export function inkDepths(
  ink: ManifestElement['ink_bbox'] | null | undefined,
): Record<CropSide, number> | null {
  if (!ink || ink.length !== 4 || !ink.every(Number.isFinite)) return null
  const [x0, y0, x1, y1] = ink
  if (!(x1 > x0) || !(y1 > y0)) return null
  const clamp01 = (v: number) => Math.min(1, Math.max(0, v))
  return { l: clamp01(x0), t: clamp01(y0), r: clamp01(1 - x1), b: clamp01(1 - y1) }
}

export interface EdgePlanInput {
  /** 起手时这条边的深度 */
  depth0: number
  /** 指针此刻要的深度（未钳位，可为负） */
  want: number
  /** 对边此刻的深度（拖这一条时它不动） */
  oppDepth: number
  /** 这条边的内容边缘深度；null = 没有 */
  inkDepth: number | null
  /** 吸附半径（分数）：由屏幕像素换来 */
  tol: number
  /** ⌥ 按住：放行越过内容边缘 */
  alt: boolean
}

export interface EdgePlan {
  depth: number
  state: EdgeCropState
}

/**
 * 停靠点规则（ADR 0161）：
 *  - 往外到「未裁剪」为止，靠近图边（半径内）吸到 0；
 *  - 往里靠近内容边缘（半径内）吸上去；
 *  - 起手时还没裁进内容的边，不按 ⌥ 越不过内容边缘：越过半径外 = 停在那里（状态 `blocked`），
 *    半径内 = 吸在边缘上；深度从不超过内容边缘；
 *  - 图边与内容边缘都在半径内（内容紧贴图边）时，离谁近吸谁；
 *    起手就已经在内容里面的边（老项目 / 别处裁过）不设这道闸，免得一动就弹出去；
 *  - 不管怎样都给对边留 `MIN_CROP_FRAC`。
 */
export function planEdgeDepth(i: EdgePlanInput): EdgePlan {
  const maxD = Math.max(0, 1 - i.oppDepth - MIN_CROP_FRAC)
  const ink = i.inkDepth
  const barrier = ink != null && !i.alt && i.depth0 <= ink + EPS
  // 闸：不按 ⌥ 时任何越过内容边缘的深度都到不了，与吸附半径无关（内容边缘离图边 ≤ tol 时也一样）
  if (barrier && i.want > ink + i.tol) {
    return { depth: Math.min(ink, maxD), state: 'blocked' }
  }
  const raw = Math.min(Math.max(i.want, 0), maxD)
  const d = barrier ? Math.min(raw, ink) : raw
  // 两个停靠点：图边(0) 与内容边缘；各自在半径内才吸，两者都在半径内（内容紧贴图边）时离谁近吸谁
  const toEdge = d <= i.tol ? d : Infinity
  const inkOk = ink != null && ink > 0 && ink <= maxD
  const toInk = inkOk && Math.abs(d - ink) <= i.tol ? Math.abs(d - ink) : Infinity
  if (toEdge === Infinity && toInk === Infinity) {
    return { depth: d, state: ink != null && d > ink + EPS ? 'content' : 'trim' }
  }
  if (toEdge <= toInk) return { depth: 0, state: 'full' }
  return { depth: ink as number, state: 'tight' }
}

/** 排版里拖一条图边 = 改图幅：松手时写什么、拖动中预览框画在哪（ADR 0161 修订 2026-11-06 / 2026-11-09） */
export interface EdgeGrowPlan {
  /** 新图幅（脚本 mm） */
  sizeMm: [number, number]
  /** 面板新的左上角：拖左 / 上边时对边不动，这里先挪好（渲染回来按左上角校正 w / h） */
  pos: { x: number; y: number }
  /** 新图在页面上的框（mm） */
  box: { x: number; y: number; w: number; h: number }
}

/**
 * 没裁剪过的图，边在图边上，指针往外（`outMm` > 0）或往里（`outMm` < 0，页面 mm）→ 这条边对应的那一维图幅
 * 加长 / 缩短，缩放比不变，所以图内文字在页面上的 pt 不变。吸附半径内仍算停在图边上（null），擦到边不会误改图幅；
 * 钳到引擎上 / 下限后没有实际变化时也回 null，调用方照图边处理。往里缩到文字放不下时由 `lib/edgeShrinkFit` 事后提示。
 */
export function planEdgeGrow(i: {
  side: CropSide
  box0: { x: number; y: number; w: number; h: number }
  sizeMm: readonly [number, number]
  /** 引擎给 `size_mm` 的上界；缺席 = 不限 */
  maxMm?: number
  /** 引擎给 `size_mm` 的下界；缺席 = 只保证 > 0 */
  minMm?: number
  outMm: number
  /** 吸附半径（页面 mm） */
  tolMm: number
}): EdgeGrowPlan | null {
  const { side, box0, sizeMm, maxMm, minMm, outMm, tolMm } = i
  if (!(Math.abs(outMm) > tolMm)) return null
  const horizontal = side === 'l' || side === 'r'
  const axis = horizontal ? 0 : 1
  // 按两轴各自的缩放比换算：老项目里被拉伸过的面板宽高比 ≠ 图幅，共用一个比例会让预览框与落定的框差一截
  const k = (horizontal ? box0.w : box0.h) / sizeMm[axis]
  const round2 = (v: number) => Math.round(v * 100) / 100
  // 下限缺席时至少留 1 mm：图幅 0 / 负数引擎会拒
  const floor = Math.max(minMm ?? 0, 1)
  const dim = Math.max(floor, Math.min(maxMm ?? Infinity, round2(sizeMm[axis] + outMm / k)))
  if (Math.abs(dim - sizeMm[axis]) < 0.005) return null
  const grown = (dim - sizeMm[axis]) * k
  const box = {
    x: side === 'l' ? box0.x - grown : box0.x,
    y: side === 't' ? box0.y - grown : box0.y,
    w: box0.w + (horizontal ? grown : 0),
    h: box0.h + (horizontal ? 0 : grown),
  }
  return {
    sizeMm: horizontal ? [dim, sizeMm[1]] : [sizeMm[0], dim],
    pos: { x: box.x, y: box.y },
    box,
  }
}

/** 把一条边设到某个深度，其余三边保持起手时的样子 */
export function withEdgeDepth(c: CropRect, side: CropSide, depth: number): CropRect {
  switch (side) {
    case 'l':
      return { x: depth, y: c.y, w: c.x + c.w - depth, h: c.h }
    case 'r':
      return { x: c.x, y: c.y, w: 1 - depth - c.x, h: c.h }
    case 't':
      return { x: c.x, y: depth, w: c.w, h: c.y + c.h - depth }
    case 'b':
      return { x: c.x, y: c.y, w: c.w, h: 1 - depth - c.y }
  }
}

/** 「贴合内容」：四边一次裁到内容边缘；缺 `ink_bbox`、或内容已占满全图 = null */
export function fitContentCrop(
  ink: ManifestElement['ink_bbox'] | null | undefined,
): CropRect | null {
  const d = inkDepths(ink)
  if (!d) return null
  const crop: CropRect = {
    x: d.l,
    y: d.t,
    w: 1 - d.l - d.r,
    h: 1 - d.t - d.b,
  }
  if (crop.w < MIN_CROP_FRAC || crop.h < MIN_CROP_FRAC) return null
  return crop
}

/** 已经是贴合状态（再点一次没有变化）：按钮置灰用 */
export function isContentFit(
  crop: CropRect | undefined,
  ink: ManifestElement['ink_bbox'] | null | undefined,
): boolean {
  const target = fitContentCrop(ink)
  if (!target) return false
  const c = crop ?? { x: 0, y: 0, w: 1, h: 1 }
  const close = (a: number, b: number) => Math.abs(a - b) < 1e-6
  return close(c.x, target.x) && close(c.y, target.y) && close(c.w, target.w) && close(c.h, target.h)
}
