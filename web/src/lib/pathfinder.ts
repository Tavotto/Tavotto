import { newId } from '@/lib/id'
import { canConvertToPath, toPathObject } from '@/lib/convertToPath'
import { pathFromMm, pathToMm, rotatePoint, type MmSubpath, type XY } from '@/lib/pathEdit'
import { pathStrokeAlign } from '@/lib/pathGeom'
import { MM_PER_PT } from '@/lib/units'
import type { VecopsKernel } from '@/lib/vecops/kernel'
import { isStrokeOnlyShape, type CanvasObject, type PathObject } from '@/types/document'

/**
 * 路径查找器、复合路径、偏移路径、轮廓化描边（ADR 0185 §五 M2）——**纯函数**：输入文档里的对象（从后往前），
 * 输出要换上去的新对象。几何在 WASM 内核（`lib/vecops/kernel`）；这里只做坐标换算与「结果沿用谁的样式、谁的 id、
 * 放在数组哪一位」这几条规则。事务（一次 commit、选中结果）在 `store/pathfinderActions`。
 *
 * 坐标：对象各自的比例坐标 → 页面 mm（**旋转已经算进去**，结果都是不旋转的对象）→ pt 交给内核（内核的精度
 * 0.01 是文档单位，用 pt 就是 0.01 pt），结果再换回来。
 *
 * 规则（与 Illustrator 一致的地方照 Illustrator，不一致的写明）：
 * - 能参与的：自由路径，以及封闭形状（矩形、椭圆、三角形、菱形、多边形、星形）——它们先按「转为路径」的同一份
 *   几何换成路径。直线、弧线、大括号、箭头只有描边、围不出面积，不参与；
 * - 合并 / 相交 / 排除重叠沿用**最前面**那个的样式，减去顶层沿用**最后面**那个的，分割 / 修边每一块沿用盖住它的
 *   最前面那个的（内核按 key 告诉我们）；
 * - 每个原对象的样式第一次被沿用时，那块结果**继承它的 id 与名字**（版式里指着它的东西不会断），其余的结果是新 id；
 * - 原对象全部删掉，结果放在**最上面那个原对象**原来的数组位置上（结果之间按内核给的从后往前排）；
 * - 原对象都在同一个组里，结果留在那个组；否则不进组。
 */

/** 内核的 mm → pt（反过来用 `MM_PER_PT`） */
const PT_PER_MM = 1 / MM_PER_PT

export type PathfinderOp = 'unite' | 'minusFront' | 'intersect' | 'exclude' | 'divide' | 'trim' | 'merge' | 'crop' | 'minusBack'

/** 与 `vecops/src/lib.rs` 的 `pathfinder_op` 同一张表 */
const OP_CODE: Record<PathfinderOp, number> = {
  unite: 0,
  minusFront: 1,
  intersect: 2,
  exclude: 3,
  divide: 4,
  trim: 5,
  merge: 6,
  crop: 7,
  minusBack: 8,
}

/** 界面上露出来的六个（面板一行图标的顺序）；合并同色 / 裁剪 / 减去后方内核支持，暂不进界面 */
export const PATHFINDER_UI_OPS: readonly PathfinderOp[] = ['unite', 'minusFront', 'intersect', 'exclude', 'divide', 'trim']

/** 能参与路径查找器 / 复合路径的对象（锁定、隐藏的不算） */
export function isPathfinderOperand(o: CanvasObject): boolean {
  if (o.locked || o.hidden) return false
  if (o.type === 'path') return true
  return o.type === 'shape' && canConvertToPath(o) && !isStrokeOnlyShape(o.shape)
}

/** 参与运算的对象 → 路径（形状按「转为路径」那份几何换） */
export function asPath(o: CanvasObject): PathObject | null {
  if (o.type === 'path') return o
  if (o.type === 'shape' && canConvertToPath(o)) {
    const p = toPathObject(o)
    if (!p) return null
    // 「转为路径」不带的第二批描边外观，结果对象要接着用
    return {
      ...p,
      ...(o.miterLimit != null ? { miterLimit: o.miterLimit } : {}),
      ...(o.dashPattern?.length ? { dashPattern: o.dashPattern } : {}),
    }
  }
  return null
}

