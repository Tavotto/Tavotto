import type { CanvasObject } from '@/types/document'

export interface Rect {
  x: number
  y: number
  w: number
  h: number
}

export const rectOf = (o: CanvasObject): Rect => ({ x: o.x, y: o.y, w: o.w, h: o.h })

export function boundsOf(objs: CanvasObject[]): Rect | null {
  if (!objs.length) return null
  const x = Math.min(...objs.map((o) => o.x))
  const y = Math.min(...objs.map((o) => o.y))
  const x2 = Math.max(...objs.map((o) => o.x + o.w))
  const y2 = Math.max(...objs.map((o) => o.y + o.h))
  return { x, y, w: x2 - x, h: y2 - y }
}

export function rectsIntersect(a: Rect, b: Rect): boolean {
  return a.x < b.x + b.w && a.x + a.w > b.x && a.y < b.y + b.h && a.y + a.h > b.y
}

/* -------------------------------------------------------------------------- */
/*  吸附：候选线 = 页面边/中线 + 其他对象三线 + 用户参考线                       */
/* -------------------------------------------------------------------------- */

export interface SnapCandidates {
  xs: number[]
  ys: number[]
}

/** 吸附目标开关；页面边与中线始终参与，它们是版面本身的骨架 */
export interface SnapTargets {
  objects: boolean
  guides: boolean
  grid: boolean
  gridSize: number
}

export function snapCandidates(
  objs: CanvasObject[],
  exclude: ReadonlySet<string>,
  page: { w: number; h: number },
  guides: { axis: 'x' | 'y'; pos: number }[] = [],
  targets: SnapTargets = { objects: true, guides: true, grid: false, gridSize: 10 },
): SnapCandidates {
  const xs = [0, page.w / 2, page.w]
  const ys = [0, page.h / 2, page.h]
  if (targets.objects) {
    for (const o of objs) {
      if (exclude.has(o.id) || o.hidden) continue
      xs.push(o.x, o.x + o.w / 2, o.x + o.w)
      ys.push(o.y, o.y + o.h / 2, o.y + o.h)
    }
  }
  if (targets.guides) for (const g of guides) (g.axis === 'x' ? xs : ys).push(g.pos)
  if (targets.grid && targets.gridSize > 0) {
    for (let v = 0; v <= page.w + 1e-6; v += targets.gridSize) xs.push(v)
    for (let v = 0; v <= page.h + 1e-6; v += targets.gridSize) ys.push(v)
  }
  return { xs, ys }
}

function nearest(v: number, candidates: number[], tol: number): number | null {
  let best: number | null = null
  let bestD = tol
  for (const c of candidates) {
    const d = Math.abs(v - c)
    if (d < bestD) {
      bestD = d
      best = c
    }
  }
  return best
}

export interface SnapResult {
  dx: number
  dy: number
  guideXs: number[]
  guideYs: number[]
}

/**
 * 对拖动中的矩形做三线吸附（左/中/右 与 上/中/下），返回需要额外施加的位移。
 * 与 v1 一致：每个轴取第一个命中的边，命中即显示参考线。
 */
export function snapMove(rect: Rect, cands: SnapCandidates, tol: number): SnapResult {
  const res: SnapResult = { dx: 0, dy: 0, guideXs: [], guideYs: [] }
  for (const edge of [0, rect.w / 2, rect.w]) {
    const hit = nearest(rect.x + edge, cands.xs, tol)
    if (hit != null) {
      res.dx = hit - edge - rect.x
      res.guideXs.push(hit)
      break
    }
  }
  for (const edge of [0, rect.h / 2, rect.h]) {
    const hit = nearest(rect.y + edge, cands.ys, tol)
    if (hit != null) {
      res.dy = hit - edge - rect.y
      res.guideYs.push(hit)
      break
    }
  }
  return res
}

/** 缩放时只吸附正在移动的那条边 */
export function snapEdge(value: number, cands: number[], tol: number): number | null {
  return nearest(value, cands, tol)
}

/* -------------------------------------------------------------------------- */
/*  对齐 / 分布                                                                */
/* -------------------------------------------------------------------------- */

