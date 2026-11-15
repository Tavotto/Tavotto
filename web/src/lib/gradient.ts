import type { Gradient, GradientStop } from '@/types/document'

/**
 * 渐变（ADR 0186）的唯一读取与几何出处。画布（`ShapeView` / `PathBody`）、导出预览（`ThumbShape`）、导出载荷、
 * EPS 透明判据、属性栏都只经这里；后端 `rendercore/geometry.read_gradient` / `gradient_paint` 同一条规则，
 * 两边对同一份共享向量 `tests/golden/gradient_vectors.json`。
 */

const HEX = /^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/

const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null)
const pair = (v: unknown): [number, number] | null => {
  if (!Array.isArray(v) || v.length !== 2) return null
  const a = num(v[0])
  const b = num(v[1])
  return a == null || b == null ? null : [a, b]
}
const clamp01 = (v: number) => Math.min(Math.max(v, 0), 1)

export interface ReadStop {
  offset: number
  color: string
  opacity: number
}
export type ReadGradient =
  | { kind: 'linear'; stops: ReadStop[]; from: [number, number]; to: [number, number] }
  | { kind: 'radial'; stops: ReadStop[]; center: [number, number]; radius: number }

/**
 * 读渐变字段；读不成 = null（退回纯色，不报错）：
 * - `kind` 只认 linear / radial；
 * - 丢掉读不成的色标（offset 不是有限数、颜色不是 #rgb / #rrggbb、opacity 给了却不是有限数），offset 与 opacity 钳到 [0, 1]
 *   （opacity 缺省 1），按 offset 稳定排序；剩不到两个 = null；
 * - 线性：from / to 都是有限数对且不重合；径向：center 是有限数对、radius 有限且 > 0。
 */
export function readGradient(g: unknown): ReadGradient | null {
  if (!g || typeof g !== 'object') return null
  const o = g as Record<string, unknown>
  if (o.kind !== 'linear' && o.kind !== 'radial') return null
  const stops: ReadStop[] = []
  for (const st of Array.isArray(o.stops) ? o.stops : []) {
    if (!st || typeof st !== 'object') continue
    const s = st as Record<string, unknown>
    const off = num(s.offset)
    if (off == null || typeof s.color !== 'string' || !HEX.test(s.color)) continue
    const op = s.opacity == null ? 1 : num(s.opacity)
    if (op == null) continue
    stops.push({ offset: clamp01(off), color: s.color, opacity: clamp01(op) })
  }
  // Array.prototype.sort 是稳定的（ES2019）：与 Python 的 sorted 同一个结果
  stops.sort((a, b) => a.offset - b.offset)
  if (stops.length < 2) return null
  if (o.kind === 'linear') {
    const a = pair(o.from)
    const b = pair(o.to)
    if (!a || !b || (a[0] === b[0] && a[1] === b[1])) return null
    return { kind: 'linear', stops, from: a, to: b }
  }
  const c = pair(o.center)
  const r = num(o.radius)
  if (!c || r == null || r <= 0) return null
  return { kind: 'radial', stops, center: c, radius: r }
}

/** 框空间里画得出来的渐变（单位与 `w` / `h` 相同）。色标 alpha = opacity × `alpha`（对象的填充 / 描边不透明度） */
export type GradientGeometry =
  | { kind: 'linear'; stops: ReadStop[]; x1: number; y1: number; x2: number; y2: number }
  | { kind: 'radial'; stops: ReadStop[]; cx: number; cy: number; r: number }

export function gradientGeometry(g: unknown, w: number, h: number, alpha = 1): GradientGeometry | null {
  const r = readGradient(g)
  if (!r) return null
  const stops = r.stops.map((st) => ({ ...st, opacity: st.opacity * alpha }))
  if (r.kind === 'linear') {
    return { kind: 'linear', stops, x1: r.from[0] * w, y1: r.from[1] * h, x2: r.to[0] * w, y2: r.to[1] * h }
  }
  return { kind: 'radial', stops, cx: r.center[0] * w, cy: r.center[1] * h, r: r.radius * Math.max(w, h, 1e-6) }
}

/** 对象此刻生效的填充 / 描边渐变：有填充（`fill` 非空）/ 有描边时才算；读不成 = null */
export function effectiveFillGradient(o: { fill?: string | null; fillGradient?: Gradient }): ReadGradient | null {
  return o.fill ? readGradient(o.fillGradient) : null
}
export function effectiveStrokeGradient(o: { strokeGradient?: Gradient }, strokeOn: boolean): ReadGradient | null {
  return strokeOn ? readGradient(o.strokeGradient) : null
}

/* ------------------------------ 编辑（属性栏） ------------------------------ */

/** 新建渐变的缺省：从对象当前的颜色到白色；线性从左到右，径向从中心到长边 */
export function defaultGradient(kind: 'linear' | 'radial', color: string): Gradient {
  const stops: GradientStop[] = [
    { offset: 0, color: HEX.test(color) ? color : '#000000' },
    { offset: 1, color: '#ffffff' },
  ]
  return kind === 'linear'
    ? { kind: 'linear', stops, from: [0, 0.5], to: [1, 0.5] }
    : { kind: 'radial', stops, center: [0.5, 0.5], radius: 0.5 }
}

