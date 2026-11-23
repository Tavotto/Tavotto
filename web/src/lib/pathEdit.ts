import type { PathNode, PathObject, PathSubpath } from '@/types/document'
import { objectRotation, readPath } from '@/types/document'
import { fitPathBox, rotateAbout } from './pathGeom'
import { remapGradients } from './gradient'

/**
 * 自由路径的锚点编辑（ADR 0185 §五 M1）——**纯函数、无界面、无 store**：画布的直接选择、右键菜单、命令面板、
 * 快捷键调的是同一组函数（`lib/pathCommands`），将来 MCP 工具也只需要调它们。
 *
 * 坐标：编辑一律在**页面 mm、未旋转**的空间里做（锚点 = 框原点 + 比例 × 框），改完再由 `pathFromMm` 经
 * `pathGeom.fitPathBox` 把包围盒收回曲线实际范围、换算回比例坐标——旋转对象的中心随框挪了的那一点整体补回，
 * 画面上没动的锚点逐点不动（ADR 0185 §二：编辑锚点改了曲线范围时，同一次 commit 里重算包围盒）。
 */

export type XY = [number, number]

/** 编辑空间里的锚点：手柄已补齐（缺省 = 锚点本身） */
export interface MmNode {
  p: XY
  in: XY
  out: XY
  smooth: boolean
}
export interface MmSubpath {
  closed: boolean
  nodes: MmNode[]
}

/** 一个锚点的地址：第几条子路径的第几个 */
export interface AnchorRef {
  sp: number
  i: number
}
export const anchorKey = (a: AnchorRef): string => `${a.sp}:${a.i}`
export function parseAnchorKey(k: string): AnchorRef | null {
  const m = /^(\d+):(\d+)$/.exec(k)
  return m ? { sp: Number(m[1]), i: Number(m[2]) } : null
}

const same = (a: XY, b: XY) => a[0] === b[0] && a[1] === b[1]
const lerp = (a: XY, b: XY, t: number): XY => [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t]
const dist = (a: XY, b: XY) => Math.hypot(a[0] - b[0], a[1] - b[1])

/** 路径 → 编辑空间（页面 mm、未旋转） */
export function pathToMm(o: PathObject): MmSubpath[] {
  const r = readPath(o)
  const at = (q: XY): XY => [o.x + q[0] * o.w, o.y + q[1] * o.h]
  return r.subpaths.map((sp, si) => ({
    closed: sp.closed,
    nodes: sp.nodes.map((n, i) => ({
      p: at(n.p),
      in: at(n.in),
      out: at(n.out),
      smooth: o.subpaths[si]?.nodes[i]?.smooth === true,
    })),
  }))
}

/** 编辑空间 → 落盘形状（手柄与锚点重合就不写字段；`smooth` 只写 true） */
export function toPathSubpaths(subs: readonly MmSubpath[]): PathSubpath[] {
  return subs.map((sp) => ({
    closed: sp.closed,
    nodes: sp.nodes.map(
      (n): PathNode => ({
        p: n.p,
        ...(same(n.in, n.p) ? {} : { in: n.in }),
        ...(same(n.out, n.p) ? {} : { out: n.out }),
        ...(n.smooth ? { smooth: true as const } : {}),
      }),
    ),
  }))
}

/**
 * 编辑后的子路径 → 同一个对象的新版本（包围盒按曲线实际范围重算、比例坐标换算回去）。`base` 是编辑开始那一刻的
 * 对象：旋转中心取它的框中心，连续拖动每一帧都从同一个 `base` 现算，不会一帧帧累积误差。
 * 少于两个锚点的子路径丢掉；一条都不剩 → null（调用方删掉这个对象）。
 */
