import type { AnchorRef, MmNode, MmSubpath, XY } from './pathEdit'

/**
 * 实时圆角（ADR 0185 M3 第二批，对标 Illustrator 的 live corners）——**纯函数**。
 *
 * - 矩形：圆点在每个角里面、离角 (r, r) 的地方（= 圆角的圆心），拖它改形状本来就有的 `cornerRadius`（四个角一起）；
 * - 自由路径：两侧都是直线段的尖角锚点（锚点自己没有手柄、相邻两段都是直线、夹角在 5°–175°）才有圆点，圆点也在圆角的圆心上。
 *   拖它把这个角**烤成一段圆弧**：一个锚点换成两个（切点），中间一段三次贝塞尔逼近的圆弧——结果仍是普通 `path`，
 *   画布、导出、路径查找器不用认识任何新东西。
 *
 * 坐标一律在对象的未旋转空间（页面 mm），与 `pathEdit` 相同。
 */

const sub = (a: XY, b: XY): XY => [a[0] - b[0], a[1] - b[1]]
const add = (a: XY, b: XY): XY => [a[0] + b[0], a[1] + b[1]]
const mul = (a: XY, k: number): XY => [a[0] * k, a[1] * k]
const len = (a: XY) => Math.hypot(a[0], a[1])
const unit = (a: XY): XY => {
  const l = len(a)
  return l > 0 ? [a[0] / l, a[1] / l] : [0, 0]
}
const same = (a: XY, b: XY) => a[0] === b[0] && a[1] === b[1]

export interface CornerInfo {
  ref: AnchorRef
  /** 角的顶点 */
  p: XY
  /** 指向前一个 / 后一个锚点的单位向量 */
  u1: XY
  u2: XY
  /** 夹角（弧度） */
  theta: number
  /** 角平分线（指向角里面）的单位向量 */
  bis: XY
  /** 能用的最大半径：切点不越过相邻两段各自的一半（两头的角都圆了也不打架） */
  maxR: number
}

const MIN_ANGLE = (5 * Math.PI) / 180

/** 路径里能做实时圆角的角 */
export function pathCorners(subs: readonly MmSubpath[]): CornerInfo[] {
  const out: CornerInfo[] = []
  subs.forEach((sp, si) => {
    const n = sp.nodes.length
    if (n < 3) return
    for (let i = 0; i < n; i++) {
      if (!sp.closed && (i === 0 || i === n - 1)) continue
      const a = sp.nodes[i]
      const prev = sp.nodes[(i - 1 + n) % n]
      const next = sp.nodes[(i + 1) % n]
      // 两段都是直线：本锚点没有手柄、前一个锚点朝这边没有手柄、后一个锚点朝这边没有手柄
      if (!same(a.in, a.p) || !same(a.out, a.p) || !same(prev.out, prev.p) || !same(next.in, next.p)) continue
      const v1 = sub(prev.p, a.p)
      const v2 = sub(next.p, a.p)
      const l1 = len(v1)
      const l2 = len(v2)
      if (l1 <= 0 || l2 <= 0) continue
      const u1 = unit(v1)
      const u2 = unit(v2)
      const theta = Math.acos(Math.max(-1, Math.min(1, u1[0] * u2[0] + u1[1] * u2[1])))
      if (theta < MIN_ANGLE || theta > Math.PI - MIN_ANGLE) continue
      const bis = unit(add(u1, u2))
      out.push({ ref: { sp: si, i }, p: a.p, u1, u2, theta, bis, maxR: (Math.min(l1, l2) / 2) * Math.tan(theta / 2) })
    }
  })
  return out
}

/** 半径 r 的圆角，圆心在角平分线上离顶点 r / sin(θ/2) 处——圆点就画在这里 */
export function cornerWidget(c: CornerInfo, r: number, minMm: number): XY {
  const d = Math.max(r / Math.sin(c.theta / 2), minMm)
  return add(c.p, mul(c.bis, d))
}

/** 指针（未旋转空间）→ 这个角的半径：指针投影到角平分线上的距离当圆心距，夹到 [0, maxR] */
export function radiusFromPointer(c: CornerInfo, q: XY): number {
  const s = (q[0] - c.p[0]) * c.bis[0] + (q[1] - c.p[1]) * c.bis[1]
  return Math.max(0, Math.min(s * Math.sin(c.theta / 2), c.maxR))
}

/**
 * 把这些角都圆成半径 `r`（各自夹到自己的 maxR）：每个角的锚点换成两个切点，之间一段三次贝塞尔圆弧
 * （手柄长 4/3·tan(φ/4)·r，φ = π − θ 是转过的角）。r ≤ 0 或不是能圆的角 = 原样。
 */
export function roundCorners(subs: readonly MmSubpath[], refs: readonly AnchorRef[], r: number): MmSubpath[] {
  return roundCornersEach(subs, new Map(refs.map((a) => [`${a.sp}:${a.i}`, r])))
}