/** 换类型（线性 ↔ 径向）：色标留着，几何换成新类型的缺省 */
export function switchKind(g: Gradient, kind: 'linear' | 'radial'): Gradient {
  if (g.kind === kind) return g
  const base = defaultGradient(kind, '#000000')
  return { ...base, stops: g.stops.map((s) => ({ ...s })) } as Gradient
}

/**
 * 线性渐变的角度（度，0 = 从左到右，逆时针为正，与 Illustrator 同向）：按对象的**实际尺寸**（mm）算，
 * 不是比例空间里的角度——宽高不等的对象上 45° 看起来就是 45°。
 */
export function linearAngle(g: Extract<Gradient, { kind: 'linear' }>, w: number, h: number): number {
  const dx = (g.to[0] - g.from[0]) * w
  const dy = (g.to[1] - g.from[1]) * h
  const deg = (Math.atan2(-dy, dx) * 180) / Math.PI
  return Math.round(((deg % 360) + 360) % 360 * 10) / 10
}

/**
 * 按角度重放线性渐变的起点 / 终点：过框中心、沿该方向，长度正好盖住整个框（框在该方向上的投影），
 * 两端落在框的两条对边 / 两个对角上——Illustrator 拖角度时的行为。
 */
export function withLinearAngle(
  g: Extract<Gradient, { kind: 'linear' }>,
  deg: number,
  w: number,
  h: number,
): Extract<Gradient, { kind: 'linear' }> {
  const t = (deg * Math.PI) / 180
  const ux = Math.cos(t)
  const uy = -Math.sin(t)
  const half = (Math.abs(ux) * w + Math.abs(uy) * h) / 2
  const sx = w > 0 ? (ux * half) / w : 0
  const sy = h > 0 ? (uy * half) / h : 0
  // `+ 0` 把 -0 归成 0（文档里不留负零）
  const r = (v: number) => Math.round(v * 1e6) / 1e6 + 0
  return { ...g, from: [r(0.5 - sx), r(0.5 - sy)], to: [r(0.5 + sx), r(0.5 + sy)] }
}

/** 加一个色标：放在最大的空档中间，颜色取两侧的中间色 */
export function addStop(g: Gradient): Gradient {
  const stops = [...g.stops].sort((a, b) => a.offset - b.offset)
  let at = 0
  let gap = -1
  for (let i = 0; i + 1 < stops.length; i++) {
    const d = stops[i + 1].offset - stops[i].offset
    if (d > gap) {
      gap = d
      at = i
    }
  }
  const a = stops[at]
  const b = stops[at + 1] ?? a
  const mid: GradientStop = { offset: Math.round(((a.offset + b.offset) / 2) * 1000) / 1000, color: mixHex(a.color, b.color) }
  stops.splice(at + 1, 0, mid)
  return { ...g, stops }
}

/** 删一个色标（至少留两个） */
export function removeStop(g: Gradient, index: number): Gradient {
  if (g.stops.length <= 2) return g
  return { ...g, stops: g.stops.filter((_, i) => i !== index) }
}

export function updateStop(g: Gradient, index: number, patch: Partial<GradientStop>): Gradient {
  return { ...g, stops: g.stops.map((s, i) => (i === index ? clean({ ...s, ...patch }) : s)) }
}

const clean = (s: GradientStop): GradientStop => {
  const out: GradientStop = { offset: clamp01(s.offset), color: s.color }
  if (s.opacity != null && s.opacity < 1) out.opacity = clamp01(s.opacity)
  return out
}

/** 两个颜色按 `k`（0 = a、1 = b）混合，回小写 #rrggbb */
function mixHex(a: string, b: string, k = 0.5): string {
  const rgb = (h: string) => {
    const t = h.length === 4 ? h.slice(1).replace(/./g, (c) => c + c) : h.slice(1)
    return [0, 2, 4].map((i) => parseInt(t.slice(i, i + 2), 16))
  }
  if (!HEX.test(a) || !HEX.test(b)) return HEX.test(a) ? a : b
  const x = rgb(a)
  const y = rgb(b)
  return '#' + x.map((v, i) => Math.round(v + (y[i] - v) * k).toString(16).padStart(2, '0')).join('')
}

/**
 * 包围盒变了（编辑锚点、转为路径收框）时把渐变的比例坐标换到新框上，让渐变在页面上逐点不动。
 * 两个框同一个旋转角；`delta` 是新框中心相对旧框中心在**对象自己的未旋转坐标**里的位移（mm）。
 */