/** 路径 → 页面 mm、旋转已算进去的子路径 */
export function worldSubpaths(o: PathObject): MmSubpath[] {
  const rot = (q: XY): XY => rotatePoint(o, q)
  return pathToMm(o).map((sp) => ({
    closed: sp.closed,
    nodes: sp.nodes.map((n) => ({ p: rot(n.p), in: rot(n.in), out: rot(n.out), smooth: n.smooth })),
  }))
}

/** 子路径 → 内核编码（pt） */
export function encodeSubpaths(subs: readonly MmSubpath[]): number[] {
  const o: number[] = [subs.length]
  const pt = (q: XY) => [q[0] * PT_PER_MM, q[1] * PT_PER_MM]
  for (const sp of subs) {
    o.push(sp.closed ? 1 : 0, sp.nodes.length)
    for (const n of sp.nodes) o.push(...pt(n.p), ...pt(n.in), ...pt(n.out), n.smooth ? 1 : 0)
  }
  return o
}

/** 从 `at` 起读一条编码过的路径（pt → mm）；回读到的子路径与下一个位置。格式不对 = null */
export function decodeSubpaths(b: readonly number[], at = 0): { subs: MmSubpath[]; next: number } | null {
  let i = at
  const get = () => (i < b.length ? b[i++] : NaN)
  const mm = (): XY => [get() * MM_PER_PT, get() * MM_PER_PT]
  const ns = get()
  if (!Number.isInteger(ns) || ns < 0) return null
  const subs: MmSubpath[] = []
  for (let s = 0; s < ns; s++) {
    const closed = get() === 1
    const k = get()
    if (!Number.isInteger(k) || k < 0) return null
    const nodes = []
    for (let j = 0; j < k; j++) {
      const p = mm()
      const inH = mm()
      const outH = mm()
      nodes.push({ p, in: inH, out: outH, smooth: get() === 1 })
    }
    subs.push({ closed, nodes })
  }
  if (i > b.length || subs.some((sp) => sp.nodes.some((n) => [...n.p, ...n.in, ...n.out].some((v) => !Number.isFinite(v))))) return null
  return { subs, next: i }
}

/**
 * 结果对象的样式底子：沿用 `src` 的填充 / 描边 / 线型，去掉旋转、填充规则与几何（几何由 `pathFromMm` 填）。
 * 渐变（ADR 0186）先拿掉、算完再按原比例放回（`withGradients`）——结果铺满它自己的新框，`pathFromMm` 不拿假框去换算它。
 */
function styleBase(src: PathObject, groupId: string | undefined): PathObject {
  const {
    rotationDeg: _r,
    fillRule: _f,
    name: _n,
    groupId: _g,
    groupPath: _gp,
    clipMask: _cm,
    layoutPinned: _l,
    fillGradient: _fg,
    strokeGradient: _sg,
    ...rest
  } = src
  // 嵌套组（ADR 0191）：留在原对象那个组里时整条链跟着走；剪切蒙版的身份不传给结果（结果是可见的新形状）
  const path = groupId && groupId === src.groupId && src.groupPath ? { groupPath: [...src.groupPath] } : {}
  return { ...rest, ...(groupId ? { groupId, ...path } : {}) }
}
const withGradients = (made: PathObject, src: PathObject): PathObject => ({
  ...made,
  ...(src.fillGradient && made.fill ? { fillGradient: src.fillGradient } : {}),
  ...(src.strokeGradient ? { strokeGradient: src.strokeGradient } : {}),
})

/** 一组原对象共同的组（全在同一个组里才有） */
function commonGroup(objs: readonly CanvasObject[]): string | undefined {
  const g = objs[0]?.groupId
  return g && objs.every((o) => o.groupId === g) ? g : undefined
}