/** 每个角各自一个半径（键 `子路径:锚点`，没写的角不圆）；半径照样钳到这个角放得下的最大值 */
export function roundCornersEach(subs: readonly MmSubpath[], radii: ReadonlyMap<string, number>): MmSubpath[] {
  const corners = new Map(pathCorners(subs).filter((c) => radii.has(`${c.ref.sp}:${c.ref.i}`)).map((c) => [`${c.ref.sp}:${c.ref.i}`, c]))
  return subs.map((sp, si) => {
    const nodes: MmNode[] = []
    sp.nodes.forEach((n, i) => {
      const c = corners.get(`${si}:${i}`)
      const rr = c ? Math.min(radii.get(`${si}:${i}`) ?? 0, c.maxR) : 0
      if (!c || !(rr > 1e-9)) {
        nodes.push({ ...n, p: [...n.p], in: [...n.in], out: [...n.out] })
        return
      }
      nodes.push(...arcNodes(c, rr))
    })
    return { closed: sp.closed, nodes }
  })
}

/**
 * 一个角圆成半径 r 的锚点：两个切点之间的圆弧，转过的角 φ = π − θ 超过 90° 时切成 ⌈φ / 90°⌉ 段（一段三次贝塞尔逼近
 * 90° 以内的圆弧误差 < 0.03%；锐角一刀 150° 会偏 0.4%）。中间的锚点在圆上、是平滑点。
 */
function arcNodes(c: CornerInfo, r: number): MmNode[] {
  const phi = Math.PI - c.theta
  const n = Math.max(1, Math.ceil(phi / (Math.PI / 2) - 1e-9))
  const k = (4 / 3) * Math.tan(phi / (4 * n)) * r
  const ctr = add(c.p, mul(c.bis, r / Math.sin(c.theta / 2)))
  const p1 = add(c.p, mul(c.u1, r / Math.tan(c.theta / 2)))
  // 从切点 1 转到切点 2 的方向：切点 1 处的切线就是 -u1（朝顶点走）
  const a0 = Math.atan2(p1[1] - ctr[1], p1[0] - ctr[0])
  const tan0: XY = [-Math.sin(a0), Math.cos(a0)]
  const sgn = tan0[0] * -c.u1[0] + tan0[1] * -c.u1[1] >= 0 ? 1 : -1
  const out: MmNode[] = []
  for (let j = 0; j <= n; j++) {
    const a = a0 + (sgn * phi * j) / n
    const q: XY = j === 0 ? p1 : j === n ? add(c.p, mul(c.u2, r / Math.tan(c.theta / 2))) : [ctr[0] + r * Math.cos(a), ctr[1] + r * Math.sin(a)]
    const tng: XY = [-Math.sin(a) * sgn, Math.cos(a) * sgn]
    out.push({
      p: q,
      in: j === 0 ? q : add(q, mul(tng, -k)),
      out: j === n ? q : add(q, mul(tng, k)),
      smooth: j > 0 && j < n,
    })
  }
  return out
}

/* ------------------------------ 矩形 ------------------------------ */

export type RectCorner = 'tl' | 'tr' | 'br' | 'bl'
const RECT_CORNERS: readonly RectCorner[] = ['tl', 'tr', 'br', 'bl']

/** 矩形一个角的顶点与指向里面的两个轴方向（未旋转空间） */
function rectCorner(o: { x: number; y: number; w: number; h: number }, c: RectCorner): { p: XY; dir: XY } {
  const right = c === 'tr' || c === 'br'
  const bottom = c === 'br' || c === 'bl'
  return { p: [right ? o.x + o.w : o.x, bottom ? o.y + o.h : o.y], dir: [right ? -1 : 1, bottom ? -1 : 1] }
}

/** 矩形四个角的圆点（圆角的圆心，至少离角 `minMm`） */
export function rectWidgets(o: { x: number; y: number; w: number; h: number; cornerRadius?: number }, minMm: number) {
  const r = Math.min(o.cornerRadius ?? 0, Math.min(o.w, o.h) / 2)
  const d = Math.max(r, minMm)
  return RECT_CORNERS.map((c) => {
    const { p, dir } = rectCorner(o, c)
    return { corner: c, at: [p[0] + dir[0] * d, p[1] + dir[1] * d] as XY }
  })
}

/** 指针（未旋转空间）→ 矩形的圆角半径：沿那个角的对角线方向投影，夹到 [0, 短边 / 2]，保留 0.1 mm */
export function rectRadiusFromPointer(o: { x: number; y: number; w: number; h: number }, c: RectCorner, q: XY): number {
  const { p, dir } = rectCorner(o, c)
  const s = ((q[0] - p[0]) * dir[0] + (q[1] - p[1]) * dir[1]) / 2
  return Math.round(Math.max(0, Math.min(s, Math.min(o.w, o.h) / 2)) * 10) / 10
}
