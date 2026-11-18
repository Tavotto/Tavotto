import { MM_PER_PT } from './units'
import { PATH_DEFAULT_JOIN, pathGeometry, pathStrokeAlign, type PathSeg } from './pathGeom'
import { rotatePoint, type MmNode, type MmSubpath } from './pathEdit'
import { dashArray, isFilledHead, miterLimitOf, strokeCap, validDashPattern, type ArrowHeadShape } from './shapeGeometry'
import type { PathObject } from '@/types/document'

/**
 * 轮廓化描边的完整外观（ADR 0185 M7）：内核（`vecops.outline`）只会把一条居中的实线描边拓成面积；虚线、端型（箭头头）、
 * 描边内外对齐在这里先摆好几何，再交给内核拓宽、合并、裁剪——画法与导出逐条对应（`PathBody` / 后端 `geometry.path_paths`）：
 *
 * - 线杆：没有端型时是整条路径，有端型时是开放子路径两端按实心端型回缩之后的那段（`pathGeometry().stroke`）；
 * - 虚线：线杆按实际生效的虚线（`dashArray`：预设线型按线宽比例，自定义虚线是 pt 绝对值；点线是 0.01×线宽的段加圆头）
 *   沿弧长切成一段段，每段各自带线端拓宽；每条子路径从头量起（与 PDF / SVG 的虚线同一口径）；
 * - 端型：实心的（三角 / 隐形 / 菱形 / 圆点）本身就是面积，直接并进来；线状的（开口 V / 短横）是一笔描边，按线宽、线端、
 *   拐角（短横缺省尖角、开口缺省圆角）拓宽后并进来——端型从来不画虚线；
 * - 内侧 / 外侧对齐（只对全闭合的路径）：描边是两倍线宽、裁到路径里面 / 外面——拓两倍宽再与填充区域求交 / 求差。
 *
 * 这里只出页面 mm 的几何片段；拓宽、合并、裁剪在 `pathfinder.outlineStrokes` 里调内核。
 */

export type XY = [number, number]

export interface StrokeParts {
  /** 要按线宽拓宽的线杆（已切好虚线），页面 mm */
  strokes: MmSubpath[]
  /** 线杆的线端 / 拐角 / 斜接上限（实际生效的） */
  cap: 'butt' | 'round' | 'square'
  join: 'miter' | 'round' | 'bevel'
  miter: number
  /** 实心端型（面积），页面 mm */
  headFills: MmSubpath[]
  /** 线状端型：要按线宽拓宽的笔画，各带自己的拐角 */
  headStrokes: { subs: MmSubpath[]; join: 'miter' | 'round' | 'bevel' }[]
  headCap: 'butt' | 'round' | 'square'
  /** 内侧 / 外侧对齐：拓两倍宽后裁到这块填充区域里面 / 外面 */
  align: 'inside' | 'outside' | null
  /** 对齐时用的填充区域（全部子路径，页面 mm） */
  region: MmSubpath[]
}

const corner = (p: XY): MmNode => ({ p, in: p, out: p, smooth: false })

/** `pathGeometry` 的段（框空间 mm）→ 页面 mm 的子路径（旋转已算进去） */
export function segsToSubpaths(segs: readonly PathSeg[], o: PathObject): MmSubpath[] {
  const page = (x: number, y: number): XY => rotatePoint(o, [o.x + x, o.y + y])
  const out: MmSubpath[] = []
  let cur: MmSubpath | null = null
  for (const s of segs) {
    if (s[0] === 'M') {
      cur = { closed: false, nodes: [corner(page(s[1], s[2]))] }
      out.push(cur)
    } else if (s[0] === 'L' && cur) cur.nodes.push(corner(page(s[1], s[2])))
    else if (s[0] === 'C' && cur) {
      const last = cur.nodes[cur.nodes.length - 1]
      last.out = page(s[1], s[2])
      cur.nodes.push({ p: page(s[5], s[6]), in: page(s[3], s[4]), out: page(s[5], s[6]), smooth: false })
    } else if (s[0] === 'Z' && cur) {
      // 闭合：`pathGeometry` 已经补了「末 → 首」那一段，末点与首点重合——并成首点（入手柄挪过去）
      const first = cur.nodes[0]
      const last = cur.nodes[cur.nodes.length - 1]
      if (cur.nodes.length > 1 && Math.hypot(last.p[0] - first.p[0], last.p[1] - first.p[1]) < 1e-9) {
        first.in = last.in
        cur.nodes.pop()
      }
      cur.closed = true
    }
  }
  return out.filter((sp) => sp.nodes.length >= 2)
}

