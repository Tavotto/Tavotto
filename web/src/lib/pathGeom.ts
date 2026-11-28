import type { ElementGeometry } from './api'
import { headGeometry, headSizeFactor, type ArrowHeadShape, type ShapeOutline } from './shapeGeometry'
import { readPath, type PathObject, type PathPoint, type PathSubpath } from '@/types/document'

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
  if ((!geom.fill && !geom.stroke) || !inClip(geom, fx, fy)) return Infinity
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
  if (!geom.fill && !geom.stroke) return false
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

/**
 * 「不透明填充」的阈值（ADR 0086 修订）：填充此刻的有效不透明度 ≥ 它，才算能把下面的线**盖住**。
 * 0.5 是肉眼分界：半透明幕墙（alpha 0.1–0.3）下面的线依然看得清、用户点它就是要选线；
 * 过半之后底下的东西已经退成背景。取值改动要同步 ADR。
 */
export const OPAQUE_FILL_ALPHA = 0.5

/** 描边层里「离光标多远」的量化档宽（mm）：同一档内再比墨迹面积 / 绘制次序 */
export const STROKE_DIST_BUCKET_MM = 0.5

/** 这份 geometry 的填充能不能盖住画在它**下面**的东西：有填充、有效不透明度达阈值 */
export function geomIsOpaqueFill(geom: ElementGeometry): boolean {
  return geom.fill && (geom.fill_alpha ?? 0) >= OPAQUE_FILL_ALPHA
}

/**
 * 光标离**可见墨迹边缘**的距离（mm）量化成的档号：落在墨迹上 = 0，每多 `STROKE_DIST_BUCKET_MM`
 * 加一档。入参 `distMm` 是到中心线的距离；粗线的半宽先扣掉（点在画出来的粗线上就是 0 档，
 * 不因为中心线远而吃亏）。
 */
