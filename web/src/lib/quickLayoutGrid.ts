/**
 * 一键排版的网格记忆（ADR 0199）上的纯函数：有效性判据、行列分隔线的位置、拖分隔线时的比例算术。
 * 没有 store、没有 DOM——手势在 `canvas/gutterDrag`，落地在 `store/quickLayoutActions`。
 */
import type { CanvasObject, QuickLayoutMemo } from '@/types/document'

/** 记下的框与图此刻的 x / y / w / h 差多少 mm 以内算「没动过」 */
export const MEMO_TOL_MM = 0.05
/** 拖分隔线时，相邻两列（行）各自至少占这两列（行）合计的多少 */
export const MIN_PAIR_SHARE = 0.15

export type Box = [number, number, number, number]
export type GutterAxis = 'col' | 'row'

/** 记忆有效：`cells` 里每张图还在、仍是面板，且位置与大小仍等于 `boxes`。任何一张动过 / 删了 = 无效（静默） */
export function quickLayoutValid(memo: QuickLayoutMemo | undefined, objects: readonly CanvasObject[]): memo is QuickLayoutMemo {
  if (!memo || memo.cols < 1 || memo.rows < 1) return false
  const ids = Object.keys(memo.cells)
  if (ids.length < 2) return false
  if (memo.colRatios.length !== memo.cols || memo.rowRatios.length !== memo.rows) return false
  const byId = new Map(objects.map((o) => [o.id, o] as const))
  return ids.every((id) => {
    const o = byId.get(id)
    const b = memo.boxes[id]
    if (!o || o.type !== 'panel' || !b) return false
    return (
      Math.abs(o.x - b[0]) <= MEMO_TOL_MM &&
      Math.abs(o.y - b[1]) <= MEMO_TOL_MM &&
      Math.abs(o.w - b[2]) <= MEMO_TOL_MM &&
      Math.abs(o.h - b[3]) <= MEMO_TOL_MM
    )
  })
}

/** 第 `i` 列（行）占的区间 [起, 止]（mm）：该列（行）里所有图的并；没有图 = null */
function spans(memo: QuickLayoutMemo, axis: GutterAxis, boxes: Record<string, Box> = memo.boxes): ([number, number] | null)[] {
  const n = axis === 'col' ? memo.cols : memo.rows
  const out: ([number, number] | null)[] = Array.from({ length: n }, () => null)
  for (const [id, [r, c]] of Object.entries(memo.cells)) {
    const b = boxes[id]
    const k = axis === 'col' ? c : r
    if (!b || k < 0 || k >= n) continue
    const [lo, hi] = axis === 'col' ? [b[0], b[0] + b[2]] : [b[1], b[1] + b[3]]
    const cur = out[k]
    out[k] = cur ? [Math.min(cur[0], lo), Math.max(cur[1], hi)] : [lo, hi]
  }
  return out
}

export interface Gutter {
  axis: GutterAxis
  /** 第 `index` 条 = 第 `index` 与 `index + 1` 列（行）之间 */
  index: number
  /** 分隔线位置（mm）：相邻两列（行）之间空隙的中线 */
  pos: number
  /** 分隔线沿另一方向的范围（mm） */
  from: number
  to: number
}

/** 所有可拖的分隔线。相邻两列（行）有空列 / 互相压住（没有空隙）的那一条不给 */
export function gutters(memo: QuickLayoutMemo): Gutter[] {
  const out: Gutter[] = []
  const all = Object.values(memo.boxes)
  if (!all.length) return out
  for (const axis of ['col', 'row'] as const) {
    const sp = spans(memo, axis)
    const [from, to] =
      axis === 'col'
        ? [Math.min(...all.map((b) => b[1])), Math.max(...all.map((b) => b[1] + b[3]))]
        : [Math.min(...all.map((b) => b[0])), Math.max(...all.map((b) => b[0] + b[2]))]
    for (let i = 0; i + 1 < sp.length; i++) {
      const a = sp[i]
      const b = sp[i + 1]
      if (!a || !b || b[0] < a[1]) continue
      out.push({ axis, index: i, pos: (a[1] + b[0]) / 2, from, to })
    }
  }
  return out
}

