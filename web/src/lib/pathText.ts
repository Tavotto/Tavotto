import { pathGeometry, type PathSeg } from '@/lib/pathGeom'
import type { PathTextObject } from '@/types/document'

/**
 * 路径文字（ADR 0190）的路径几何：与后端 `rendercore/pathtext.py` 同一个展平规则（第一条画得出的子路径；直线段原样、
 * 三次贝塞尔按 t 等分 `FLATTEN_STEPS` 段），`tests/golden/path_text_vectors.json` 对拍。字落在哪只由后端算——
 * 这里只管起点柄画在哪、点 / 拖在路径上的哪一处（弧长比例）。
 */
export const FLATTEN_STEPS = 32

export type XY = [number, number]

export interface ArcPath {
  pts: XY[]
  cum: number[]
  closed: boolean
}

const cubic = (p0: XY, p1: XY, p2: XY, p3: XY, t: number): XY => {
  const mt = 1 - t
  return [
    mt ** 3 * p0[0] + 3 * mt * mt * t * p1[0] + 3 * mt * t * t * p2[0] + t ** 3 * p3[0],
    mt ** 3 * p0[1] + 3 * mt * mt * t * p1[1] + 3 * mt * t * t * p2[1] + t ** 3 * p3[1],
  ]
}

/** 填充几何 → 第一条子路径的折线与是否闭合（后端 `pathtext.polyline`） */
export function polyline(segs: readonly PathSeg[]): { pts: XY[]; closed: boolean } {
  const pts: XY[] = []
  let closed = false
  for (const s of segs) {
    if (s[0] === 'M') {
      if (pts.length) break
      pts.push([s[1], s[2]])
    } else if (s[0] === 'L' && pts.length) pts.push([s[1], s[2]])
    else if (s[0] === 'C' && pts.length) {
      const p0 = pts[pts.length - 1]
      for (let k = 1; k <= FLATTEN_STEPS; k++) pts.push(cubic(p0, [s[1], s[2]], [s[3], s[4]], [s[5], s[6]], k / FLATTEN_STEPS))
    } else if (s[0] === 'Z' && pts.length) {
      closed = true
      const a = pts[0]
      const b = pts[pts.length - 1]
      if (a[0] !== b[0] || a[1] !== b[1]) pts.push(a)
      break
    }
  }
  return { pts, closed }
}

export function arcTable(pts: readonly XY[]): number[] {
  const cum = [0]
  for (let i = 1; i < pts.length; i++) cum.push(cum[i - 1] + Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]))
  return cum
}

/** 路径文字的路径（框空间，单位随 w / h）；`flip` 时反着走（后端 `pathtext.path_of`） */
export function arcPathOf(o: Pick<PathTextObject, 'subpaths' | 'flip'>, w: number, h: number): ArcPath {
  const { pts, closed } = polyline(pathGeometry({ subpaths: o.subpaths }, w, h, 0).fill)
  if (o.flip) pts.reverse()
  return { pts, cum: arcTable(pts), closed }
}

/** 弧长 s 处的点与切线角（后端 `pathtext.point_at`）。开放路径越界 = null；闭合路径绕回 */
export function pointAt(a: ArcPath, s: number): { x: number; y: number; angle: number } | null {
  const { pts, cum, closed } = a
  const total = cum[cum.length - 1] ?? 0
  if (!(total > 0) || pts.length < 2) return null
  if (closed) s = ((s % total) + total) % total
  else if (s < -1e-9 || s > total + 1e-9) return null
  s = Math.min(Math.max(s, 0), total)
  let i = 1
  while (i < cum.length - 1 && cum[i] < s) i++
  while (i < cum.length - 1 && cum[i] - cum[i - 1] <= 1e-12) i++
  const seg = cum[i] - cum[i - 1]
  const p = pts[i - 1]
  const q = pts[i]
  const u = seg <= 1e-12 ? 0 : (s - cum[i - 1]) / seg
  return { x: p[0] + (q[0] - p[0]) * u, y: p[1] + (q[1] - p[1]) * u, angle: Math.atan2(q[1] - p[1], q[0] - p[0]) }
}

/** 离 q 最近的路径上的点，回它的弧长比例 0–1 与距离（点在路径上落起点、拖起点柄都用它） */
export function nearestOffset(a: ArcPath, q: XY): { offset: number; dist: number } | null {
  const { pts, cum } = a
  const total = cum[cum.length - 1] ?? 0
  if (!(total > 0)) return null
  let best = { s: 0, d: Infinity }
  for (let i = 1; i < pts.length; i++) {
    const p = pts[i - 1]
    const r = pts[i]
    const dx = r[0] - p[0]
    const dy = r[1] - p[1]
    const len2 = dx * dx + dy * dy
    const u = len2 > 0 ? Math.min(1, Math.max(0, ((q[0] - p[0]) * dx + (q[1] - p[1]) * dy) / len2)) : 0
    const d = Math.hypot(p[0] + dx * u - q[0], p[1] + dy * u - q[1])
    if (d < best.d) best = { s: cum[i - 1] + (cum[i] - cum[i - 1]) * u, d }
  }
  return { offset: best.s / total, dist: best.d }
}

/** 起点柄（起点比例处，框空间）。`flip` 时比例沿反方向量——与后端同一个口径 */
export function startHandle(o: PathTextObject, w: number, h: number): { x: number; y: number; angle: number } | null {
  const a = arcPathOf(o, w, h)
  const total = a.cum[a.cum.length - 1] ?? 0
  return pointAt(a, clampOffset(o.startOffset) * total)
}

export const clampOffset = (v: number | undefined): number =>
  typeof v === 'number' && Number.isFinite(v) ? Math.min(1, Math.max(0, v)) : 0
