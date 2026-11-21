import type { CanvasObject, EndPoint, Gradient, PanelObject, PathObject, ShapeObject, TextObject } from '@/types/document'
import { lineEndpoints, objectRotation, panelRotation } from '@/types/document'
import { toPathObject } from './convertToPath'
import { pathToMm, rotatePoint, toPathSubpaths, type MmSubpath, type XY } from './pathEdit'
import { fitPathBox } from './pathGeom'
import { boundsOf, visualBounds, type Rect } from './geometry'
import { clipChainOf, groupMasks } from './groupTree'
import { maskOutline } from './clipMask'
import { segsExtent } from './pathGeom'

/**
 * 变换面板（ADR 0185 M4 第二批）的**纯函数**：参考点、按参考点缩放 / 旋转 / 倾斜 / 镜像、剪切组按蒙版算的可见范围。
 * 不碰 store、不开事务（写入在 `store/transformActions`）。
 *
 * 一律只写已有字段：缩放改 x / y / w / h（「缩放描边和效果」开着时再改线宽、圆角、文字边框）；镜像改位置、取反旋转角，
 * 不对称的形状先换成等价路径再镜像锚点、面板切自己的 flipH / flipV；倾斜把路径（形状 / 箭头先换成路径）的锚点在页面上
 * 斜切后**烤进**路径。所以导出端没有新东西要认——后端照常画路径与面板，同源靠「结果仍是旧字段」+ 参考实现对拍
 * （`tests/golden/transform_vectors.json`，`scripts/dev/gen_transform_vectors.py`）。
 */

/** 九宫格参考点：上中下 × 左中右 */
export type RefPoint = 'tl' | 't' | 'tr' | 'l' | 'c' | 'r' | 'bl' | 'b' | 'br'
export const REF_POINTS: readonly RefPoint[] = ['tl', 't', 'tr', 'l', 'c', 'r', 'bl', 'b', 'br']

/** 参考点在框里的比例位置 */
export function refFrac(rp: RefPoint): XY {
  const fx = rp.endsWith('l') ? 0 : rp.endsWith('r') ? 1 : 0.5
  const fy = rp.startsWith('t') ? 0 : rp.startsWith('b') ? 1 : 0.5
  return [fx, fy]
}

/** 框上的参考点（页面 mm） */
export const refOf = (r: Rect, rp: RefPoint): XY => {
  const [fx, fy] = refFrac(rp)
  return [r.x + fx * r.w, r.y + fy * r.h]
}

const intersect = (a: Rect, b: Rect): Rect | null => {
  const x = Math.max(a.x, b.x)
  const y = Math.max(a.y, b.y)
  const x2 = Math.min(a.x + a.w, b.x + b.w)
  const y2 = Math.min(a.y + a.h, b.y + b.h)
  return x2 > x && y2 > y ? { x, y, w: x2 - x, h: y2 - y } : null
}

/** 剪切蒙版轮廓的外接框（页面 mm）；画不出轮廓 = null */
export function maskBounds(m: CanvasObject): Rect | null {
  const o = maskOutline(m)
  const e = o ? segsExtent(o.segs) : null
  return e ? { x: e.x0, y: e.y0, w: e.x1 - e.x0, h: e.y1 - e.y0 } : null
}

/**
 * 一批对象**看得见**的范围：每个对象的视觉框再与裁它的每个蒙版的外接框求交（剪切组的选框与参考点按蒙版范围算，
 * Illustrator 同一做法）。蒙版本身（隐藏的）不算；全部被裁光 = null。`all` 是找蒙版用的全集。
 */
export function visibleBounds(objs: readonly CanvasObject[], all: readonly CanvasObject[] = objs): Rect | null {
  const masks = groupMasks(all)
  const boxes: Rect[] = []
  for (const o of objs) {
    if (o.hidden) continue
    let r: Rect | null = visualBounds(o)
    for (const m of clipChainOf(o, masks)) {
      const mb = maskBounds(m)
      r = r && mb ? intersect(r, mb) : null
    }
    if (r) boxes.push(r)
  }
  return boundsOf(boxes)
}

