import type { MmNode, MmSubpath, XY } from './pathEdit'
import { MM_PER_PT } from './units'
import type { VecopsKernel } from './vecops/kernel'

/**
 * 铅笔（ADR 0185 M8）的纯几何：一笔的原始轨迹 → 自由路径的子路径。
 *
 * - 记点：相邻两个点至少隔 `minStepMm`（屏幕上约半个像素），去掉抖动重复的点；
 * - 闭合：终点回到起点附近（`closeTolMm`，屏幕上几个像素）、而且这一笔够长（不是原地点一下）= 闭合，末尾落在闭合圈里的点丢掉；
 * - 简化：交给内核的拟合（`vecops.simplify`，与路径查找器「简化路径」同一个入口）——把折线拟合成贝塞尔，偏离原轨迹不超过
 *   容差；容差按屏幕像素换算（`tolMm`），缩放不同手感一样。内核拿不到（内嵌画布）时退回 RDP 抽稀成折线，不画不出来；
 * - 不简化（⌥）：原样的折线。
 */

export interface PencilOptions {
  /** 相邻两点最小间隔（mm） */
  minStepMm: number
  /** 终点离起点多近算闭合（mm） */
  closeTolMm: number
  /** 拟合容差（mm） */
  tolMm: number
  /** 拟合前平滑：按弧长重采样的间隔与移动平均的半窗（mm；不给 = 不平滑） */
  smoothStepMm?: number
  smoothHalfMm?: number
  /** 拟合后相邻锚点至少隔多远（mm；更近的合并；不给 = 不合并） */
  minGapMm?: number
}

const dist = (a: XY, b: XY) => Math.hypot(a[0] - b[0], a[1] - b[1])
const corner = (p: XY): MmNode => ({ p, in: p, out: p, smooth: false })

/** 记一个新点：离上一个点太近就不要（回新数组；不改入参） */
export function addPoint(pts: readonly XY[], p: XY, minStepMm: number): XY[] {
  if (pts.length && dist(pts[pts.length - 1], p) < minStepMm) return pts as XY[]
  return [...pts, p]
}

/** 一笔的总长（mm） */
export const strokeLength = (pts: readonly XY[]): number => pts.reduce((s, p, i) => (i ? s + dist(pts[i - 1], p) : 0), 0)

/** 这一笔闭不闭合：终点回到起点附近，而且一笔的长度至少是闭合圈直径的 4 倍（免得原地画个小钩也闭起来） */
export function isClosed(pts: readonly XY[], closeTolMm: number): boolean {
  return pts.length >= 3 && dist(pts[0], pts[pts.length - 1]) <= closeTolMm && strokeLength(pts) >= 8 * closeTolMm
}

/** 闭合时末尾落在起点闭合圈里的点丢掉（终点就是起点） */
export function trimClosing(pts: readonly XY[], closeTolMm: number): XY[] {
  let n = pts.length
  while (n > 3 && dist(pts[n - 1], pts[0]) <= closeTolMm) n--
  return pts.slice(0, n)
}

/* ------------------------------ 拟合前：平滑 ------------------------------ */

/** 按弧长等距重采样（首尾保留） */
export function resample(pts: readonly XY[], step: number): XY[] {
  if (pts.length < 2 || !(step > 0)) return pts.slice()
  const out: XY[] = [pts[0]]
  let carry = 0
  for (let i = 1; i < pts.length; i++) {
    const a = pts[i - 1]
    const b = pts[i]
    const seg = dist(a, b)
    if (seg <= 0) continue
    let t = 0
    while (carry + seg * (1 - t) >= step) {
      t += (step - carry) / seg
      out.push([a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t])
      carry = 0
    }
    carry += seg * (1 - t)
  }
  const last = pts[pts.length - 1]
  if (dist(out[out.length - 1], last) > 1e-9) out.push(last)
  return out
}

/** 移动平均（半窗 k 个点）。开放：首尾不动；闭合：首尾相接地平均 */
export function movingAverage(pts: readonly XY[], k: number, closed: boolean): XY[] {
  const n = pts.length
  if (k < 1 || n < 3) return pts.slice()
  return pts.map((p, i) => {
    if (!closed && (i === 0 || i === n - 1)) return p
    let sx = 0
    let sy = 0
    let c = 0
    for (let j = i - k; j <= i + k; j++) {
      const q = closed ? pts[((j % n) + n) % n] : pts[Math.min(n - 1, Math.max(0, j))]
      sx += q[0]
      sy += q[1]
      c++
    }
    return [sx / c, sy / c] as XY
  })
}

