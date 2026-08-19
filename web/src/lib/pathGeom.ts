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
    // 填充的子路径**一律带闭合边**：matplotlib 填一条只有 MOVETO/LINETO、
    // 没有 CLOSEPOLY 的路径时会隐式闭合它并把那条边画出来，`closed` 却是
    // false。只按 `closed` 判会漏掉一条真实存在的边。
    if ((p.closed || geom.fill) && pts.length > 2) {
      const d = segDistMm(size, px, py, pts[pts.length - 1], pts[0])
      if (d < best) best = d
    }
  }
  return best
}

/**
 * 点是否落在填充区域内部。
 *
 * **nonzero 缠绕数**——不是 even-odd。matplotlib 出的 SVG 不写 `fill-rule`，
 * 也就是用 SVG 的默认值 nonzero；同向嵌套的两个环之间那块在渲染器眼里是
 * **实心的**，even-odd 的奇偶翻转却会把它当成洞，于是点在明明填了色的像素
 * 上却选不中。命中判据必须跟渲染器走。
 *
 * 反向嵌套（外环顺时针、内环逆时针）在 nonzero 下照旧是洞，PathPatch 带
 * 孔洞时用户期待的行为一点没变。
 *
 * 子路径**一律按闭合处理**：填充路径可以没有 CLOSEPOLY，matplotlib 仍会
 * 隐式闭合再填。首尾相接那条边由 `j = pts.length - 1` 的起手式覆盖。
 */
export function geomContains(geom: ElementGeometry, fx: number, fy: number): boolean {
  if (!geom.fill || !inClip(geom, fx, fy)) return false
  // isLeft > 0：点在有向边 a→b 的左侧
  const isLeft = (ax: number, ay: number, bx: number, by: number) =>
    (bx - ax) * (fy - ay) - (fx - ax) * (by - ay)
  let winding = 0
  for (const p of geom.paths) {
    const pts = p.points
    if (pts.length < 3) continue
    for (let i = 0, j = pts.length - 1; i < pts.length; j = i++) {
      const [xi, yi] = pts[i]
      const [xj, yj] = pts[j]
      if (yj <= fy) {
        if (yi > fy && isLeft(xj, yj, xi, yi) > 0) winding++
      } else if (yi <= fy && isLeft(xj, yj, xi, yi) < 0) winding--
    }
  }
  return winding !== 0
}

const cross3 = (o: Frac, p: Frac, q: Frac) =>
  (p[0] - o[0]) * (q[1] - o[1]) - (p[1] - o[1]) * (q[0] - o[0])

/** q 在 o–p 这条**线段**的包围盒里（只在三点共线时用来判「落在段上」） */
const onSpan = (o: Frac, p: Frac, q: Frac) =>
  q[0] >= Math.min(o[0], p[0]) &&
  q[0] <= Math.max(o[0], p[0]) &&
  q[1] >= Math.min(o[1], p[1]) &&
  q[1] <= Math.max(o[1], p[1])

/**
 * 两条线段是否相交。
 *
 * 共线那一格**必须再比一维区间**：四个叉积全为 0 只说明四点在同一条直线上，
 * 说明不了两段有重叠。旧写法用 `<= 0` 的乘积判据，于是 x=0–0.1 的水平段会被
 * 一个 x=0.8–0.9、y 相同的选择框边判成相交——框选把老远之外的水平/垂直线
 * 一起收走。
 */
export function segIntersectsSeg(a: Frac, b: Frac, c: Frac, d: Frac): boolean {
  const d1 = cross3(a, b, c)
  const d2 = cross3(a, b, d)
  const d3 = cross3(c, d, a)
  const d4 = cross3(c, d, b)
  if (
    ((d1 > 0 && d2 < 0) || (d1 < 0 && d2 > 0)) &&
    ((d3 > 0 && d4 < 0) || (d3 < 0 && d4 > 0))
  ) {
    return true
  }
  if (d1 === 0 && onSpan(a, b, c)) return true
  if (d2 === 0 && onSpan(a, b, d)) return true
  if (d3 === 0 && onSpan(c, d, a)) return true
  if (d4 === 0 && onSpan(c, d, b)) return true
  return false
}

/** 线段与矩形是否相交（含端点落在框内） */
function segHitsRect(a: Frac, b: Frac, r: FracRect): boolean {
  const inside = (p: Frac) =>
    p[0] >= r.x && p[0] <= r.x + r.w && p[1] >= r.y && p[1] <= r.y + r.h
  if (inside(a) || inside(b)) return true
  const corners: Frac[] = [
    [r.x, r.y],
    [r.x + r.w, r.y],
    [r.x + r.w, r.y + r.h],
    [r.x, r.y + r.h],
  ]
  for (let i = 0; i < 4; i++) {
    if (segIntersectsSeg(a, b, corners[i], corners[(i + 1) % 4])) return true
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
  // **先把选择框裁进 clip 再比**。只做一次粗略的「框与 clip 有重叠」是不够的：
  // 一条跨出子图的曲线，超出去那一段 matplotlib 根本没画，而横跨子图边界的
  // 选择框会只与那截不可见的延长线相交，于是框选选中了一条肉眼看不见的线。
  // 裁完之后任何交点都落在 clip 之内，也就必然是画出来的那部分。
  const box: FracRect = clip
    ? (() => {
        const x = Math.max(clip.x, r.x)
        const y = Math.max(clip.y, r.y)
        return {
          x,
          y,
          w: Math.min(clip.x + clip.w, r.x + r.w) - x,
          h: Math.min(clip.y + clip.h, r.y + r.h) - y,
        }
      })()
    : r
  if (box.w < 0 || box.h < 0) return false
  for (const p of geom.paths) {
    const pts = p.points
    for (let i = 1; i < pts.length; i++) {
      if (segHitsRect(pts[i - 1], pts[i], box)) return true
    }
    if (
      (p.closed || geom.fill) &&
      pts.length > 2 &&
      segHitsRect(pts[pts.length - 1], pts[0], box)
    ) {
      return true
    }
  }
  return false
}

/**
 * 命中容差（mm）：基础可用性容差与**可见墨迹半宽**取大的那个。
 *
 * 基础容差是给中心线用的。一条 12pt 的线半宽 ≈2.1mm，超出 1.5mm 的容差：
 * 点在明明画着墨的像素上却选不中它——而改成按路径命中之前的 bbox 判据
 * 是能选中的，所以这是一次实打实的退步。
 */
export function geomHitTolMm(geom: ElementGeometry, baseMm: number): number {
  const pt = geom.stroke ? (geom.stroke_pt ?? 0) : 0
  return Math.max(baseMm, (pt * 25.4) / 72 / 2)
}

/** 闭合路径的面积（分数²，取绝对值之和）；命中评分与 bbox 面积同一量纲 */
export function geomAreaFrac(geom: ElementGeometry): number {
  let total = 0
  for (const p of geom.paths) {
    // 填充路径可以没有 CLOSEPOLY（matplotlib 隐式闭合再填），面积照样成立
    if (!(p.closed || geom.fill) || p.points.length < 3) continue
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