export function strokeDistBucket(geom: ElementGeometry | null, distMm: number): number {
  const half = geom && geom.stroke ? ((geom.stroke_pt ?? 0) * 25.4) / 72 / 2 : 0
  const edge = distMm - half
  return edge <= 1e-9 ? 0 : Math.ceil(edge / STROKE_DIST_BUCKET_MM - 1e-9)
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
    // 填充路径按隐式闭合算（与 L79/L205/L232 同一判据）：少算这条边会让
    // 墨迹面积被低估，重叠元素的命中胜者可能因此反转
    if ((p.closed || geom.fill) && pts.length > 2) {
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

/* -------------------------------------------------------------------------- */
/*  画布对象 `path`（自由贝塞尔路径，ADR 0185 §二 / §四）                          */
/* -------------------------------------------------------------------------- */
//
// 上面那一半是图内元素（引擎给的分数坐标折线）；下面这一半是画布标注 `path` 的几何——
// 同一个主题（路径上的命中 / 描边 / 包围盒），所以放在同一个文件里，不开第二个。
//
// **与后端 `rendercore/geometry.py` 的 `read_path` / `path_segments` 严格同源**：共享向量
// `tests/golden/path_vectors.json` 两侧各跑一遍。规则：
// * 比例坐标 × 包围盒（`w` / `h`，单位随调用方：画布世界 px、导出预览页面 mm、后端 pt）；
// * 相邻两锚点：前一个的出手柄与后一个的入手柄**都**与各自锚点重合 → 直线段 `L`，否则三次贝塞尔 `C`；
// * 闭合子路径补一段「末 → 首」再 `Z`；少于两个锚点的子路径不画；
// * 开放子路径（且有描边）两端按 `headStart` / `headEnd` 画端型（`shapeGeometry.headGeometry`，与箭头同一份），
//   端型方向 = 那一端的切线（往里第一个不与端点重合的控制点）；实心端型让描边回缩：端点与相邻手柄一起
//   沿切线平移回缩量。填充用未回缩的几何。


/** 路径的缺省拐角：与封闭形状 / 箭头相同（圆角）；与后端 `geometry.PATH_DEFAULT_JOIN` 同一个缺省 */
export const PATH_DEFAULT_JOIN = 'round' as const

export type PathSeg =
  | ['M', number, number]
  | ['L', number, number]
  | ['C', number, number, number, number, number, number]
  | ['Z']

type XY = [number, number]
interface ScaledNode {
  p: XY
  in: XY
  out: XY
}

const sameXY = (a: XY, b: XY) => a[0] === b[0] && a[1] === b[1]
const addXY = (a: XY, d: XY): XY => [a[0] + d[0], a[1] + d[1]]

function segTo(a: ScaledNode, b: ScaledNode, out: PathSeg[]): void {
  if (sameXY(a.out, a.p) && sameXY(b.in, b.p)) out.push(['L', b.p[0], b.p[1]])
  else out.push(['C', a.out[0], a.out[1], b.in[0], b.in[1], b.p[0], b.p[1]])
}

function subpathSegs(nodes: ScaledNode[], closed: boolean, out: PathSeg[]): void {
  out.push(['M', nodes[0].p[0], nodes[0].p[1]])
  for (let i = 1; i < nodes.length; i++) segTo(nodes[i - 1], nodes[i], out)
  if (closed) {
    segTo(nodes[nodes.length - 1], nodes[0], out)
    out.push(['Z'])
  }
}

/** `tip` 往里第一个不与它重合的控制点；全重合 = null（退化，端型方向取零向量，与零长箭头一致） */
function firstDistinct(tip: XY, cands: XY[]): XY | null {
  for (const c of cands) if (Math.hypot(c[0] - tip[0], c[1] - tip[1]) > 1e-9) return c
  return null
}

function unitTo(from: XY | null, to: XY | null): XY {
  if (!from || !to) return [0, 0]
  const dx = to[0] - from[0]
  const dy = to[1] - from[1]
  const len = Math.hypot(dx, dy) || 1
  return [dx / len, dy / len]
}

export interface PathGeometry {
  /** 全部子路径、未回缩：填充用它；没有端型时描边也是它 */
  fill: PathSeg[]
  /** 描边：开放子路径两端按实心端型回缩 */
  stroke: PathSeg[]
  /** 画出来的端型（每条开放子路径先终点、后起点——与箭头的绘制次序相同） */
  heads: ArrowHeadShape[]
  /** 画出来的端型里最大的帽半宽（命中带用）；没有端型 = 0 */
  headHalf: number
}

type PathGeomInput = Pick<
  PathObject,
  'subpaths' | 'fillRule' | 'stroke' | 'headStart' | 'headEnd' | 'headSizeStart' | 'headSizeEnd'
>

/**
 * 路径实际生效的描边对齐（ADR 0185 M2）：设了内侧 / 外侧、有描边、而且每条画得出的子路径都闭合，才生效；否则 null（居中）。
 * 后端 `geometry.path_stroke_align` 同一条判据。
 */
export function pathStrokeAlign(
  o: Pick<PathObject, 'subpaths' | 'fillRule' | 'stroke' | 'headStart' | 'headEnd' | 'strokeAlign'>,
): 'inside' | 'outside' | null {
  if (o.strokeAlign !== 'inside' && o.strokeAlign !== 'outside') return null
  const r = readPath(o)
  if (!r.strokeOn) return null
  const drawn = r.subpaths.filter((sp) => sp.nodes.length >= 2)
  return drawn.length > 0 && drawn.every((sp) => sp.closed) ? o.strokeAlign : null
}

/** 外侧对齐的裁剪区：框四周各放出 `pad` 的大矩形 + 路径本身，按奇偶规则 = 路径外面那一圈（前后端同一个 pad 规则） */
export function outsideClipPad(w: number, h: number, sw: number): number {
  return Math.max(w, h) + 4 * sw
}

/** 路径在框空间（原点框左上、y 向下）里的几何。`w` / `h` / `sw` 同单位。与后端 `geometry.path_segments` 同源 */
export function pathGeometry(o: PathGeomInput, w: number, h: number, sw: number): PathGeometry {
  const r = readPath(o)
  const sc = (q: PathPoint): XY => [q[0] * w, q[1] * h]
  const fill: PathSeg[] = []
  const stroke: PathSeg[] = []
  const heads: ArrowHeadShape[] = []
  let headHalf = 0
  const fStart = headSizeFactor(o.headSizeStart)
  const fEnd = headSizeFactor(o.headSizeEnd)
  const wantHeads = r.strokeOn && (r.headStart !== 'none' || r.headEnd !== 'none')
  for (const sp of r.subpaths) {
    const n = sp.nodes.length
    if (n < 2) continue
    const nodes: ScaledNode[] = sp.nodes.map((q) => ({ p: sc(q.p), in: sc(q.in), out: sc(q.out) }))
    subpathSegs(nodes, sp.closed, fill)
    if (sp.closed || !wantHeads) {
      subpathSegs(nodes, sp.closed, stroke)
      continue
    }
    const first = nodes[0]
    const last = nodes[n - 1]
    const us = unitTo(first.p, firstDistinct(first.p, [first.out, nodes[1].in, nodes[1].p]))
    const ue = unitTo(firstDistinct(last.p, [last.in, nodes[n - 2].out, nodes[n - 2].p]), last.p)
    const e = headGeometry(r.headEnd, { x: last.p[0], y: last.p[1] }, 1, ue[0], ue[1], sw, fEnd)
    const s = headGeometry(r.headStart, { x: first.p[0], y: first.p[1] }, -1, us[0], us[1], sw, fStart)
    const trimmed = nodes.slice()
    if (s.trim) {
      const d: XY = [us[0] * s.trim, us[1] * s.trim]
      trimmed[0] = { p: addXY(first.p, d), in: first.in, out: addXY(first.out, d) }
    }
    if (e.trim) {
      const d: XY = [-ue[0] * e.trim, -ue[1] * e.trim]
      trimmed[n - 1] = { p: addXY(last.p, d), in: addXY(last.in, d), out: last.out }
    }
    subpathSegs(trimmed, false, stroke)
    if (e.shape) {
      heads.push(e.shape)
      headHalf = Math.max(headHalf, sw * 1.7 * fEnd)
    }
    if (s.shape) {
      heads.push(s.shape)
      headHalf = Math.max(headHalf, sw * 1.7 * fStart)
    }
  }
  return { fill, stroke, heads, headHalf }
}

/**
 * 一条已补齐手柄的子路径（任意坐标系，`map` 把点换到要画的地方）→ d 串；L / C 的判据与 `pathGeometry` 同一条。
 * 钢笔草稿与锚点编辑的覆盖层用它（它们手里是页面 mm 的锚点，不是比例坐标）。
 */
export function subpathD(
  sp: { closed: boolean; nodes: readonly { p: readonly [number, number]; in: readonly [number, number]; out: readonly [number, number] }[] },
  map: (q: readonly [number, number]) => [number, number],
): string {
  if (!sp.nodes.length) return ''
  const nodes: ScaledNode[] = sp.nodes.map((n) => ({ p: map(n.p), in: map(n.in), out: map(n.out) }))
  // L / C 的判据要看**原坐标**里重合没有（换算后浮点可能把重合的点算出一点点差）
  const segs: PathSeg[] = []
  segs.push(['M', nodes[0].p[0], nodes[0].p[1]])
  const step = (a: number, b: number) => {
    const A = sp.nodes[a]
    const B = sp.nodes[b]
    const straight = A.out[0] === A.p[0] && A.out[1] === A.p[1] && B.in[0] === B.p[0] && B.in[1] === B.p[1]
    if (straight) segs.push(['L', nodes[b].p[0], nodes[b].p[1]])
    else segs.push(['C', nodes[a].out[0], nodes[a].out[1], nodes[b].in[0], nodes[b].in[1], nodes[b].p[0], nodes[b].p[1]])
  }
  for (let i = 1; i < nodes.length; i++) step(i - 1, i)
  if (sp.closed && nodes.length > 1) {
    step(nodes.length - 1, 0)
    segs.push(['Z'])
  }
  return pathD(segs)
}

/** 段 → SVG path 的 d 串（数字原样，不截位：画布与导出逐点一致） */
export function pathD(segs: readonly PathSeg[]): string {
  return segs.map((s) => (s.length === 1 ? 'Z' : `${s[0]} ${s.slice(1).join(' ')}`)).join(' ')
}

/** 选中描示的轮廓（`OverlaySvg.ObjectOutline`，单位随调用方）：与 PathView 画的、命中层认的是同一条 */
export function pathOutline(o: PathGeomInput, w: number, h: number, sw: number): ShapeOutline {
  const d = pathD(pathGeometry(o, w, h, sw).fill)
  return d ? { kind: 'path', d } : null
}

/** 三次贝塞尔一维的极值参数（开区间 (0,1) 内） */
function cubicExtremaT(p0: number, p1: number, p2: number, p3: number): number[] {
  const a = -p0 + 3 * p1 - 3 * p2 + p3
  const b = 2 * (p0 - 2 * p1 + p2)
  const c = p1 - p0
  const ts: number[] = []
  if (Math.abs(a) < 1e-12) {
    if (Math.abs(b) > 1e-12) ts.push(-c / b)
  } else {
    const disc = b * b - 4 * a * c
    if (disc >= 0) {
      const sq = Math.sqrt(disc)
      ts.push((-b + sq) / (2 * a), (-b - sq) / (2 * a))
    }
  }
  return ts.filter((t) => t > 0 && t < 1)
}

const cubicAt = (p0: number, p1: number, p2: number, p3: number, t: number) => {
  const u = 1 - t
  return u * u * u * p0 + 3 * u * u * t * p1 + 3 * u * t * t * p2 + t * t * t * p3
}

/**
 * 段的**曲线实际**包围盒（不按控制点：手柄可以落在框外，ADR 0185 §二）。空 = null。
 * 包围盒的权威是对象的 `x/y/w/h`；这里只给「锚点改了以后该把框收到哪」的那个数。
 */
export function segsExtent(segs: readonly PathSeg[]): { x0: number; y0: number; x1: number; y1: number } | null {
  let x0 = Infinity
  let y0 = Infinity
  let x1 = -Infinity
  let y1 = -Infinity
  const take = (x: number, y: number) => {
    if (x < x0) x0 = x
    if (x > x1) x1 = x
    if (y < y0) y0 = y
    if (y > y1) y1 = y
  }
  let cx = 0
  let cy = 0
  for (const s of segs) {
    if (s[0] === 'M' || s[0] === 'L') {
      cx = s[1]
      cy = s[2]
      take(cx, cy)
    } else if (s[0] === 'C') {
      const [, ax, ay, bx, by, ex, ey] = s
      for (const t of cubicExtremaT(cx, ax, bx, ex)) take(cubicAt(cx, ax, bx, ex, t), cubicAt(cy, ay, by, ey, t))
      for (const t of cubicExtremaT(cy, ay, by, ey)) take(cubicAt(cx, ax, bx, ex, t), cubicAt(cy, ay, by, ey, t))
      cx = ex
      cy = ey
      take(cx, cy)
    }
  }
  return Number.isFinite(x0) ? { x0, y0, x1, y1 } : null
}

/** 每段三次贝塞尔打散成多少段折线（命中用；0.1 mm 级的路径上误差远小于命中容差） */
const FLATTEN_STEPS = 24

/** 段 → 折线（每个 M 起一条；Z 记成闭合） */
function flatten(segs: readonly PathSeg[]): { pts: XY[]; closed: boolean }[] {
  const out: { pts: XY[]; closed: boolean }[] = []
  let cur: { pts: XY[]; closed: boolean } | null = null
  for (const s of segs) {
    if (s[0] === 'M') {
      cur = { pts: [[s[1], s[2]]], closed: false }
      out.push(cur)
    } else if (!cur) {
      continue
    } else if (s[0] === 'L') {
      cur.pts.push([s[1], s[2]])
    } else if (s[0] === 'C') {
      const [px, py] = cur.pts[cur.pts.length - 1]
      for (let i = 1; i <= FLATTEN_STEPS; i++) {
        const t = i / FLATTEN_STEPS
        cur.pts.push([cubicAt(px, s[1], s[3], s[5], t), cubicAt(py, s[2], s[4], s[6], t)])
      }
    } else {
      cur.closed = true
    }
  }
  return out
}

function distToSeg(px: number, py: number, a: XY, b: XY): number {
  const dx = b[0] - a[0]
  const dy = b[1] - a[1]
  const len2 = dx * dx + dy * dy
  let t = len2 ? ((px - a[0]) * dx + (py - a[1]) * dy) / len2 : 0
  t = t < 0 ? 0 : t > 1 ? 1 : t
  return Math.hypot(px - (a[0] + dx * t), py - (a[1] + dy * t))
}

/**
 * 路径的命中（画布 `PathView` 透明命中层的纯函数版，单位 mm、对象自己的框空间、未旋转）：
 * * **填充**按 `fillRule`（缺省 nonzero，与渲染器同一判据；每条子路径按隐式闭合填）。有填充、或既无填充又无描边
 *   （整块可点，否则只剩一圈看不见的轮廓——与形状同一条）时，点在内部即命中；
 * * **描边**按 mm 距离：离描边中心线 ≤ max(容差, 半线宽, 端型帽半宽)。距离在 mm 里量，扁框上横竖同一口径。
 */
export function pathHitTest(o: PathObject, x: number, y: number, tolMm: number, swMm: number): boolean {
  const g = pathGeometry(o, o.w, o.h, swMm)
  const r = readPath(o)
  if (r.strokeOn) {
    const reach = Math.max(tolMm, swMm / 2, g.headHalf)
    for (const line of flatten(g.stroke)) {
      const pts = line.pts
      for (let i = 1; i < pts.length; i++) if (distToSeg(x, y, pts[i - 1], pts[i]) <= reach) return true
      if (line.closed && pts.length > 2 && distToSeg(x, y, pts[pts.length - 1], pts[0]) <= reach) return true
    }
  }
  const filled = !!o.fill || !r.strokeOn
  if (!filled) return false
  let winding = 0
  let crossings = 0
  for (const line of flatten(g.fill)) {
    const pts = line.pts
    if (pts.length < 3) continue
    for (let i = 0, j = pts.length - 1; i < pts.length; j = i++) {
      const [xi, yi] = pts[i]
      const [xj, yj] = pts[j]
      const left = (xi - xj) * (y - yj) - (x - xj) * (yi - yj)
      if (yj <= y) {
        if (yi > y && left > 0) {
          winding++
          crossings++
        }
      } else if (yi <= y && left < 0) {
        winding--
        crossings++
      }
    }
  }
  return r.fillRule === 'evenodd' ? crossings % 2 === 1 : winding !== 0
}

/** 画布语义的旋转（CSS `rotate(deg)`：y 向下空间里顺时针），绕 `c` 转点 `p` */
export function rotateAbout(p: XY, c: XY, deg: number): XY {
  if (!deg) return p
  const rad = (deg * Math.PI) / 180
  const cos = Math.cos(rad)
  const sin = Math.sin(rad)
  const dx = p[0] - c[0]
  const dy = p[1] - c[1]
  return [c[0] + dx * cos - dy * sin, c[1] + dx * sin + dy * cos]
}

/** 包围盒一维的下限（mm）：水平 / 竖直的直线路径另一维是 0，与箭头被钳到的 0.01 mm 同一量级 */
export const MIN_PATH_DIM_MM = 0.01

/**
 * 页面 mm（未旋转）里的子路径 → 一个 `path` 对象的权威包围盒 + 比例坐标（ADR 0185 §二：包围盒按曲线
 * 实际范围算）。`rotationDeg` / `pivot` 是对象的旋转与原来那个框的中心：框换了中心，旋转中心跟着变，
 * 所以整体再平移一次（`d = R·v − v`，v = 新中心 − 旧中心），画面上逐点不动。
 */
export function fitPathBox(
  subpathsMm: readonly PathSubpath[],
  rotationDeg: number,
  pivot: XY,
): { x: number; y: number; w: number; h: number; subpaths: PathSubpath[] } | null {
  const segs: PathSeg[] = []
  for (const sp of subpathsMm) {
    if (sp.nodes.length < 2) continue
    subpathSegs(
      sp.nodes.map((q) => ({ p: q.p, in: q.in ?? q.p, out: q.out ?? q.p })),
      sp.closed,
      segs,
    )
  }
  const ext = segsExtent(segs)
  if (!ext) return null
  let w = ext.x1 - ext.x0
  let h = ext.y1 - ext.y0
  let x = ext.x0
  let y = ext.y0
  if (w < MIN_PATH_DIM_MM) {
    x -= (MIN_PATH_DIM_MM - w) / 2
    w = MIN_PATH_DIM_MM
  }
  if (h < MIN_PATH_DIM_MM) {
    y -= (MIN_PATH_DIM_MM - h) / 2
    h = MIN_PATH_DIM_MM
  }
  const frac = (q: PathPoint): PathPoint => [(q[0] - x) / w, (q[1] - y) / h]
  const subpaths = subpathsMm.map((sp) => ({
    closed: sp.closed,
    nodes: sp.nodes.map((q) => ({
      p: frac(q.p),
      ...(q.in ? { in: frac(q.in) } : {}),
      ...(q.out ? { out: frac(q.out) } : {}),
      ...(q.smooth ? { smooth: true as const } : {}),
    })),
  }))
  if (rotationDeg) {
    const c: XY = [x + w / 2, y + h / 2]
    const moved = rotateAbout(c, pivot, rotationDeg)
    x += moved[0] - c[0]
    y += moved[1] - c[1]
  }
  return { x, y, w, h, subpaths }
}