function headSubpaths(h: ArrowHeadShape, o: PathObject): MmSubpath[] {
  const page = (x: number, y: number): XY => rotatePoint(o, [o.x + x, o.y + y])
  if (h.kind === 'dot') {
    const k = 0.5522847498 * h.r
    const P = (x: number, y: number) => page(h.cx + x, h.cy + y)
    return [
      {
        closed: true,
        nodes: [
          { p: P(h.r, 0), in: P(h.r, -k), out: P(h.r, k), smooth: true },
          { p: P(0, h.r), in: P(k, h.r), out: P(-k, h.r), smooth: true },
          { p: P(-h.r, 0), in: P(-h.r, k), out: P(-h.r, -k), smooth: true },
          { p: P(0, -h.r), in: P(-k, -h.r), out: P(k, -h.r), smooth: true },
        ],
      },
    ]
  }
  if (isFilledHead(h)) {
    const pts = h.points
      .trim()
      .split(/\s+/)
      .map((q) => q.split(',').map(Number) as XY)
    return [{ closed: true, nodes: pts.map(([x, y]) => corner(page(x, y))) }]
  }
  // 线状端型的 d：只有 M / L（`head_strokes` / `arrowHeadShapes` 同一张表）
  const subs: MmSubpath[] = []
  const tok = h.d.match(/[ML]|-?\d*\.?\d+(?:e[-+]?\d+)?/gi) ?? []
  let cur: MmSubpath | null = null
  for (let i = 0; i < tok.length; ) {
    const c = tok[i++]
    const x = Number(tok[i++])
    const y = Number(tok[i++])
    if (c === 'M') {
      cur = { closed: false, nodes: [corner(page(x, y))] }
      subs.push(cur)
    } else if (cur) cur.nodes.push(corner(page(x, y)))
  }
  return subs.filter((sp) => sp.nodes.length >= 2)
}

/* ------------------------------------ 虚线 ------------------------------------ */

type Seg = { a: XY; b: XY; c: XY; d: XY }
const lerp = (p: XY, q: XY, t: number): XY => [p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t]
const at = (s: Seg, t: number): XY => {
  const ab = lerp(s.a, s.b, t)
  const bc = lerp(s.b, s.c, t)
  const cd = lerp(s.c, s.d, t)
  return lerp(lerp(ab, bc, t), lerp(bc, cd, t), t)
}
/** 三次段的 [t0, t1] 那一截（de Casteljau 两刀） */
function subSeg(s: Seg, t0: number, t1: number): Seg {
  const split = (q: Seg, t: number): [Seg, Seg] => {
    const ab = lerp(q.a, q.b, t)
    const bc = lerp(q.b, q.c, t)
    const cd = lerp(q.c, q.d, t)
    const abc = lerp(ab, bc, t)
    const bcd = lerp(bc, cd, t)
    const m = lerp(abc, bcd, t)
    return [
      { a: q.a, b: ab, c: abc, d: m },
      { a: m, b: bcd, c: cd, d: q.d },
    ]
  }
  const right = t0 > 0 ? split(s, t0)[1] : s
  const u = t0 >= 1 ? 1 : (t1 - t0) / (1 - t0)
  return u < 1 ? split(right, u)[0] : right
}

const SAMPLES = 64

/** 一段的弧长表（t 等分 SAMPLES 份）：(累计长度, t) */
function arcTable(s: Seg): { len: number[]; total: number } {
  const len = [0]
  let prev = s.a
  for (let k = 1; k <= SAMPLES; k++) {
    const q = at(s, k / SAMPLES)
    len.push(len[k - 1] + Math.hypot(q[0] - prev[0], q[1] - prev[1]))
    prev = q
  }
  return { len, total: len[SAMPLES] }
}
function tAt(tab: { len: number[]; total: number }, d: number): number {
  if (d <= 0) return 0
  if (d >= tab.total) return 1
  let i = 1
  while (i < SAMPLES && tab.len[i] < d) i++
  const a = tab.len[i - 1]
  const b = tab.len[i]
  return (i - 1 + (b > a ? (d - a) / (b - a) : 0)) / SAMPLES
}

/**
 * 按虚线（画 / 空交替，mm）把子路径切成一段段画出来的部分（开放子路径）。每条子路径从头量起；闭合子路径的末段接着量。
 * 全是 0 的段长（点线的「点」很短但不是 0）照样出一个点段，交给圆头拓成小圆。
 */