export function remapGradient(
  g: Gradient,
  oldBox: { w: number; h: number },
  newBox: { w: number; h: number },
  delta: [number, number],
): Gradient {
  const ow = oldBox.w || 1e-9
  const oh = oldBox.h || 1e-9
  const nw = newBox.w || 1e-9
  const nh = newBox.h || 1e-9
  const map = (p: [number, number]): [number, number] => [
    ((p[0] - 0.5) * ow - delta[0]) / nw + 0.5,
    ((p[1] - 0.5) * oh - delta[1]) / nh + 0.5,
  ]
  if (g.kind === 'linear') return { ...g, from: map(g.from), to: map(g.to) }
  return { ...g, center: map(g.center), radius: (g.radius * Math.max(ow, oh)) / Math.max(nw, nh) }
}

/**
 * 对象的包围盒换了（同一个旋转角）：把它身上的填充 / 描边渐变一起换到新框上，画面上的渐变逐点不动。
 * 没有渐变就原样返回同一个对象。
 */
export function remapGradients<T extends { fillGradient?: Gradient; strokeGradient?: Gradient }>(
  target: T,
  from: { x: number; y: number; w: number; h: number },
  to: { x: number; y: number; w: number; h: number },
  rotationDeg: number,
): T {
  if (!target.fillGradient && !target.strokeGradient) return target
  const dx = to.x + to.w / 2 - (from.x + from.w / 2)
  const dy = to.y + to.h / 2 - (from.y + from.h / 2)
  const t = (-rotationDeg * Math.PI) / 180
  const delta: [number, number] = [dx * Math.cos(t) - dy * Math.sin(t), dx * Math.sin(t) + dy * Math.cos(t)]
  const out = { ...target }
  if (target.fillGradient) out.fillGradient = remapGradient(target.fillGradient, from, to, delta)
  if (target.strokeGradient) out.strokeGradient = remapGradient(target.strokeGradient, from, to, delta)
  return out
}

/* ------------------------------ 色带编辑（属性栏的横向色带） ------------------------------ */

/** 色标按位置稳定排序（色带上从左到右的次序；编辑一律在这个次序上做，写回去也是排好的） */
export function sortedStops(g: Gradient): GradientStop[] {
  return g.stops.map((s, i) => ({ s, i })).sort((a, b) => a.s.offset - b.s.offset || a.i - b.i).map((x) => x.s)
}

/** 色带上 `t` 处的颜色与不透明度（相邻两个色标之间线性插值，两端外面延续首尾色）——点色带加节点时取它 */
export function colorAt(g: Gradient, t: number): { color: string; opacity: number } {
  const st = sortedStops(g)
  const op = (s: GradientStop) => s.opacity ?? 1
  if (t <= st[0].offset) return { color: st[0].color, opacity: op(st[0]) }
  const last = st[st.length - 1]
  if (t >= last.offset) return { color: last.color, opacity: op(last) }
  for (let i = 0; i + 1 < st.length; i++) {
    const a = st[i]
    const b = st[i + 1]
    if (t >= a.offset && t <= b.offset) {
      const k = b.offset > a.offset ? (t - a.offset) / (b.offset - a.offset) : 0
      return { color: mixHex(a.color, b.color, k), opacity: Math.round((op(a) + (op(b) - op(a)) * k) * 1000) / 1000 }
    }
  }
  return { color: last.color, opacity: op(last) }
}

const round3 = (v: number) => Math.round(v * 1000) / 1000

/** 在 `t` 处加一个色标（颜色取插值色）；回新渐变与新色标在排序后的下标 */
export function insertStopAt(g: Gradient, t: number): { g: Gradient; index: number } {
  const at = round3(clamp01(t))
  const { color, opacity } = colorAt(g, at)
  const st = sortedStops(g)
  const index = st.filter((s) => s.offset <= at).length
  st.splice(index, 0, clean({ offset: at, color, opacity }))
  return { g: { ...g, stops: st }, index }
}

/** 把排序后第 `i` 个色标挪到 `t`：夹在左右邻居之间，不越过它们 */
export function moveStop(g: Gradient, i: number, t: number): Gradient {
  const st = sortedStops(g)
  const lo = i > 0 ? st[i - 1].offset : 0
  const hi = i + 1 < st.length ? st[i + 1].offset : 1
  st[i] = clean({ ...st[i], offset: round3(Math.min(Math.max(t, lo), hi)) })
  return { ...g, stops: st }
}

/** 反向：色标位置镜像（0 ↔ 1），次序倒过来 */
export function reverseStops(g: Gradient): Gradient {
  return { ...g, stops: sortedStops(g).reverse().map((s) => clean({ ...s, offset: round3(1 - s.offset) })) }
}

/** 色带的 CSS 预览（从左到右）：色标带透明时用 color-mix 混成透明，底下由调用方垫棋盘格 */
export function stopsCss(g: Gradient): string {
  const parts = sortedStops(g).map((s) => {
    const c = (s.opacity ?? 1) < 1 ? `color-mix(in srgb, ${s.color} ${round3((s.opacity ?? 1) * 100)}%, transparent)` : s.color
    return `${c} ${round3(s.offset * 100)}%`
  })
  return `linear-gradient(to right, ${parts.join(', ')})`
}