/* ------------------------------------- 缩放 ------------------------------------- */

/** 「缩放描边和效果」：线宽、圆角、文字边框 / 内边距跟着 √(kx·ky) 走 */
function scaleStrokes<T extends CanvasObject>(o: T, k: number): T {
  const c = o as unknown as Record<string, number | undefined>
  for (const f of ['strokePt', 'cornerRadius', 'borderPt', 'padding'] as const) {
    if (typeof c[f] === 'number') c[f] = (c[f] as number) * k
  }
  return o
}

export interface ScaleOptions {
  /** 「缩放描边和效果」开关 */
  strokes: boolean
  /** 文字：`size` = 字号随 √(kx·ky) 缩（整组缩放时，Illustrator 同一做法）；`width` = 只改行长（单个文字改 W） */
  text: 'size' | 'width'
}

/**
 * 以 `ref` 为不动点缩放一个对象：框中心按 kx / ky 映过去，宽高乘 kx / ky（旋转过的对象按它自己的框缩——非等比时与
 * Illustrator 的「斜切出来」不同，见 ADR 修订）。面板保持宽高比（取 √(kx·ky)），横排文字的高由内容决定。
 */
export function scaleObject<T extends CanvasObject>(o: T, kx: number, ky: number, ref: XY, opt: ScaleOptions): T {
  const out = structuredClone(o) as T
  let sx = kx
  let sy = ky
  if (out.type === 'panel') sx = sy = Math.sqrt(Math.abs(kx * ky))
  const cx = o.x + o.w / 2
  const cy = o.y + o.h / 2
  const ncx = ref[0] + (cx - ref[0]) * kx
  const ncy = ref[1] + (cy - ref[1]) * ky
  const k = Math.sqrt(Math.abs(kx * ky))
  if (out.type === 'text') {
    const t = out as unknown as TextObject
    const vertical = t.writingMode === 'vertical'
    if (opt.text === 'size') {
      t.sizePt *= k
      if (vertical) t.h *= sy
      else t.w *= sx
    } else if (vertical) t.h *= sy
    else t.w *= sx
  } else {
    out.w *= Math.abs(sx)
    out.h *= Math.abs(sy)
  }
  out.x = ncx - out.w / 2
  out.y = ncy - out.h / 2
  if (opt.strokes) scaleStrokes(out, k)
  return out
}

/* ------------------------------------- 旋转 ------------------------------------- */

/** 以 `ref` 为圆心转 `deg`（顺时针，画布语义）：框中心绕它转，自身角度加 `deg`。面板只能整 90° 转（调用方先判） */
export function rotateObjectAbout<T extends CanvasObject>(o: T, deg: number, ref: XY): T {
  const out = structuredClone(o) as T
  const c = rot([o.x + o.w / 2, o.y + o.h / 2], ref, deg)
  out.x = c[0] - o.w / 2
  out.y = c[1] - o.h / 2
  if (out.type === 'panel') {
    const p = out as unknown as PanelObject
    const r = (((panelRotation(p) + deg) % 360) + 360) % 360
    if (r) p.rotation = r as PanelObject['rotation']
    else delete p.rotation
    // 90° 的奇数倍：落位框宽高对调（面板的框是旋转后的落位）
    if (Math.round(Math.abs(deg) / 90) % 2 === 1) {
      const { w, h } = out
      out.w = h
      out.h = w
      out.x = c[0] - out.w / 2
      out.y = c[1] - out.h / 2
    }
    return out
  }
  const next = normDeg(objectRotation(o) + deg)
  if (next) out.rotationDeg = next
  else delete out.rotationDeg
  return out
}

const normDeg = (d: number) => {
  const r = ((d % 360) + 540) % 360 - 180
  return Math.abs(r) < 1e-9 ? 0 : r
}

function rot(p: XY, c: XY, deg: number): XY {
  if (!deg) return p
  const a = (deg * Math.PI) / 180
  const dx = p[0] - c[0]
  const dy = p[1] - c[1]
  return [c[0] + dx * Math.cos(a) - dy * Math.sin(a), c[1] + dx * Math.sin(a) + dy * Math.cos(a)]
}