/* ------------------------------ 拟合后：极值处切出锚点、去掉多余的锚点 ------------------------------ */

type Seg = [XY, XY, XY, XY]
const bez = (s: Seg, t: number): XY => {
  const u = 1 - t
  return [
    u * u * u * s[0][0] + 3 * u * u * t * s[1][0] + 3 * u * t * t * s[2][0] + t * t * t * s[3][0],
    u * u * u * s[0][1] + 3 * u * u * t * s[1][1] + 3 * u * t * t * s[2][1] + t * t * t * s[3][1],
  ]
}
const segOf = (a: MmNode, b: MmNode): Seg => [a.p, a.out, b.in, b.p]
const unit = (v: XY): XY | null => {
  const l = Math.hypot(v[0], v[1])
  return l > 1e-12 ? [v[0] / l, v[1] / l] : null
}

/**
 * 用一段三次贝塞尔 a → c 去贴 `pts`（从 a.p 到 c.p 依次排好的点）：两端的切线方向不变（a 的出手柄、c 的入手柄方向），只用最小二乘
 * 重新定两个手柄的长度（Schneider 的 generateBezier + reparameterize）。贴不住（有点偏离 > tol）= null。
 */
export function joinSegments(a: MmNode, c: MmNode, pts: readonly XY[], tol: number): { aOut: XY; cIn: XY } | null {
  if (pts.length < 3) return null
  const t1 = unit([a.out[0] - a.p[0], a.out[1] - a.p[1]]) ?? unit([pts[1][0] - a.p[0], pts[1][1] - a.p[1]])
  const t2 = unit([c.in[0] - c.p[0], c.in[1] - c.p[1]]) ?? unit([pts[pts.length - 2][0] - c.p[0], pts[pts.length - 2][1] - c.p[1]])
  if (!t1 || !t2) return null
  const p0 = a.p
  const p3 = c.p
  const chord = dist(p0, p3)
  // 手柄长度的最小二乘（给定每个点的参数 u）
  const solve = (u: readonly number[]): Seg => {
    let c00 = 0
    let c01 = 0
    let c11 = 0
    let x0 = 0
    let x1 = 0
    u.forEach((t, i) => {
      const m = 1 - t
      const b1 = 3 * m * m * t
      const b2 = 3 * m * t * t
      const A1: XY = [t1[0] * b1, t1[1] * b1]
      const A2: XY = [t2[0] * b2, t2[1] * b2]
      c00 += A1[0] * A1[0] + A1[1] * A1[1]
      c01 += A1[0] * A2[0] + A1[1] * A2[1]
      c11 += A2[0] * A2[0] + A2[1] * A2[1]
      const base = m * m * m + b1
      const tail = b2 + t * t * t
      const r: XY = [pts[i][0] - (p0[0] * base + p3[0] * tail), pts[i][1] - (p0[1] * base + p3[1] * tail)]
      x0 += A1[0] * r[0] + A1[1] * r[1]
      x1 += A2[0] * r[0] + A2[1] * r[1]
    })
    const det = c00 * c11 - c01 * c01
    let al = Math.abs(det) > 1e-12 ? (x0 * c11 - c01 * x1) / det : chord / 3
    let ar = Math.abs(det) > 1e-12 ? (c00 * x1 - c01 * x0) / det : chord / 3
    if (!(al > 1e-6 * chord) || !(ar > 1e-6 * chord)) al = ar = chord / 3
    return [p0, [p0[0] + t1[0] * al, p0[1] + t1[1] * al], [p3[0] + t2[0] * ar, p3[1] + t2[1] * ar], p3]
  }
  // 起步用弦长参数化，再按「离新曲线最近的参数」重定几轮（Schneider 的 reparameterize）
  const cum = [0]
  for (let i = 1; i < pts.length; i++) cum.push(cum[i - 1] + dist(pts[i - 1], pts[i]))
  const total = cum[cum.length - 1]
  if (!(total > 0)) return null
  let u = cum.map((v) => v / total)
  const N = 96
  const cx = new Float64Array(N + 1)
  const cy = new Float64Array(N + 1)
  for (let iter = 0; iter < 5; iter++) {
    const seg = solve(u)
    for (let k = 0; k <= N; k++) [cx[k], cy[k]] = bez(seg, k / N)
    // 每个点到新曲线（折线近似，96 段）的最近距离（平方）与所在参数
    let worst = 0
    u = pts.map(([qx, qy]) => {
      let best = Infinity
      let bt = 0
      for (let k = 0; k < N; k++) {
        const dx = cx[k + 1] - cx[k]
        const dy = cy[k + 1] - cy[k]
        const l2 = dx * dx + dy * dy
        const f = l2 > 0 ? Math.min(1, Math.max(0, ((qx - cx[k]) * dx + (qy - cy[k]) * dy) / l2)) : 0
        const ex = qx - cx[k] - f * dx
        const ey = qy - cy[k] - f * dy
        const d2 = ex * ex + ey * ey
        if (d2 < best) {
          best = d2
          bt = (k + f) / N
        }
      }
      if (best > worst) worst = best
      return bt
    })
    if (worst <= tol * tol) return { aOut: seg[1], cIn: seg[2] }
  }
  return null
}