export interface PathfinderPlan {
  /** 要删掉的原对象 */
  remove: string[]
  /** 结果（从后往前），放在 `at`（删掉原对象之后的数组下标） */
  add: PathObject[]
  at: number
}

/**
 * 原对象（数组顺序，从后往前）→ 结果的落位方案。`results` 是 (样式来源的下标, 子路径) 从后往前。
 * 没有结果 = null（调用方提示「没有重叠的部分」之类，**不删任何东西**）。
 */
export function planReplace(
  objects: readonly CanvasObject[],
  sources: readonly PathObject[],
  results: readonly { key: number; subs: MmSubpath[] }[],
): PathfinderPlan | null {
  if (!sources.length) return null
  const group = commonGroup(sources)
  const used = new Set<number>()
  const add: PathObject[] = []
  for (const r of results) {
    const src = sources[r.key]
    if (!src) continue
    const base = styleBase(src, group)
    const made = pathFromMm({ ...base, x: 0, y: 0, w: 1, h: 1 }, r.subs)
    if (!made) continue
    const inherit = !used.has(r.key)
    used.add(r.key)
    add.push({
      ...withGradients(made, src),
      id: inherit ? src.id : newId('p'),
      ...(inherit && src.name != null ? { name: src.name } : {}),
    })
  }
  if (!add.length) return null
  const ids = new Set(sources.map((s) => s.id))
  const idx = objects.map((o, i) => (ids.has(o.id) ? i : -1)).filter((i) => i >= 0)
  const top = Math.max(...idx)
  return { remove: [...ids], add, at: top - (idx.length - 1) }
}

/** 按数组顺序挑出参与运算的对象并换成路径；任何一个换不了 = null */
export function operands(objects: readonly CanvasObject[], ids: readonly string[]): PathObject[] | null {
  const want = new Set(ids)
  const picked = objects.filter((o) => want.has(o.id))
  if (!picked.length || !picked.every(isPathfinderOperand)) return null
  const paths = picked.map(asPath)
  return paths.every((p): p is PathObject => p != null) ? paths : null
}

/** 路径查找器的一次运算 → 落位方案；内核算不出或结果是空的 = null */
export function runPathfinder(
  kernel: VecopsKernel,
  op: PathfinderOp,
  objects: readonly CanvasObject[],
  ids: readonly string[],
): PathfinderPlan | null {
  const src = operands(objects, ids)
  if (!src || src.length < 2) return null
  const input: number[] = [src.length]
  src.forEach((o, i) => input.push(o.fillRule === 'evenodd' ? 1 : 0, i, ...encodeSubpaths(worldSubpaths(o))))
  const out = kernel.pathfinder(OP_CODE[op], input)
  if (!out) return null
  const n = out[0]
  const results: { key: number; subs: MmSubpath[] }[] = []
  let i = 1
  for (let r = 0; r < n; r++) {
    const key = out[i++]
    const d = decodeSubpaths(out, i)
    if (!d) return null
    i = d.next
    results.push({ key, subs: d.subs })
  }
  return planReplace(objects, src, results)
}

/**
 * 建立复合路径（⌘8）：所有子路径并进**最后面**那个对象（沿用它的样式、id 与名字），填充规则设成奇偶——
 * 重叠的地方挖空（Illustrator 建复合路径也是沿用最后面那个的样式）。不需要内核。
 */
export function makeCompound(objects: readonly CanvasObject[], ids: readonly string[]): PathfinderPlan | null {
  const src = operands(objects, ids)
  if (!src || src.length < 2) return null
  const subs = src.flatMap(worldSubpaths)
  return withFillRule(planReplace(objects, src, [{ key: 0, subs }]), 'evenodd')
}

