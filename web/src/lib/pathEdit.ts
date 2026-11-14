import type { PathNode, PathObject, PathSubpath } from '@/types/document'
import { objectRotation, readPath } from '@/types/document'
import { fitPathBox, rotateAbout } from './pathGeom'

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
  return { ...base, x: box.x, y: box.y, w: box.w, h: box.h, subpaths: box.subpaths }
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