export function pathFromMm(base: PathObject, subs: readonly MmSubpath[]): PathObject | null {
  const kept = subs.filter((sp) => sp.nodes.length >= 2)
  if (!kept.length) return null
  const box = fitPathBox(toPathSubpaths(kept), objectRotation(base), [base.x + base.w / 2, base.y + base.h / 2])
  if (!box) return null
  const next = { ...base, x: box.x, y: box.y, w: box.w, h: box.h, subpaths: box.subpaths }
  // 渐变的比例坐标跟着新框换算（ADR 0186）：编辑锚点不让渐变在页面上挪动
  return remapGradients(next, base, next, objectRotation(base))
}

const clone = (subs: readonly MmSubpath[]): MmSubpath[] =>
  subs.map((sp) => ({ closed: sp.closed, nodes: sp.nodes.map((n) => ({ ...n, p: [...n.p], in: [...n.in], out: [...n.out] })) }))

const valid = (subs: readonly MmSubpath[], a: AnchorRef) => !!subs[a.sp]?.nodes[a.i]

/** 平移锚点（连同它两侧的手柄） */
export function moveAnchors(subs: readonly MmSubpath[], refs: readonly AnchorRef[], d: XY): MmSubpath[] {
  const out = clone(subs)
  const seen = new Set<string>()
  for (const a of refs) {
    if (!valid(out, a) || seen.has(anchorKey(a))) continue
    seen.add(anchorKey(a))
    const n = out[a.sp].nodes[a.i]
    n.p = [n.p[0] + d[0], n.p[1] + d[1]]
    n.in = [n.in[0] + d[0], n.in[1] + d[1]]
    n.out = [n.out[0] + d[0], n.out[1] + d[1]]
  }
  return out
}

/**
 * 拖一个手柄到 `pos`。平滑点：另一侧手柄保持共线（长度不变，长度为 0 时跟着镜像）；`breakSmooth`（⌥）：
 * 只动这一侧，锚点变成尖角。
 */
export function moveHandle(
  subs: readonly MmSubpath[],
  a: AnchorRef,
  which: 'in' | 'out',
  pos: XY,
  breakSmooth: boolean,
): MmSubpath[] {
  const out = clone(subs)
  if (!valid(out, a)) return out
  const n = out[a.sp].nodes[a.i]
  n[which] = pos
  const other = which === 'in' ? 'out' : 'in'
  if (breakSmooth) {
    n.smooth = false
  } else if (n.smooth) {
    const len = dist(n[other], n.p) || dist(pos, n.p)
    const d = dist(pos, n.p)
    if (d > 1e-12) n[other] = [n.p[0] - ((pos[0] - n.p[0]) / d) * len, n.p[1] - ((pos[1] - n.p[1]) / d) * len]
  }
  return out
}

/** 删锚点；少于两个锚点的子路径整条去掉（`pathFromMm` 收尾） */
export function deleteAnchors(subs: readonly MmSubpath[], refs: readonly AnchorRef[]): MmSubpath[] {
  const drop = new Set(refs.map(anchorKey))
  return clone(subs)
    .map((sp, si) => ({
      closed: sp.closed,
      nodes: sp.nodes.filter((_, i) => !drop.has(anchorKey({ sp: si, i }))),
    }))
    .map((sp) => (sp.closed && sp.nodes.length < 3 ? { ...sp, closed: false } : sp))
}

/** 第 `seg` 段（从锚点 seg 到下一个；闭合子路径的最后一段回到 0）；不存在 = null */
function segEnds(sp: MmSubpath, seg: number): [number, number] | null {
  const n = sp.nodes.length
  if (seg < 0 || n < 2) return null
  if (seg < n - 1) return [seg, seg + 1]
  if (seg === n - 1 && sp.closed) return [seg, 0]
  return null
}

/**
 * 在第 `seg` 段的参数 `t` 处加一个锚点，**曲线一点不变**（de Casteljau 分割）：直线段加一个无手柄的点，
 * 曲线段加一个平滑点，两侧手柄按分割比例收短。
 */
