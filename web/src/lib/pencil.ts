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

/** Ramer–Douglas–Peucker 抽稀（内核拿不到时的退路）：偏离不超过 tol */
export function rdp(pts: readonly XY[], tol: number): XY[] {
  if (pts.length <= 2) return pts.slice()
  const [a, b] = [pts[0], pts[pts.length - 1]]
  const len = dist(a, b)
  let far = -1
  let farD = -1
  for (let i = 1; i < pts.length - 1; i++) {
    const p = pts[i]
    const d = len > 0 ? Math.abs((b[0] - a[0]) * (a[1] - p[1]) - (a[0] - p[0]) * (b[1] - a[1])) / len : dist(a, p)
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
export function pencilSubpath(
  pts: readonly XY[],
  opts: PencilOptions,
  mode: { raw: boolean; kernel: VecopsKernel | null },
): MmSubpath | null {
  if (pts.length < 2 || strokeLength(pts) < 2 * opts.minStepMm + 1e-9) return null
  const closed = isClosed(pts, opts.closeTolMm)
  const body = closed ? trimClosing(pts, opts.closeTolMm) : pts.slice()
  if (mode.raw) return { closed, nodes: body.map(corner) }
  if (mode.kernel) {
    const fit = fitWithKernel(mode.kernel, body, closed, opts.tolMm)
    if (fit) return fit
  }
  const thin = closed ? rdp([...body, body[0]], opts.tolMm).slice(0, -1) : rdp(body, opts.tolMm)
  return thin.length >= 2 ? { closed, nodes: thin.map(corner) } : null
}