/**
 * 去掉中间的锚点、两段并成一段（`joinSegments`，贴不住就不并）。`pick` 给这一轮按优先级排好的候选下标；开放子路径的首尾锚点、
 * `keep` 里（按位置认）的锚点不去。每去掉一个重排一次，做到一个也去不掉为止。
 */
const posKey = (q: MmNode) => `${q.p[0]},${q.p[1]}`

/** 一段曲线上的采样点（含两端） */
const sampleSeg = (a: MmNode, b: MmNode, k = 12): XY[] => Array.from({ length: k + 1 }, (_, i) => bez(segOf(a, b), i / k))

function dropAnchors(
  sp: MmSubpath,
  tol: number,
  pick: (nodes: readonly MmNode[]) => number[],
  keep: ReadonlySet<string> = new Set(),
): MmSubpath {
  const nodes = sp.nodes.map((n) => n)
  const n0 = nodes.length
  // 每一段要贴住的点：一开始是原来那段曲线上的采样；并过之后是被并的几段原样拼起来——偏离总是对着整理前的曲线量，不会越并越歪
  const spans: XY[][] = nodes.map((q, i) => (sp.closed || i < n0 - 1 ? sampleSeg(q, nodes[(i + 1) % n0]) : []))
  const min = sp.closed ? 3 : 2
  // 并不了的三元组记下来：邻居没变就不再试（锚点一改就是新对象，自然失效）——长的一笔也是线性次数的拟合
  const ids = new Map<MmNode, number>()
  const id = (q: MmNode) => {
    let v = ids.get(q)
    if (v === undefined) ids.set(q, (v = ids.size))
    return v
  }
  const failed = new Set<string>()
  for (;;) {
    if (nodes.length <= min) break
    const n = nodes.length
    let done = false
    for (const k of pick(nodes)) {
      if ((!sp.closed && (k === 0 || k === n - 1)) || keep.has(posKey(nodes[k]))) continue
      const ia = (k - 1 + n) % n
      const ic = (k + 1) % n
      const key = `${id(nodes[ia])}|${id(nodes[k])}|${id(nodes[ic])}`
      if (failed.has(key)) continue
      const pts = [...spans[ia], ...spans[k].slice(1)]
      const j = joinSegments(nodes[ia], nodes[ic], pts, tol)
      if (!j) {
        failed.add(key)
        continue
      }
      nodes[ia] = { ...nodes[ia], out: j.aOut, smooth: false }
      nodes[ic] = { ...nodes[ic], in: j.cIn, smooth: false }
      nodes.splice(k, 1)
      spans[ia] = pts
      spans.splice(k, 1)
      done = true
      break
    }
    if (!done) break
  }
  return { closed: sp.closed, nodes }
}

/** 三次段在哪些 t 上 x（axis = 0）或 y（axis = 1）取极值（0 < t < 1） */
function extremaT(s: Seg, axis: 0 | 1): number[] {
  const [p0, p1, p2, p3] = s.map((q) => q[axis])
  // B'(t)/3 = a(1-t)^2 + 2b·t(1-t) + c·t^2 = A t^2 + B t + C
  const a = p1 - p0
  const b = p2 - p1
  const c = p3 - p2
  const A = a - 2 * b + c
  const B = 2 * (b - a)
  const C = a
  const out: number[] = []
  if (Math.abs(A) < 1e-12) {
    if (Math.abs(B) > 1e-12) out.push(-C / B)
  } else {
    const disc = B * B - 4 * A * C
    if (disc >= 0) {
      const r = Math.sqrt(disc)
      out.push((-B + r) / (2 * A), (-B - r) / (2 * A))
    }
  }
  return out.filter((t) => t > 1e-6 && t < 1 - 1e-6)
}