export function insertAnchor(subs: readonly MmSubpath[], sp: number, seg: number, t: number): MmSubpath[] {
  const out = clone(subs)
  const path = out[sp]
  const ends = path ? segEnds(path, seg) : null
  if (!path || !ends) return out
  const a = path.nodes[ends[0]]
  const b = path.nodes[ends[1]]
  let node: MmNode
  if (same(a.out, a.p) && same(b.in, b.p)) {
    const p = lerp(a.p, b.p, t)
    node = { p, in: p, out: p, smooth: false }
  } else {
    const q0 = lerp(a.p, a.out, t)
    const q1 = lerp(a.out, b.in, t)
    const q2 = lerp(b.in, b.p, t)
    const r0 = lerp(q0, q1, t)
    const r1 = lerp(q1, q2, t)
    node = { p: lerp(r0, r1, t), in: r0, out: r1, smooth: true }
    a.out = q0
    b.in = q2
  }
  path.nodes.splice(ends[0] + 1, 0, node)
  return out
}

/**
 * 「添加锚点」（Illustrator 的 +）：选了锚点 = 在每个选中锚点**后面那一段**的中点加一个；没选 = 每一段都加。
 * 返回新子路径与新加锚点的地址（界面把它们选上）。
 */
export function addAnchors(
  subs: readonly MmSubpath[],
  refs: readonly AnchorRef[],
): { subs: MmSubpath[]; added: AnchorRef[] } {
  const targets: AnchorRef[] = refs.length
    ? refs.filter((a) => valid(subs, a) && segEnds(subs[a.sp], a.i))
    : subs.flatMap((sp, si) => sp.nodes.map((_, i) => ({ sp: si, i })).filter((a) => segEnds(sp, a.i)))
  // 同一条子路径从后往前插：前面的下标不受后面插入的影响
  const sorted = [...targets].sort((x, y) => x.sp - y.sp || y.i - x.i)
  let cur = clone(subs)
  for (const a of sorted) cur = insertAnchor(cur, a.sp, a.i, 0.5)
  const added: AnchorRef[] = []
  for (const sp of new Set(sorted.map((a) => a.sp))) {
    const mine = sorted.filter((a) => a.sp === sp).map((a) => a.i).sort((x, y) => x - y)
    // 插完之后第 k 个（升序）插入点的新下标 = 原下标 + 1 + k
    mine.forEach((i, k) => added.push({ sp, i: i + 1 + k }))
  }
  return { subs: cur, added }
}

/**
 * 转换锚点（Illustrator 的 ⇧C）：选中的全是平滑点 → 都变尖角（手柄收回锚点）；否则都变平滑——手柄沿
 * 「前一个锚点 → 后一个锚点」的方向，长度取到相邻锚点距离的三分之一（开放端只看有的那一侧）。
 */
export function convertAnchors(subs: readonly MmSubpath[], refs: readonly AnchorRef[]): MmSubpath[] {
  const out = clone(subs)
  const live = refs.filter((a) => valid(out, a))
  if (!live.length) return out
  const allSmooth = live.every((a) => out[a.sp].nodes[a.i].smooth)
  for (const a of live) {
    const path = out[a.sp]
    const n = path.nodes[a.i]
    if (allSmooth) {
      n.in = [...n.p]
      n.out = [...n.p]
      n.smooth = false
      continue
    }
    const len = path.nodes.length
    const prev = a.i > 0 ? path.nodes[a.i - 1] : path.closed ? path.nodes[len - 1] : null
    const next = a.i < len - 1 ? path.nodes[a.i + 1] : path.closed ? path.nodes[0] : null
    const from = prev?.p ?? n.p
    const to = next?.p ?? n.p
    const dx = to[0] - from[0]
    const dy = to[1] - from[1]
    const d = Math.hypot(dx, dy)
    if (d < 1e-12) continue
    const ux = dx / d
    const uy = dy / d
    const li = prev ? dist(prev.p, n.p) / 3 : 0
    const lo = next ? dist(next.p, n.p) / 3 : 0
    n.in = [n.p[0] - ux * li, n.p[1] - uy * li]
    n.out = [n.p[0] + ux * lo, n.p[1] + uy * lo]
    n.smooth = true
  }
  return out
}