export function dashSubpaths(subs: readonly MmSubpath[], dash: readonly number[]): MmSubpath[] {
  const total = dash.reduce((a, b) => a + b, 0)
  if (!(total > 0)) return subs.map((sp) => ({ ...sp }))
  const out: MmSubpath[] = []
  for (const sp of subs) {
    const n = sp.nodes.length
    const segs: Seg[] = []
    for (let i = 0; i + 1 < n || (sp.closed && i < n); i++) {
      const p = sp.nodes[i]
      const q = sp.nodes[(i + 1) % n]
      segs.push({ a: p.p, b: p.out, c: q.in, d: q.p })
      if (i + 1 >= n) break
    }
    let k = 0 // 第几段虚线
    let left = dash[0] // 这一段还剩多长
    let on = true
    let cur: MmSubpath | null = null
    let first: MmSubpath | null = null // 从起点就在画的那一段（闭合路径要与末段接上）
    for (const s of segs) {
      const tab = arcTable(s)
      let pos = 0
      while (pos < tab.total - 1e-12) {
        const step = Math.min(left, tab.total - pos)
        if (on) {
          const piece = subSeg(s, tAt(tab, pos), tAt(tab, pos + step))
          if (!cur) {
            cur = { closed: false, nodes: [{ p: piece.a, in: piece.a, out: piece.b, smooth: false }] }
            out.push(cur)
            if (!first && out.length && k === 0 && pos === 0 && s === segs[0]) first = cur
          } else cur.nodes[cur.nodes.length - 1].out = piece.b
          cur.nodes.push({ p: piece.d, in: piece.c, out: piece.d, smooth: false })
        }
        pos += step
        left -= step
        if (left <= 1e-12) {
          k = (k + 1) % dash.length
          left = dash[k]
          on = !on
          if (!on) cur = null
          // 长度为 0 的「画」段（点线以外很少见）：出一个点
          while (left <= 1e-12 && on) {
            const q = at(s, tAt(tab, pos))
            out.push({ closed: false, nodes: [corner(q), corner(q)] })
            k = (k + 1) % dash.length
            left = dash[k]
            on = !on
          }
        }
      }
    }
    // 闭合路径：横跨起点的画段被切成首尾两截，接回一段（别让它们各自出笔帽、丢掉起点处的拐角）；一段盖满整圈 = 保持闭合
    if (sp.closed && first && cur && on) {
      if (cur === first) {
        const nodes = first.nodes
        if (nodes.length >= 3) {
          nodes[0].in = nodes[nodes.length - 1].in
          nodes.pop()
          first.closed = true
        }
      } else {
        cur.nodes[cur.nodes.length - 1].out = first.nodes[0].out
        cur.nodes.push(...first.nodes.slice(1))
        out.splice(out.indexOf(first), 1)
      }
    }
  }
  return out
}

/* ------------------------------------ 拆件 ------------------------------------ */

/** 一条路径的描边拆成要拓宽 / 要合并 / 要裁剪的几何（页面 mm）。没有描边 = null */
export function strokeParts(o: PathObject): StrokeParts | null {
  if (o.stroke === false || !(o.strokePt > 0)) return null
  const sw = Math.max(o.strokePt, 0.05) * MM_PER_PT
  const g = pathGeometry(o, o.w, o.h, sw)
  if (!g.fill.length) return null
  const custom = validDashPattern(o.dashPattern)
  const dashStr = dashArray(o.dash, sw, custom, MM_PER_PT)
  const dash = dashStr ? dashStr.split(' ').map(Number) : null
  let strokes = segsToSubpaths(g.heads.length ? g.stroke : g.fill, o)
  if (dash) strokes = dashSubpaths(strokes, dash)
  const align = pathStrokeAlign(o)
  const headFills: MmSubpath[] = []
  const headStrokes: StrokeParts['headStrokes'] = []
  for (const h of g.heads) {
    const subs = headSubpaths(h, o)
    if (isFilledHead(h)) headFills.push(...subs)
    else headStrokes.push({ subs, join: o.lineJoin ?? (h.kind === 'bar' ? 'miter' : 'round') })
  }
  return {
    strokes,
    cap: strokeCap(o.lineCap, custom ? undefined : o.dash),
    join: o.lineJoin ?? PATH_DEFAULT_JOIN,
    miter: miterLimitOf(o.miterLimit),
    headFills,
    headStrokes,
    headCap: strokeCap(o.lineCap, undefined),
    align,
    region: align ? segsToSubpaths(g.fill, o) : [],
  }
}