/** 释放复合路径（⌥⇧⌘8）：一个多子路径的路径拆成每条子路径一个对象，第一条留原 id；填充规则回到缺省 */
export function releaseCompound(objects: readonly CanvasObject[], id: string): PathfinderPlan | null {
  const o = objects.find((x) => x.id === id)
  if (!o || o.type !== 'path' || o.locked || o.hidden || o.subpaths.length < 2) return null
  const subs = worldSubpaths(o)
  return planReplace(
    objects,
    [o],
    subs.map((sp) => ({ key: 0, subs: [sp] })),
  )
}

function withFillRule(plan: PathfinderPlan | null, rule: 'evenodd'): PathfinderPlan | null {
  if (!plan) return null
  return { ...plan, add: plan.add.map((p) => ({ ...p, fillRule: rule })) }
}

/** 偏移路径的拐角：路径自己的拐角（缺省圆角）→ 内核的 0 斜接 / 1 圆角 / 2 斜角 */
const JOIN_CODE = { miter: 0, round: 1, bevel: 2 } as const
const CAP_CODE = { butt: 0, round: 1, square: 2 } as const

/**
 * 内核的偏移只认非零环绕。奇偶填充且多子路径（复合路径）的对象先过一遍内核的「合并」：单个对象的合并
 * 会按它的填充规则算出真实的填充区域，输出的洞与外轮廓反向、非零下同样成立。算不出就退回原几何。
 */
function nonzeroInput(kernel: VecopsKernel, o: PathObject): number[] {
  const enc = encodeSubpaths(worldSubpaths(o))
  if (o.fillRule !== 'evenodd' || o.subpaths.length < 2) return enc
  const out = kernel.pathfinder(OP_CODE.unite, [1, 1, 0, ...enc])
  const d = out && out[0] === 1 ? decodeSubpaths(out, 2) : null
  return d ? encodeSubpaths(d.subs) : enc
}

/**
 * 偏移路径：每个选中的对象旁边**多一个**偏移后的新对象（原对象不动），紧挨着放在它上面。
 * `deltaMm` 正 = 往外扩、负 = 往里缩。拐角跟原对象的拐角。
 */
export function offsetPaths(
  kernel: VecopsKernel,
  objects: readonly CanvasObject[],
  ids: readonly string[],
  deltaMm: number,
): { after: string; add: PathObject }[] | null {
  const src = operands(objects, ids)
  if (!src || !Number.isFinite(deltaMm) || deltaMm === 0) return null
  const out: { after: string; add: PathObject }[] = []
  for (const o of src) {
    const join = JOIN_CODE[o.lineJoin ?? 'round']
    const r = kernel.offset(nonzeroInput(kernel, o), deltaMm * PT_PER_MM, join, o.miterLimit ?? 10)
    const d = r && decodeSubpaths(r)
    if (!d) continue
    const base = styleBase(o, o.groupId)
    const made = pathFromMm({ ...base, x: 0, y: 0, w: 1, h: 1 }, d.subs)
    if (made) out.push({ after: o.id, add: { ...withGradients(made, o), id: newId('p') } })
  }
  return out.length ? out : null
}

/** 能轮廓化描边的：有描边、没有自定义 / 预设虚线、没有端型、路径没设内外对齐（这几样内核不画，转出来会和画面不一样） */
export function canOutlineStroke(o: CanvasObject): boolean {
  if (o.locked || o.hidden) return false
  if (o.type !== 'path' && !(o.type === 'shape' && canConvertToPath(o))) return false
  const p = o as { stroke?: boolean; strokePt: number; dash?: string; dashPattern?: number[] }
  if (p.stroke === false || !(p.strokePt > 0)) return false
  if ((p.dash && p.dash !== 'solid') || p.dashPattern?.length) return false
  if (o.type === 'path' && ((o.headStart && o.headStart !== 'none') || (o.headEnd && o.headEnd !== 'none'))) return false
  // 路径的内侧 / 外侧对齐是裁出来的半条两倍宽描边，内核按居中描；形状的对齐已经进了「转为路径」的几何，不受影响
  if (o.type === 'path' && pathStrokeAlign(o)) return false
  return true
}