/** 第 `index` 条分隔线两侧那两列（行）的宽（高），mm；拖动读数用。没有这条分隔线 = null */
export function gutterPair(memo: QuickLayoutMemo, axis: GutterAxis, index: number): [number, number] | null {
  const sp = spans(memo, axis)
  const a = sp[index]
  const b = sp[index + 1]
  return a && b ? [a[1] - a[0], b[1] - b[0]] : null
}

export interface GutterDrag {
  ratios: number[]
  /** 实际生效的位移（mm，夹取之后） */
  delta: number
  /** 预览用：每张图拖完之后的大致外框（只用来画淡色轮廓，真正的版面由后端重算） */
  boxes: Record<string, Box>
}

/**
 * 把第 `index` 条分隔线拖 `deltaMm`：**只动相邻两列（行）**，它俩合计的权重不变（别的列权重原样），
 * 各自至少占两者合计的 `MIN_PAIR_SHARE`。没有这条分隔线（空列、越界）= null。
 */
export function dragGutter(
  memo: QuickLayoutMemo,
  axis: GutterAxis,
  index: number,
  deltaMm: number,
  /** 记忆里每张图的整图框（被裁剪的图与可见框不同）：给了就按整图尺寸换算发给求解器的占比；预览框与位移仍按可见框 */
  fullBoxes?: Record<string, Box>,
): GutterDrag | null {
  const sp = spans(memo, axis)
  const a = sp[index]
  const b = sp[index + 1]
  const ratios = axis === 'col' ? memo.colRatios : memo.rowRatios
  if (!a || !b || index < 0 || index + 1 >= ratios.length) return null
  const wa = a[1] - a[0]
  const wb = b[1] - b[0]
  const total = wa + wb
  if (!(total > 0) || !Number.isFinite(deltaMm)) return null
  const share = Math.min(1 - MIN_PAIR_SHARE, Math.max(MIN_PAIR_SHARE, (wa + deltaMm) / total))
  const delta = share * total - wa
  const pair = ratios[index] + ratios[index + 1]
  // 求解器的占比按整图尺寸：把拖完之后两列（行）的可见尺寸按各自「整图 / 可见」换回整图，再取占比
  let rShare = share
  if (fullBoxes) {
    const fs = spans(memo, axis, fullBoxes)
    const fa = fs[index]
    const fb = fs[index + 1]
    if (fa && fb && wa > 0 && wb > 0) {
      const na = share * total * ((fa[1] - fa[0]) / wa)
      const nb = (1 - share) * total * ((fb[1] - fb[0]) / wb)
      if (na + nb > 0) rShare = na / (na + nb)
    }
  }
  const next = ratios.slice()
  next[index] = pair * rShare
  next[index + 1] = pair * (1 - rShare)
  const boxes: Record<string, Box> = {}
  for (const [id, [r, c]] of Object.entries(memo.cells)) {
    const box = memo.boxes[id]
    if (!box) continue
    const k = axis === 'col' ? c : r
    const nb: Box = [...box]
    const [pos, len] = axis === 'col' ? [0, 2] : [1, 3]
    if (k === index) nb[len] = box[len] + delta
    else if (k === index + 1) {
      nb[pos] = box[pos] + delta
      nb[len] = box[len] - delta
    }
    boxes[id] = nb
  }
  return { ratios: next, delta, boxes }
}

/**
 * 每一行图框的绝对高度（mm），取自 `boxes`（预览框）里该行任意一格的高度。
 * 有哪一行没有格子（空行）= 高度未知，整份返回 null（调用方只发比例）。
 */
export function rowHeightsMm(memo: QuickLayoutMemo, boxes: Record<string, Box>): number[] | null {
  const out: (number | null)[] = Array.from({ length: memo.rows }, () => null)
  for (const [id, [r]] of Object.entries(memo.cells)) {
    const b = boxes[id]
    if (!b || r < 0 || r >= memo.rows || out[r] !== null) continue
    out[r] = b[3]
  }
  return out.every((h): h is number => h !== null && Number.isFinite(h) && h > 0) ? out : null
}
