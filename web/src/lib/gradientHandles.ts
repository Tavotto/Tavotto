import { lock45 } from '@/lib/pathEdit'
import { rotateAbout } from '@/lib/pathGeom'
import { moveStop, sortedStops } from '@/lib/gradient'
import type { Gradient } from '@/types/document'

/**
 * 画布渐变柄的几何（ADR 0186 第二期）——**纯函数**：渐变的比例坐标 ↔ 页面 mm（旋转算进去），拖柄之后的新渐变。
 * 覆盖层（`canvas/GradientHandles`）只管画与指针；同一个渐变在属性栏的色带里改，这里读的是同一份文档，自然同步。
 *
 * 线性：起点 / 终点两个柄，色标是这条线上的点（按位置插在两端之间）。径向：中心一个柄、半径一个柄（在对象自己的
 * 水平方向上，旋转对象跟着转），色标是中心到半径柄之间的点。
 */

type XY = [number, number]
export interface Box {
  x: number
  y: number
  w: number
  h: number
  rotationDeg?: number
}

const center = (o: Box): XY => [o.x + o.w / 2, o.y + o.h / 2]
const rot = (o: Box) => o.rotationDeg ?? 0

/** 框的比例坐标 → 页面 mm（绕框中心转到对象朝向） */
export function ratioToPage(o: Box, r: readonly [number, number]): XY {
  return rotateAbout([o.x + r[0] * o.w, o.y + r[1] * o.h], center(o), rot(o))
}
/** 页面 mm → 框的比例坐标 */
export function pageToRatio(o: Box, p: readonly [number, number]): XY {
  const q = rotateAbout([p[0], p[1]], center(o), -rot(o))
  return [o.w ? (q[0] - o.x) / o.w : 0, o.h ? (q[1] - o.y) / o.h : 0]
}

export interface HandleGeometry {
  kind: 'linear' | 'radial'
  /** 线性 = 起点；径向 = 中心 */
  a: XY
  /** 线性 = 终点；径向 = 半径柄 */
  b: XY
  /** 径向的半径（mm，画虚线圆用） */
  radiusMm?: number
  /** 色标在柄上的位置（按位置排序后的次序） */
  stops: { at: XY; color: string; offset: number }[]
}

export function handleGeometry(o: Box, g: Gradient): HandleGeometry {
  const lerp = (p: XY, q: XY, t: number): XY => [p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t]
  let a: XY
  let b: XY
  let radiusMm: number | undefined
  if (g.kind === 'linear') {
    a = ratioToPage(o, g.from)
    b = ratioToPage(o, g.to)
  } else {
    a = ratioToPage(o, g.center)
    radiusMm = g.radius * Math.max(o.w, o.h)
    // 半径柄在对象自己的水平方向（旋转对象跟着转）
    const local: XY = [o.x + g.center[0] * o.w + radiusMm, o.y + g.center[1] * o.h]
    b = rotateAbout(local, center(o), rot(o))
  }
  return {
    kind: g.kind,
    a,
    b,
    radiusMm,
    stops: sortedStops(g).map((s) => ({ at: lerp(a, b, s.offset), color: s.color, offset: s.offset })),
  }
}

/** 框上的吸附点（比例坐标）：四角、四边中点、中心 */
const KEY = [0, 0.5, 1]

/**
 * 拖起点 / 终点 / 中心之后的位置（页面 mm → 新渐变）：
 * - ⇧：以另一端为原点锁 45° 的倍数（页面方向，旋转对象上也按看到的方向锁）；
 * - 吸附：x、y 各自吸到框的 0 / ½ / 1（容差 `tolMm`），吸到哪个回在 `snapped` 里给覆盖层画提示。
 */
export function dragEndpoint(
  o: Box,
  g: Gradient,
  which: 'a' | 'b',
  page: XY,
  opts: { shift?: boolean; tolMm?: number } = {},
): { g: Gradient; snapped: boolean } {
  const geo = handleGeometry(o, g)
  let p: XY = page
  if (opts.shift && !(g.kind === 'radial' && which === 'a')) p = lock45(which === 'a' ? geo.b : geo.a, p)
  let r = pageToRatio(o, p)
  let snapped = false
  const tol = opts.tolMm ?? 0
  if (tol > 0 && !opts.shift) {
    const snap = (v: number, size: number) => {
      for (const k of KEY) if (Math.abs(v - k) * size <= tol) return { v: k, hit: true }
      return { v, hit: false }
    }
    const sx = snap(r[0], o.w)
    const sy = snap(r[1], o.h)
    r = [sx.v, sy.v]
    snapped = sx.hit || sy.hit
  }
  const round = (v: number) => Math.round(v * 1e6) / 1e6 + 0
  r = [round(r[0]), round(r[1])]
  if (g.kind === 'linear') {
    const next = which === 'a' ? { ...g, from: r } : { ...g, to: r }
    // 起点终点重合的渐变读不成（会退回纯色）：不许拖到一起
    if (next.from[0] === next.to[0] && next.from[1] === next.to[1]) return { g, snapped: false }
    return { g: next, snapped }
  }
  if (which === 'a') return { g: { ...g, center: r }, snapped }
  // 半径柄：到中心的距离 / 长边（方向不存，只取长度）
  const d = Math.hypot(p[0] - geo.a[0], p[1] - geo.a[1])
  const radius = round(Math.max(d, 0.01) / Math.max(o.w, o.h, 1e-6))
  return { g: { ...g, radius }, snapped: false }
}

/** 拖柄上的色标：指针投影到柄那条线上得到位置，夹在左右邻居之间（与色带同一个 `moveStop`） */
export function dragStop(o: Box, g: Gradient, index: number, page: XY): Gradient {
  const { a, b } = handleGeometry(o, g)
  const dx = b[0] - a[0]
  const dy = b[1] - a[1]
  const len2 = dx * dx + dy * dy
  const t = len2 > 0 ? ((page[0] - a[0]) * dx + (page[1] - a[1]) * dy) / len2 : 0
  return moveStop(g, index, t)
}