/**
 * 轮廓化描边：描边画出来的那块面积变成一个填充对象（填充色 = 原描边色与不透明度，无描边）。
 * - 原对象没有填充：轮廓直接换掉它（同 id、同位置）；
 * - 原对象有填充：原对象留下、关掉描边，轮廓作为新对象紧挨着放在它上面（Illustrator 是编成一组，这里不成组）。
 */
export function outlineStrokes(
  kernel: VecopsKernel,
  objects: readonly CanvasObject[],
  ids: readonly string[],
): { replace: PathObject; extra: PathObject | null }[] | null {
  const want = new Set(ids)
  const picked = objects.filter((o) => want.has(o.id))
  if (!picked.length || !picked.every(canOutlineStroke)) return null
  const out: { replace: PathObject; extra: PathObject | null }[] = []
  for (const o of picked) {
    const p = asPath(o)
    if (!p) continue
    const r = kernel.outline(
      encodeSubpaths(worldSubpaths(p)),
      p.strokePt,
      CAP_CODE[p.lineCap ?? 'butt'],
      JOIN_CODE[p.lineJoin ?? 'round'],
      p.miterLimit ?? 10,
    )
    const d = r && decodeSubpaths(r)
    if (!d) continue
    const base = styleBase(p, p.groupId)
    const {
      dash: _d,
      dashPattern: _dp,
      lineCap: _c,
      lineJoin: _j,
      miterLimit: _m,
      strokeOpacity: _so,
      fillOpacity: _fo,
      ...plain
    } = base
    const made = pathFromMm(
      {
        ...plain,
        x: 0,
        y: 0,
        w: 1,
        h: 1,
        fill: p.color,
        ...(p.strokeOpacity != null ? { fillOpacity: p.strokeOpacity } : {}),
        stroke: false,
      },
      d.subs,
    )
    if (!made) continue
    // 描边渐变变成轮廓的填充渐变（ADR 0186）：比例坐标照原样，铺满轮廓的框
    const outlined: PathObject = p.strokeGradient ? { ...made, fillGradient: p.strokeGradient } : made
    if (p.fill) {
      const kept: PathObject = { ...p, stroke: false }
      out.push({ replace: kept, extra: { ...outlined, id: newId('p') } })
    } else {
      out.push({ replace: { ...outlined, id: p.id, ...(p.name != null ? { name: p.name } : {}) }, extra: null })
    }
  }
  return out.length ? out : null
}

/** 简化的容差（pt）：曲线偏离原来不超过它。Illustrator 简化面板的中档是 1 pt；科研图小，取一半 */
export const SIMPLIFY_TOLERANCE_PT = 0.5

/**
 * 简化：在容差内删掉多余的锚点（钢笔、轮廓化、偏移之后常有一串挤在一起的点）。在对象自己的未旋转空间里算，
 * id、旋转、样式都不变；锚点一个都没少的不算改动（不进结果）。
 */
export function simplifyPaths(kernel: VecopsKernel, objects: readonly CanvasObject[], ids: readonly string[]): PathObject[] | null {
  const want = new Set(ids)
  const out: PathObject[] = []
  for (const o of objects) {
    if (!want.has(o.id) || o.type !== 'path' || o.locked || o.hidden) continue
    const subs = pathToMm(o)
    const r = kernel.simplify(encodeSubpaths(subs), SIMPLIFY_TOLERANCE_PT)
    const d = r && decodeSubpaths(r)
    if (!d) continue
    const before = subs.reduce((n, sp) => n + sp.nodes.length, 0)
    const after = d.subs.reduce((n, sp) => n + sp.nodes.length, 0)
    if (after >= before) continue
    const made = pathFromMm(o, d.subs)
    if (made) out.push(made)
  }
  return out.length ? out : null
}

