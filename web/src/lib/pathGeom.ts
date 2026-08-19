import type { ElementGeometry } from './api'

/**
 * 图内元素**真实路径**上的几何运算：命中、框选、描边、乐观位移。
 *
 * 输入的 geometry 一律是引擎算好的 figure 分数坐标（y 向下），前端**只消费、
 * 不推算**——数据坐标、scale、clip、贝塞尔细分全在 matplotlib 那边，这边猜
 * 一次就多一份会分岔的实现。
 *
 * 距离一律换到 **mm** 再比：分数坐标的 x/y 各自除以图宽图高，直接在分数系里
 * 算距离会让扁图上的横向容差比纵向大好几倍（与图内箭头的 arrowDistMm 同一
 * 口径）。
 */

export type Frac = [number, number]
export interface FracRect {
  x: number
  y: number
  w: number
  h: number
}

/** 裁剪框之外的点看不见，也就不该命中 */
function inClip(geom: ElementGeometry, fx: number, fy: number): boolean {
  const c = geom.clip
  if (!c) return true
  return fx >= c[0] && fx <= c[0] + c[2] && fy >= c[1] && fy <= c[1] + c[3]
}

function clipRect(geom: ElementGeometry): FracRect | null {
  const c = geom.clip
  return c ? { x: c[0], y: c[1], w: c[2], h: c[3] } : null
}

function rectsOverlap(a: FracRect, b: FracRect): boolean {
  return a.x < b.x + b.w && a.x + a.w > b.x && a.y < b.y + b.h && a.y + a.h > b.y
}

/** 点到线段的距离（mm 系：x/y 分别乘图宽图高，算出来才是视觉距离） */
function segDistMm(
  size: readonly [number, number],
  px: number,
  py: number,
  a: Frac,
  b: Frac,
): number {
  const ax = a[0] * size[0]
  const ay = a[1] * size[1]
  const bx = b[0] * size[0]
  const by = b[1] * size[1]
  const dx = bx - ax
  const dy = by - ay
  const len2 = dx * dx + dy * dy
  let t = len2 ? ((px - ax) * dx + (py - ay) * dy) / len2 : 0
  t = t < 0 ? 0 : t > 1 ? 1 : t
  return Math.hypot(px - (ax + dx * t), py - (ay + dy * t))
}

/** 点到整条 geometry 的最短距离（mm）。裁剪框外一律 Infinity。 */
export function geomDistMm(
  geom: ElementGeometry,
  size: readonly [number, number],
  fx: number,
  fy: number,
): number {
  if (!inClip(geom, fx, fy)) return Infinity
  const px = fx * size[0]
  const py = fy * size[1]
  let best = Infinity
  for (const p of geom.paths) {
    const pts = p.points
    for (let i = 1; i < pts.length; i++) {
      const d = segDistMm(size, px, py, pts[i - 1], pts[i])
      if (d < best) best = d
    }
    if (p.closed && pts.length > 2) {
      const d = segDistMm(size, px, py, pts[pts.length - 1], pts[0])
      if (d < best) best = d
    }
  }
  return best
}

/**
 * 点是否落在填充区域内部。**even-odd**（与描边时的 fill-rule 同一条）：
 * 一个环里套一个环时中间那块是洞，点在洞里不算命中——这正是 PathPatch
 * 带孔洞时用户期待的行为。
 */
export function geomContains(geom: ElementGeometry, fx: number, fy: number): boolean {
  if (!geom.fill || !inClip(geom, fx, fy)) return false
  let inside = false
  for (const p of geom.paths) {
    if (!p.closed || p.points.length < 3) continue
    const pts = p.points
    for (let i = 0, j = pts.length - 1; i < pts.length; j = i++) {
      const [xi, yi] = pts[i]
      const [xj, yj] = pts[j]
      if (yi > fy !== yj > fy && fx < ((xj - xi) * (fy - yi)) / (yj - yi) + xi) {
        inside = !inside
      }
    }
  }
  return inside
}