/* ------------------------------------- 镜像 ------------------------------------- */

export type Axis = 'h' | 'v'

/** 自身框里沿这根轴对称的形状：镜像只动位置与角度，几何不用改 */
const SYMMETRIC: Record<Axis, ReadonlySet<string>> = {
  h: new Set(['rect', 'ellipse', 'diamond', 'triangle', 'polygon', 'star']),
  v: new Set(['rect', 'ellipse', 'diamond', 'brace']),
}

const flipPt = (p: [number, number], axis: Axis): [number, number] => (axis === 'h' ? [1 - p[0], p[1]] : [p[0], 1 - p[1]])
const flipEnd = (e: EndPoint, axis: Axis): EndPoint => (axis === 'h' ? { rx: 1 - e.rx, ry: e.ry } : { rx: e.rx, ry: 1 - e.ry })

function flipGradient(g: Gradient | undefined, axis: Axis): Gradient | undefined {
  if (!g) return g
  return g.kind === 'linear'
    ? { ...g, from: flipPt(g.from, axis), to: flipPt(g.to, axis) }
    : { ...g, center: flipPt(g.center, axis) }
}

/** 路径在自己的框里镜像：锚点与手柄的比例坐标翻过来，渐变跟着翻 */
function flipPathLocal(p: PathObject, axis: Axis): PathObject {
  return {
    ...p,
    subpaths: p.subpaths.map((sp) => ({
      ...sp,
      nodes: sp.nodes.map((n) => ({
        ...n,
        p: flipPt(n.p, axis),
        ...(n.in ? { in: flipPt(n.in, axis) } : {}),
        ...(n.out ? { out: flipPt(n.out, axis) } : {}),
      })),
    })),
    ...(p.fillGradient ? { fillGradient: flipGradient(p.fillGradient, axis) } : {}),
    ...(p.strokeGradient ? { strokeGradient: flipGradient(p.strokeGradient, axis) } : {}),
  }
}

/**
 * 沿过 `ref` 的竖线（`h`，左右翻）/ 横线（`v`，上下翻）镜像。页面上的镜像 = 自身框里同一根轴的镜像 + 角度取反
 * （F·R(θ) = R(−θ)·F），所以：框关于 ref 翻过去、角度取反、再在自身框里翻——路径翻锚点、箭头 / 直线翻端点、面板切
 * flipH / flipV（面板的落位是「先翻再转」）、文字只翻位置（字不倒过来）、自身对称的形状不用动几何、不对称的先换成路径。
 */
export function reflectObject(o: CanvasObject, axis: Axis, ref: XY): CanvasObject {
  let out: CanvasObject = structuredClone(o)
  if (out.type === 'shape') {
    const s = out as ShapeObject
    if (s.shape === 'line') {
      const { start, end } = lineEndpoints(s)
      s.start = flipEnd(start, axis)
      s.end = flipEnd(end, axis)
    } else if (!SYMMETRIC[axis].has(s.shape) || s.fillGradient || s.strokeGradient) {
      const p = toPathObject(s)
      if (p) out = flipPathLocal(p, axis)
    }
  } else if (out.type === 'path') out = flipPathLocal(out as PathObject, axis)
  else if (out.type === 'arrow') {
    out.start = flipEnd(out.start, axis)
    out.end = flipEnd(out.end, axis)
  } else if (out.type === 'panel') {
    const p = out as PanelObject
    if (axis === 'h') p.flipH = p.flipH ? undefined : true
    else p.flipV = p.flipV ? undefined : true
    if (!p.flipH) delete p.flipH
    if (!p.flipV) delete p.flipV
    const r = (360 - panelRotation(p)) % 360
    if (r) p.rotation = r as PanelObject['rotation']
    else delete p.rotation
  }
  // 框关于 ref 翻过去（换成路径后框可能变了：用新框）
  if (axis === 'h') out.x = 2 * ref[0] - (out.x + out.w)
  else out.y = 2 * ref[1] - (out.y + out.h)
  if (out.type !== 'panel') {
    const d = normDeg(-objectRotation(out))
    if (d) out.rotationDeg = d
    else delete out.rotationDeg
  }
  return out
}