/**
 * 反转路径方向：子路径的锚点倒过来排，进出手柄互换，曲线逐点不变。端型字段**不跟着换**——起点 / 终点换了头，
 * 箭头就指向另一边（Illustrator 用「反转路径方向」掉转弯箭头也是这个效果）；虚线从另一头起。
 */
export function reversePath(o: PathObject): PathObject {
  return {
    ...o,
    subpaths: o.subpaths.map((sp) => ({
      closed: sp.closed,
      nodes: [...sp.nodes].reverse().map((n) => ({
        p: n.p,
        ...(n.out ? { in: n.out } : {}),
        ...(n.in ? { out: n.in } : {}),
        ...(n.smooth ? { smooth: true as const } : {}),
      })),
    })),
  }
}

/* ------------------------------ 形状生成器（⇧M） ------------------------------ */

/** 形状生成器里的一块区域：页面 mm（旋转已算进去）的子路径 + 沿用哪个原对象的样式（`sources` 的下标） */
export interface BuilderFace {
  key: number
  subs: MmSubpath[]
}

/** 内核的形状栈编码（从后往前，key = 下标） */
function encodeStack(items: readonly { rule: 0 | 1; subs: MmSubpath[] }[]): number[] {
  const input: number[] = [items.length]
  items.forEach((it, i) => input.push(it.rule, i, ...encodeSubpaths(it.subs)))
  return input
}

function decodeResults(out: readonly number[]): { key: number; subs: MmSubpath[] }[] | null {
  const results: { key: number; subs: MmSubpath[] }[] = []
  let i = 1
  for (let r = 0; r < out[0]; r++) {
    const key = out[i++]
    const d = decodeSubpaths(out, i)
    if (!d) return null
    i = d.next
    results.push({ key, subs: d.subs })
  }
  return results
}

/**
 * 进形状生成器：选中的对象按「分割」切成互不重叠的区域，每块记着盖住它的最前面那个原对象（样式与 id 都跟它）。
 * 不能参与 / 少于两个 / 内核算不出 = null。
 */
export function builderFaces(
  kernel: VecopsKernel,
  objects: readonly CanvasObject[],
  ids: readonly string[],
): { sources: PathObject[]; faces: BuilderFace[] } | null {
  const sources = operands(objects, ids)
  if (!sources || sources.length < 2) return null
  const out = kernel.pathfinder(
    OP_CODE.divide,
    encodeStack(sources.map((o) => ({ rule: o.fillRule === 'evenodd' ? 1 : 0, subs: worldSubpaths(o) }))),
  )
  const faces = out && decodeResults(out)
  return faces && faces.length ? { sources, faces } : null
}

/** 把几块区域并成一块（内核「合并」）：沿用 `faces[0]` 的样式来源 */
export function mergeFaces(kernel: VecopsKernel, faces: readonly BuilderFace[]): BuilderFace | null {
  if (!faces.length) return null
  if (faces.length === 1) return faces[0]
  const out = kernel.pathfinder(OP_CODE.unite, encodeStack(faces.map((f) => ({ rule: 0, subs: f.subs }))))
  const res = out && decodeResults(out)
  if (!res || !res.length) return null
  return { key: faces[0].key, subs: res.flatMap((r) => r.subs) }
}

/** 退出形状生成器时的落位：剩下的每块区域一个路径对象（id / 样式 / 位置规则与路径查找器相同） */
export function builderPlan(
  objects: readonly CanvasObject[],
  sources: readonly PathObject[],
  faces: readonly BuilderFace[],
): PathfinderPlan | null {
  if (!faces.length) {
    // 全删掉了：原对象都删，什么都不加
    const ids = new Set(sources.map((s) => s.id))
    const idx = objects.map((o, i) => (ids.has(o.id) ? i : -1)).filter((i) => i >= 0)
    return { remove: [...ids], add: [], at: Math.max(...idx) - (idx.length - 1) }
  }
  return planReplace(objects, sources, faces)
}
