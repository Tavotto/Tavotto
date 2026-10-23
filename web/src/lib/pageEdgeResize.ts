import { PAGE_MM_MAX } from '@/lib/pageRange'
import type { PageColumn } from '@/lib/pageColumns'

/** 单图编辑里拖图的边能缩到的最小尺寸（mm）：图比页面小得多是常态（小插图），不套页面的 30 mm */
export const FIGURE_EDGE_MIN_MM = 10
/** 画布上拖页面边能缩到的最小尺寸（mm）；比属性栏的 `PAGE_MM_MIN` 严，免得一不小心把页面拖没 */
export const PAGE_EDGE_MIN_MM = 30
/** 宽度离栏宽多近（mm）就吸过去 */
export const PAGE_EDGE_SNAP_MM = 2

export type PageEdge = 'l' | 'r' | 't' | 'b'

export interface PageEdgeResult {
  w: number
  h: number
  /** 页面原点的平移（mm）：拖左 / 上边 = 新增的长度（对象页面坐标 +shift，图在屏幕上不动），其余 0 */
  shiftX: number
  shiftY: number
  /** 吸上的栏宽；没吸 = null */
  snap: PageColumn | null
}

/**
 * 拖一条边得到的新页面尺寸（纯函数，手势每一帧从**手势开始时**的尺寸现算）。
 *
 * `delta` 是指针沿拖动轴的位移（mm，右 / 下为正）：右 / 下边 = 变大，左 / 上边 = 变小（往外拖才变大）。
 * 宽度（只有左右边）离栏宽 ±`PAGE_EDGE_SNAP_MM` 吸过去，`noSnap`（按住 ⌥）关掉；高度没有规范数，不吸。
 */
export function resizePageEdge(opts: {
  edge: PageEdge
  start: { w: number; h: number }
  delta: number
  columns: PageColumn[]
  noSnap?: boolean
  /** 最小尺寸（mm），默认 `PAGE_EDGE_MIN_MM`；比它还小的起点（小图）不给它在第一帧被抬高，调用方传 `min(起点, 默认)` */
  min?: number
}): PageEdgeResult {
  const { edge, start, delta, columns, noSnap, min = PAGE_EDGE_MIN_MM } = opts
  const horizontal = edge === 'l' || edge === 'r'
  const sign = edge === 'r' || edge === 'b' ? 1 : -1
  const base = horizontal ? start.w : start.h
  // 0.1 mm 一格：标注读数与写进文档的数一致，不留 89.00000001
  let next = Math.round(Math.min(PAGE_MM_MAX, Math.max(min, base + sign * delta)) * 10) / 10
  let snap: PageColumn | null = null
  if (horizontal && !noSnap) {
    let best = PAGE_EDGE_SNAP_MM
    for (const c of columns) {
      const d = Math.abs(c.w - next)
      if (d < best) {
        best = d
        snap = c
      }
    }
    if (snap) next = snap.w
  }
  const w = horizontal ? next : start.w
  const h = horizontal ? start.h : next
  return {
    w,
    h,
    shiftX: edge === 'l' ? w - start.w : 0,
    shiftY: edge === 't' ? h - start.h : 0,
    snap,
  }
}

const round4 = (v: number) => Math.round(v * 1e4) / 1e4

export interface FigureEdgeResult {
  x: number
  y: number
  w: number
  h: number
  snap: PageColumn | null
}

/**
 * 单图编辑里拖图的四条边之一得到的新框（纯函数）：数学与拖页面边是同一份（`resizePageEdge`：右 / 下边变大、左 / 上边往外拖变大，
 * 宽度吸栏宽 ±2 mm、⌥ 关、0.1 mm 一格），区别只有锚点——**对侧那条边不动**：拖左 / 上边时 x / y 反向补偿
 * （`x = 起点右边 - 新宽`），其余 x / y 不变。最小尺寸取 `min(起点, FIGURE_EDGE_MIN_MM)`，小图不会在第一帧被抬高。
 */
export function resizeFigureEdge(opts: {
  edge: PageEdge
  start: { x: number; y: number; w: number; h: number }
  delta: number
  columns: PageColumn[]
  noSnap?: boolean
}): FigureEdgeResult {
  const { edge, start } = opts
  const base = edge === 'l' || edge === 'r' ? start.w : start.h
  const r = resizePageEdge({ ...opts, min: Math.min(FIGURE_EDGE_MIN_MM, base) })
  return {
    x: edge === 'l' ? round4(start.x + start.w - r.w) : start.x,
    y: edge === 't' ? round4(start.y + start.h - r.h) : start.y,
    w: r.w,
    h: r.h,
    snap: r.snap,
  }
}