/** 在 t 处把 a → b 这一段切成两段（de Casteljau）：a 的新出手柄、中间的新锚点、b 的新入手柄 */
function splitAt(a: MmNode, b: MmNode, t: number): { aOut: XY; mid: MmNode; bIn: XY } {
  const L = (p: XY, q: XY): XY => [p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t]
  const ab = L(a.p, a.out)
  const bc = L(a.out, b.in)
  const cd = L(b.in, b.p)
  const abc = L(ab, bc)
  const bcd = L(bc, cd)
  return { aOut: ab, mid: { p: L(abc, bcd), in: abc, out: bcd, smooth: true }, bIn: cd }
}

/**
 * 在每段曲线的极值处（最高 / 最低、最左 / 最右的点）切出一个锚点——只切不重拟合，曲线形状一点不变。离两端锚点不到 `minGap` 的不切，
 * 那个锚点就算峰谷上的。回切好的子路径和峰谷上的那些锚点（科研图里的峰、谷就是要拖的地方）。
 */
export function anchorExtrema(sp: MmSubpath, minGap: number): { sp: MmSubpath; extrema: Set<MmNode> } {
  const nodes = sp.nodes.map((q) => ({ ...q }))
  const n = nodes.length
  const extrema = new Set<MmNode>()
  const out: MmNode[] = []
  const segs = sp.closed ? n : n - 1
  for (let i = 0; i < n; i++) {
    let a = nodes[i]
    out.push(a)
    if (i >= segs) continue
    const b = nodes[(i + 1) % n] // 闭合路径最后一段的 b 就是 out[0]，同一个对象
    const ts = [...extremaT(segOf(a, b), 0), ...extremaT(segOf(a, b), 1)].sort((x, y) => x - y)
    let from = 0 // 已经切掉的那一截在原参数上到哪儿了
    for (const t of ts) {
      if (t - from < 1e-4) continue
      const cut = splitAt(a, b, (t - from) / (1 - from))
      // 离两端的锚点太近：不切，那个锚点就当峰谷上的锚点（留着不去）
      const near = dist(cut.mid.p, a.p) < minGap ? a : dist(cut.mid.p, b.p) < minGap ? b : null
      if (near) {
        extrema.add(near)
        continue
      }
      a.out = cut.aOut
      b.in = cut.bIn
      out.push(cut.mid)
      extrema.add(cut.mid)
      a = cut.mid
      from = t
    }
  }
  return { sp: { closed: sp.closed, nodes: out }, extrema }
}

/**
 * 拟合后的整理：① 每段的极值处（峰、谷）切出锚点，离已有锚点不到 `minGap` 的不切；② 不在极值上的锚点，挤得最近的先试，去掉后两段并成
 * 一段仍贴得住（`tol`）就去掉。结果：锚点落在峰谷上、彼此隔得开，形状偏离拟合结果不超过 `tol`。
 */
export function tidyAnchors(sp: MmSubpath, minGap: number, tol: number): MmSubpath {
  const { sp: cut, extrema } = anchorExtrema(sp, minGap)
  const keep = new Set([...extrema].map(posKey))
  return dropAnchors(cut, tol, (nodes) => {
    const n = nodes.length
    const crowd = nodes.map((q, i) => {
      const prev = sp.closed || i > 0 ? dist(q.p, nodes[(i - 1 + n) % n].p) : Infinity
      const next = sp.closed || i < n - 1 ? dist(q.p, nodes[(i + 1) % n].p) : Infinity
      return Math.min(prev, next)
    })
    return nodes.map((_, i) => i).sort((x, y) => crowd[x] - crowd[y])
  }, keep)
}