/** 落在矩形（页面 mm、未旋转的编辑空间）里的锚点 */
export function anchorsInRect(
  subs: readonly MmSubpath[],
  r: { x: number; y: number; w: number; h: number },
): AnchorRef[] {
  const out: AnchorRef[] = []
  subs.forEach((sp, si) =>
    sp.nodes.forEach((n, i) => {
      if (n.p[0] >= r.x && n.p[0] <= r.x + r.w && n.p[1] >= r.y && n.p[1] <= r.y + r.h) out.push({ sp: si, i })
    }),
  )
  return out
}

/** 页面坐标（已旋转）↔ 编辑空间（未旋转）：绕对象框中心转 ∓rotationDeg */
export function unrotatePoint(o: PathObject, p: XY): XY {
  return rotate(p, [o.x + o.w / 2, o.y + o.h / 2], -objectRotation(o))
}
export function rotatePoint(o: PathObject, p: XY): XY {
  return rotate(p, [o.x + o.w / 2, o.y + o.h / 2], objectRotation(o))
}
/** 页面上的一段位移换到编辑空间（只转方向） */
export function unrotateDelta(o: PathObject, d: XY): XY {
  return rotate(d, [0, 0], -objectRotation(o))
}
const rotate = (p: XY, c: XY, deg: number): XY => rotateAbout(p, c, deg)

/**
 * ⇧ 方向锁：`p` 投影到从 `origin` 出发、最近的 45° 倍数方向上（Illustrator 的钢笔 / 直接选择同一手感）。
 * 轴向分量逐位归零（cos(π/2) 是 6e-17 不是 0），锁水平 / 垂直时不留极小漂移。与 `canvas/interactions.axisLock` 同一条投影。
 */
export function lock45(origin: XY, p: XY): XY {
  const dx = p[0] - origin[0]
  const dy = p[1] - origin[1]
  if (!dx && !dy) return p
  const ang = Math.round(Math.atan2(dy, dx) / (Math.PI / 4)) * (Math.PI / 4)
  const ux = Math.abs(Math.cos(ang)) < 1e-9 ? 0 : Math.cos(ang)
  const uy = Math.abs(Math.sin(ang)) < 1e-9 ? 0 : Math.sin(ang)
  const proj = dx * ux + dy * uy
  return [origin[0] + ux * proj, origin[1] + uy * proj]
}

/**
 * 把一个点吸到候选线上（x、y 各自独立吸，容差内取最近；页面 mm）。回吸后的点与吸上的线（给覆盖层画吸附线）。
 * 候选 = 画布那一套（页面边与中线、别的对象、参考线、网格、图内元素中心线：`interactions.candidatesFor`）
 * 再加同一条路径别的锚点（`anchorSnapLines`）。
 */
export function snapXY(
  p: XY,
  cands: { xs: readonly number[]; ys: readonly number[] } | null,
  tol: number,
): { p: XY; xs: number[]; ys: number[] } {
  if (!cands) return { p, xs: [], ys: [] }
  const near = (v: number, list: readonly number[]) => {
    let best: number | null = null
    let bd = tol
    for (const c of list) {
      const d = Math.abs(v - c)
      if (d < bd) {
        bd = d
        best = c
      }
    }
    return best
  }
  const x = near(p[0], cands.xs)
  const y = near(p[1], cands.ys)
  return { p: [x ?? p[0], y ?? p[1]], xs: x != null ? [x] : [], ys: y != null ? [y] : [] }
}