/** 线段与矩形是否相交（含端点落在框内）；与 elementGeom.segIntersectsRect 同一算法 */
function segHitsRect(a: Frac, b: Frac, r: FracRect): boolean {
  const inside = (p: Frac) =>
    p[0] >= r.x && p[0] <= r.x + r.w && p[1] >= r.y && p[1] <= r.y + r.h
  if (inside(a) || inside(b)) return true
  const cross = (o: Frac, p: Frac, q: Frac) =>
    (p[0] - o[0]) * (q[1] - o[1]) - (p[1] - o[1]) * (q[0] - o[0])
  const corners: Frac[] = [
    [r.x, r.y],
    [r.x + r.w, r.y],
    [r.x + r.w, r.y + r.h],
    [r.x, r.y + r.h],
  ]
  for (let i = 0; i < 4; i++) {
    const c = corners[i]
    const d = corners[(i + 1) % 4]
    if (cross(a, b, c) * cross(a, b, d) <= 0 && cross(c, d, a) * cross(c, d, b) <= 0) return true
  }
  return false
}

/**
 * 框选：选择框与路径**本身**相交才算命中（整条路径落进框里同样算——端点在
 * 框内即相交）。刻意**不**把「框整个落在一大块填充内部」算作命中，与
 * Illustrator 语义一致：框选是「圈住看得见的墨迹」，不是「戳进去」。
 */
export function geomHitsRect(geom: ElementGeometry, r: FracRect): boolean {
  const clip = clipRect(geom)
  if (clip && !rectsOverlap(clip, r)) return false
  for (const p of geom.paths) {
    const pts = p.points
    for (let i = 1; i < pts.length; i++) {
      if (segHitsRect(pts[i - 1], pts[i], r)) return true
    }
    if (p.closed && pts.length > 2 && segHitsRect(pts[pts.length - 1], pts[0], r)) return true
  }
  return false
}

/** 闭合路径的面积（分数²，取绝对值之和）；命中评分与 bbox 面积同一量纲 */
export function geomAreaFrac(geom: ElementGeometry): number {
  let total = 0
  for (const p of geom.paths) {
    if (!p.closed || p.points.length < 3) continue
    let a = 0
    const pts = p.points
    for (let i = 0, j = pts.length - 1; i < pts.length; j = i++) {
      a += pts[j][0] * pts[i][1] - pts[i][0] * pts[j][1]
    }
    total += Math.abs(a) / 2
  }
  return total
}

/**
 * 描边的「墨迹面积」（分数²）：路径长度 × 2×容差，换算回分数系。
 * 与图内箭头的评分同一口径，因此一条压在子图上的曲线总能赢过子图容器。
 */
export function geomInkAreaFrac(
  geom: ElementGeometry,
  size: readonly [number, number],
  tolMm: number,
): number {
  let lenMm = 0
  for (const p of geom.paths) {
    const pts = p.points
    for (let i = 1; i < pts.length; i++) {
      lenMm += Math.hypot(
        (pts[i][0] - pts[i - 1][0]) * size[0],
        (pts[i][1] - pts[i - 1][1]) * size[1],
      )
    }
    if (p.closed && pts.length > 2) {
      lenMm += Math.hypot(
        (pts[0][0] - pts[pts.length - 1][0]) * size[0],
        (pts[0][1] - pts[pts.length - 1][1]) * size[1],
      )
    }
  }
  const area = size[0] * size[1]
  return area > 0 ? (lenMm * 2 * tolMm) / area : 0
}

/** 拖动中的乐观位移：几何跟着同一个 dfx/dfy 走（分数系，直接加） */
export function translateGeom(
  geom: ElementGeometry,
  dfx: number,
  dfy: number,
): ElementGeometry {
  if (!dfx && !dfy) return geom
  return {
    ...geom,
    paths: geom.paths.map((p) => ({
      closed: p.closed,
      points: p.points.map(([x, y]) => [x + dfx, y + dfy] as Frac),
    })),
    clip: geom.clip
      ? [geom.clip[0] + dfx, geom.clip[1] + dfy, geom.clip[2], geom.clip[3]]
      : undefined,
  }
}

/** geometry → SVG path 的 d 串。`toPoint` 把分数坐标换成屏幕坐标。 */
export function geomPathD(
  geom: ElementGeometry,
  toPoint: (p: Frac) => { x: number; y: number },
): string {
  const out: string[] = []
  for (const p of geom.paths) {
    if (p.points.length < 2) continue
    p.points.forEach((pt, i) => {
      const s = toPoint(pt)
      out.push(`${i === 0 ? 'M' : 'L'}${s.x.toFixed(2)},${s.y.toFixed(2)}`)
    })
    if (p.closed) out.push('Z')
  }
  return out.join(' ')
}