/** Ramer–Douglas–Peucker 抽稀（内核拿不到时的退路）：偏离不超过 tol */
export function rdp(pts: readonly XY[], tol: number): XY[] {
  if (pts.length <= 2) return pts.slice()
  const [a, b] = [pts[0], pts[pts.length - 1]]
  const len = dist(a, b)
  let far = -1
  let farD = -1
  for (let i = 1; i < pts.length - 1; i++) {
    const p = pts[i]
    // 到有限线段 ab 的距离（投影夹进两端）：折回去的笔画不会被当成贴着无限直线
    let d = dist(a, p)
    if (len > 0) {
      const t = Math.max(0, Math.min(1, ((p[0] - a[0]) * (b[0] - a[0]) + (p[1] - a[1]) * (b[1] - a[1])) / (len * len)))
      d = dist(p, [a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])])
    }
    if (d > farD) {
      farD = d
      far = i
    }
  }
  if (farD <= tol) return [a, b]
  return [...rdp(pts.slice(0, far + 1), tol).slice(0, -1), ...rdp(pts.slice(far), tol)]
}

const PT = 1 / MM_PER_PT

/** 内核拟合：折线 → 贝塞尔子路径（pt 编码进出，与 `pathfinder.encodeSubpaths` 同一格式）。拟合不出 = null */
export function fitWithKernel(kernel: VecopsKernel, pts: readonly XY[], closed: boolean, tolMm: number): MmSubpath | null {
  const input: number[] = [1, closed ? 1 : 0, pts.length]
  for (const [x, y] of pts) input.push(x * PT, y * PT, x * PT, y * PT, x * PT, y * PT, 0)
  const out = kernel.simplify(input, tolMm * PT)
  if (!out || out[0] < 1) return null
  let i = 1
  const c = out[i++] === 1
  const k = out[i++]
  const mm = (): XY => [out[i++] * MM_PER_PT, out[i++] * MM_PER_PT]
  const nodes: MmNode[] = []
  for (let j = 0; j < k; j++) {
    const p = mm()
    const inH = mm()
    const outH = mm()
    nodes.push({ p, in: inH, out: outH, smooth: out[i++] === 1 })
  }
  if (nodes.length < 2 || nodes.some((n) => [...n.p, ...n.in, ...n.out].some((v) => !Number.isFinite(v)))) return null
  return { closed: c, nodes }
}

/**
 * 一笔 → 子路径。`raw` = 不简化（⌥）；否则内核拟合（拿不到内核就 RDP 抽稀）。点太少 / 太短 = null（原地点一下不画东西）。
 */
/** 拟合后整理锚点能用的那份容差 */
const TIDY_SHARE = 0.4

/** 拟合前的轻度平滑：按弧长重采样、再移动平均（手抖的小锯齿抹掉，峰谷的位置不动）。没给参数 = 原样 */
export function smoothStroke(pts: readonly XY[], closed: boolean, opts: PencilOptions): XY[] {
  const step = opts.smoothStepMm
  if (!step || !opts.smoothHalfMm) return pts.slice()
  const k = Math.round(opts.smoothHalfMm / step)
  if (k < 1) return pts.slice()
  if (!closed) return movingAverage(resample(pts, step), k, false)
  // 闭合：首尾接上再重采样，最后那个和起点重合的点去掉
  const ring = resample([...pts, pts[0]], step)
  if (dist(ring[ring.length - 1], ring[0]) < step / 2) ring.pop()
  return movingAverage(ring, k, true)
}

export function pencilSubpath(
  pts: readonly XY[],
  opts: PencilOptions,
  mode: { raw: boolean; kernel: VecopsKernel | null },
): MmSubpath | null {
  if (pts.length < 2 || strokeLength(pts) < 2 * opts.minStepMm + 1e-9) return null
  const closed = isClosed(pts, opts.closeTolMm)
  const body = closed ? trimClosing(pts, opts.closeTolMm) : pts.slice()
  if (mode.raw) return { closed, nodes: body.map(corner) }
  const smooth = smoothStroke(body, closed, opts)
  if (mode.kernel) {
    // 容差分两份：拟合用大头，拟合后的整理（并锚点）用剩下的——合起来偏离不超过 tolMm
    const tidyTol = opts.minGapMm ? opts.tolMm * TIDY_SHARE : 0
    const fit = fitWithKernel(mode.kernel, smooth, closed, opts.tolMm - tidyTol)
    if (fit) return opts.minGapMm ? tidyAnchors(fit, opts.minGapMm, tidyTol) : fit
  }
  const thin = closed ? rdp([...smooth, smooth[0]], opts.tolMm).slice(0, -1) : rdp(smooth, opts.tolMm)
  return thin.length >= 2 ? { closed, nodes: thin.map(corner) } : null
}