/** 一条路径上锚点在页面上的位置（已旋转）作为吸附线；`skip` 里的锚点（正在拖的那些）不算 */
export function anchorSnapLines(o: PathObject, skip: ReadonlySet<string> = new Set()): { xs: number[]; ys: number[] } {
  const xs: number[] = []
  const ys: number[] = []
  pathToMm(o).forEach((sp, si) =>
    sp.nodes.forEach((n, i) => {
      if (skip.has(anchorKey({ sp: si, i }))) return
      const [x, y] = rotatePoint(o, n.p)
      xs.push(x)
      ys.push(y)
    }),
  )
  return { xs, ys }
}

/* -------------------------------------------------------------------------- */
/*  剪刀 / 连接 / 平均（ADR 0185 M1）                                          */
/* -------------------------------------------------------------------------- */

const isEnd = (sp: MmSubpath, i: number) => !sp.closed && (i === 0 || i === sp.nodes.length - 1)

/**
 * 剪刀：在选中的锚点处把路径断开。
 * - 同一条子路径上**相邻**的两个锚点都选了 = 剪掉它们之间那一段（「在线段上断开」）；
 * - 其余选中的锚点 = 在这个锚点处断开（锚点一分为二，两段各留一个；开放子路径的端点上断开等于没断）。
 * 闭合子路径断一刀变成从那个锚点起、到那个锚点止的开放子路径；再断才分成几段。
 * 回的是每条原子路径断出来的**几段**（按顺序）；没动的子路径原样一段。
 */
export function cutAtAnchors(subs: readonly MmSubpath[], refs: readonly AnchorRef[]): MmSubpath[][] {
  const keys = new Set(refs.map(anchorKey))
  return clone(subs).map((sp, si) => {
    const n = sp.nodes.length
    const on = (i: number) => keys.has(anchorKey({ sp: si, i }))
    // 要剪掉的段（段 k = 锚点 k → 下一个）与要断开的锚点
    const cutSeg = new Set<number>()
    for (let k = 0; k < n; k++) {
      const next = k + 1 < n ? k + 1 : sp.closed ? 0 : -1
      if (next >= 0 && on(k) && on(next)) cutSeg.add(k)
    }
    const inCutSeg = (i: number) => cutSeg.has(i) || cutSeg.has((i - 1 + n) % n)
    const splitAt = [...Array(n).keys()].filter((i) => on(i) && !inCutSeg(i) && !isEnd(sp, i))
    if (!cutSeg.size && !splitAt.length) return [sp]
    // 走一圈：在「剪掉的段」处断开，在「断开的锚点」处复制一份锚点再断开
    const breaksAfter = new Set<number>([...cutSeg]) // 段 k 之后断开（不画这段）
    const splitNodes = new Set(splitAt) // 锚点 i 处断开（这段收在 i、下一段从 i 起）
    const start = sp.closed ? (cutSeg.size ? ([...cutSeg][0] + 1) % n : splitAt[0]) : 0
    const order = sp.closed ? [...Array(n).keys()].map((k) => (start + k) % n) : [...Array(n).keys()]
    const pieces: MmNode[][] = []
    let cur: MmNode[] = []
    order.forEach((i, k) => {
      const node = sp.nodes[i]
      cur.push({ ...node, p: [...node.p], in: [...node.in], out: [...node.out] })
      const last = k === order.length - 1
      if (splitNodes.has(i) && k > 0) {
        pieces.push(cur)
        cur = [{ ...node, p: [...node.p], in: [...node.in], out: [...node.out] }]
      }
      if (breaksAfter.has(i) && !last) {
        pieces.push(cur)
        cur = []
      }
    })
    if (sp.closed) {
      // 闭合的回到起点：在起点断开时，最后一段要收在起点那个锚点上
      if (!cutSeg.size) cur.push({ ...sp.nodes[start], p: [...sp.nodes[start].p], in: [...sp.nodes[start].in], out: [...sp.nodes[start].out] })
    }
    if (cur.length) pieces.push(cur)
    return pieces
      .filter((nodes) => nodes.length >= 2)
      .map((nodes) => {
        // 断口处的手柄：断开的那一侧没有曲线了，收回锚点
        const a = nodes[0]
        const b = nodes[nodes.length - 1]
        a.in = [...a.p]
        b.out = [...b.p]
        return { closed: false, nodes }
      })
  })
}