/* ------------------------------------- 倾斜 ------------------------------------- */

/** 能倾斜的：路径、形状、箭头（形状与箭头先换成路径）。文字与图不行 */
export const canSkew = (o: CanvasObject): boolean => o.type === 'path' || o.type === 'shape' || o.type === 'arrow'

/**
 * 水平倾斜 `deg` 度（−89…89）：以过 `ref` 的水平线为不动线，页面上 x' = x + tan(deg)·(ref.y − y)（正角 = 上边往右）。
 * 锚点先换到页面（含旋转），斜切后重新收框——结果是不带旋转角的路径（倾斜烤进几何）。换不成路径 = null。
 */
export function skewObject(o: CanvasObject, deg: number, ref: XY): PathObject | null {
  const p = o.type === 'path' ? (o as PathObject) : o.type === 'shape' || o.type === 'arrow' ? toPathObject(o) : null
  if (!p) return null
  const t = Math.tan((deg * Math.PI) / 180)
  const shear = (q: XY): XY => {
    const r = rotatePoint(p, q)
    return [r[0] + t * (ref[1] - r[1]), r[1]]
  }
  const subs: MmSubpath[] = pathToMm(p).map((sp) => ({
    closed: sp.closed,
    nodes: sp.nodes.map((n) => ({ p: shear(n.p), in: shear(n.in), out: shear(n.out), smooth: n.smooth })),
  }))
  const box = fitPathBox(toPathSubpaths(subs), 0, [0, 0])
  if (!box) return null
  const { rotationDeg: _r, ...rest } = p
  return { ...rest, x: box.x, y: box.y, w: box.w, h: box.h, subpaths: box.subpaths }
}

/* ------------------------------------ 再次变换 ------------------------------------ */

/** 一次变换（⌥⌘D 重放它）：参考点相对**当时的选区范围**，重放时按新选区的同一个参考点算 */
export type TransformOp =
  | { kind: 'move'; dx: number; dy: number }
  | { kind: 'scale'; kx: number; ky: number; rp: RefPoint }
  | { kind: 'rotate'; deg: number; rp: RefPoint }
  | { kind: 'skew'; deg: number; rp: RefPoint }
  | { kind: 'reflect'; axis: Axis; rp: RefPoint }

/**
 * 把一次变换作用到一批对象上（整体，参考点 = 这批对象看得见的范围上的那一点）。返回新对象（同 id、同顺序）；
 * 做不了的对象（倾斜文字 / 图、非 90° 转图）原样返回，`skipped` 报个数。
 */
export function applyTransform(
  objs: readonly CanvasObject[],
  op: TransformOp,
  all: readonly CanvasObject[],
  strokes: boolean,
): { objects: CanvasObject[]; skipped: number } {
  const box = visibleBounds(objs, all) ?? boundsOf(objs.map(visualBounds))
  if (!box) return { objects: [...objs], skipped: 0 }
  const ref = op.kind === 'move' ? ([0, 0] as XY) : refOf(box, op.rp)
  let skipped = 0
  const objects = objs.map((o) => {
    switch (op.kind) {
      case 'move':
        return { ...o, x: o.x + op.dx, y: o.y + op.dy }
      case 'scale':
        return scaleObject(o, op.kx, op.ky, ref, { strokes, text: 'size' })
      case 'rotate':
        if (o.type === 'panel' && op.deg % 90 !== 0) {
          skipped++
          return o
        }
        return rotateObjectAbout(o, op.deg, ref)
      case 'skew': {
        const r = canSkew(o) ? skewObject(o, op.deg, ref) : null
        if (!r) {
          skipped++
          return o
        }
        return r
      }
      case 'reflect':
        return reflectObject(o, op.axis, ref)
    }
  })
  return { objects, skipped }
}