export type AlignMode =
  | 'left'
  | 'hcenter'
  | 'right'
  | 'top'
  | 'vcenter'
  | 'bottom'
  | 'hdist'
  | 'vdist'
  | 'samew'
  | 'sameh'

export const ALIGN_NEEDS_THREE: AlignMode[] = ['hdist', 'vdist']

/**
 * 就地对齐一组对象（会直接修改传入对象，供 immer draft 使用）。
 * 单选时以页面为基准框，多选时以选区包围盒为基准 —— 与 v1 行为一致。
 */
export function applyAlign(
  objs: CanvasObject[],
  mode: AlignMode,
  page: { w: number; h: number },
  primary?: CanvasObject,
): boolean {
  if (!objs.length) return false
  const box: Rect =
    objs.length === 1 ? { x: 0, y: 0, w: page.w, h: page.h } : boundsOf(objs)!
  const prim = primary ?? objs[objs.length - 1]

  if (mode === 'hdist' || mode === 'vdist') {
    if (objs.length < 3) return false
    const k = mode === 'hdist' ? 'x' : 'y'
    const s = mode === 'hdist' ? 'w' : 'h'
    const sorted = objs.slice().sort((a, b) => a[k] - b[k])
    const total = sorted.reduce((t, o) => t + o[s], 0)
    const gap = (box[s] - total) / (sorted.length - 1)
    let cur = box[k]
    for (const o of sorted) {
      o[k] = cur
      cur += o[s] + gap
    }
    return true
  }

  if (mode === 'samew' || mode === 'sameh') {
    for (const o of objs) {
      if (o === prim) continue
      const k = mode === 'samew' ? prim.w / o.w : prim.h / o.h
      o.w *= k
      // 面板与形状等比缩放，文字高度自适应
      if (o.type === 'panel') o.h *= k
      else if (mode === 'sameh') o.h = prim.h
    }
    return true
  }

  for (const o of objs) {
    if (mode === 'left') o.x = box.x
    else if (mode === 'right') o.x = box.x + box.w - o.w
    else if (mode === 'hcenter') o.x = box.x + (box.w - o.w) / 2
    else if (mode === 'top') o.y = box.y
    else if (mode === 'bottom') o.y = box.y + box.h - o.h
    else if (mode === 'vcenter') o.y = box.y + (box.h - o.h) / 2
  }
  return true
}

/** 阅读顺序：先按行（同一行容差 5mm）再按列，用于 (a)(b)(c) 自动编号 */
export function readingOrder<T extends { x: number; y: number }>(items: T[]): T[] {
  return items.slice().sort((a, b) => (Math.abs(a.y - b.y) > 5 ? a.y - b.y : a.x - b.x))
}

export type ResizeDir = 'nw' | 'ne' | 'sw' | 'se' | 'n' | 's' | 'e' | 'w'

/** 由手柄方向计算新矩形；proportional 时以变化较大的一维为准等比缩放 */
export function resizeRect(
  orig: Rect,
  dir: ResizeDir,
  dxMm: number,
  dyMm: number,
  proportional: boolean,
  minW = 3,
  minH = 2,
): Rect {
  const east = dir.includes('e')
  const west = dir.includes('w')
  const south = dir.includes('s')
  const north = dir.includes('n')

  let w = orig.w + (east ? dxMm : west ? -dxMm : 0)
  let h = orig.h + (south ? dyMm : north ? -dyMm : 0)
  w = Math.max(minW, w)
  h = Math.max(minH, h)

  if (proportional && (east || west) && (north || south)) {
    const k = Math.max(w / orig.w, h / orig.h)
    w = orig.w * k
    h = orig.h * k
  } else if (proportional && (east || west)) {
    h = orig.h * (w / orig.w)
  } else if (proportional && (north || south)) {
    w = orig.w * (h / orig.h)
  }

  return {
    x: west ? orig.x + (orig.w - w) : orig.x,
    y: north ? orig.y + (orig.h - h) : orig.y,
    w,
    h,
  }
}