/** 两个端点在编辑空间里重合（< 1e-6 mm）就合成一个锚点，否则用一段直线连上 */
const SAME_POINT_MM = 1e-6

/**
 * 连接：把 `a` 的 `aEnd` 端与 `b` 的 `bEnd` 端连起来，得到一条开放子路径（a 在前、b 在后，方向随 a）。
 * 两端重合就合成一个锚点（入手柄取 a 的、出手柄取 b 的），否则在它们之间加一段直线。
 */
export function joinSubpaths(a: MmSubpath, aEnd: 'start' | 'end', b: MmSubpath, bEnd: 'start' | 'end'): MmSubpath {
  const rev = (sp: MmSubpath): MmNode[] =>
    sp.nodes
      .slice()
      .reverse()
      .map((m) => ({ ...m, in: [...m.out] as XY, out: [...m.in] as XY }))
  const head = aEnd === 'end' ? clone([a])[0].nodes : rev(a)
  const tail = bEnd === 'start' ? clone([b])[0].nodes : rev(b)
  const x = head[head.length - 1]
  const y = tail[0]
  if (Math.hypot(x.p[0] - y.p[0], x.p[1] - y.p[1]) < SAME_POINT_MM) {
    head[head.length - 1] = { p: x.p, in: x.in, out: y.out, smooth: false }
    return { closed: false, nodes: [...head, ...tail.slice(1)] }
  }
  // 不重合：接上的是一段直线，两个端点上没用到的手柄（钢笔拖出来的）收回锚点，免得直线被拉弯
  head[head.length - 1] = { ...x, out: [...x.p] as XY, smooth: false }
  tail[0] = { ...y, in: [...y.p] as XY, smooth: false }
  return { closed: false, nodes: [...head, ...tail] }
}

/** 闭合一条开放子路径（连接它自己的两端）：首尾重合就合成一个锚点 */
export function closeSubpath(sp: MmSubpath): MmSubpath {
  const nodes = clone([sp])[0].nodes
  const a = nodes[0]
  const z = nodes[nodes.length - 1]
  if (nodes.length > 2 && Math.hypot(a.p[0] - z.p[0], a.p[1] - z.p[1]) < SAME_POINT_MM) {
    nodes[0] = { p: a.p, in: z.in, out: a.out, smooth: false }
    nodes.pop()
  }
  return { closed: true, nodes }
}

/** 一个锚点是不是开放子路径的端点；是的话在哪一端 */
export function endOf(subs: readonly MmSubpath[], a: AnchorRef): 'start' | 'end' | null {
  const sp = subs[a.sp]
  if (!sp || sp.closed || sp.nodes.length < 2) return null
  return a.i === 0 ? 'start' : a.i === sp.nodes.length - 1 ? 'end' : null
}

/** 平均：选中的锚点挪到它们的平均位置（两侧手柄跟着走），横竖两个方向都平均 */
export function averageAnchors(subs: readonly MmSubpath[], refs: readonly AnchorRef[]): MmSubpath[] {
  const live = refs.filter((a) => valid(subs, a))
  if (live.length < 2) return clone(subs)
  let sx = 0
  let sy = 0
  for (const a of live) {
    sx += subs[a.sp].nodes[a.i].p[0]
    sy += subs[a.sp].nodes[a.i].p[1]
  }
  const c: XY = [sx / live.length, sy / live.length]
  let out = clone(subs)
  for (const a of live) {
    const p = out[a.sp].nodes[a.i].p
    out = moveAnchors(out, [a], [c[0] - p[0], c[1] - p[1]])
  }
  return out
}
