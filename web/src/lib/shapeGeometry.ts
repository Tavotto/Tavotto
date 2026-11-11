import type {
  ArrowObject,
  DashStyle,
  HeadSize,
  LineCap,
  LineJoin,
  ShapeKind,
  ShapeObject,
} from '@/types/document'
import { arrowHeads, shapeStrokeOn } from '@/types/document'

/** 正 N 边形顶点（内切于包围盒，顶点朝上）；与后端 _polygon_points 同一公式 */
export function polygonPoints(
  sides: number,
  w: number,
  h: number,
  inset: number,
): [number, number][] {
  const n = Math.max(3, Math.min(12, Math.round(sides)))
  const rx = Math.max(w / 2 - inset, 0.001)
  const ry = Math.max(h / 2 - inset, 0.001)
  const pts: [number, number][] = []
  for (let i = 0; i < n; i++) {
    const a = -Math.PI / 2 + (i * 2 * Math.PI) / n
    pts.push([w / 2 + rx * Math.cos(a), h / 2 + ry * Math.sin(a)])
  }
  return pts
}

/**
 * 大括号「{」：右侧两钩 + 中部左尖，用二次贝塞尔；
 * 旋转（rotationDeg）覆盖其它方向。与后端 _brace_segments 同一构造。
 */
export function bracePath(w: number, h: number, inset: number): string {
  const cx = w / 2
  const tipGap = Math.min(h * 0.06, h / 2 - inset)
  return [
    `M ${w - inset},${inset}`,
    `Q ${cx},${inset} ${cx},${h * 0.25}`,
    `L ${cx},${h / 2 - tipGap}`,
    `Q ${cx},${h / 2} ${inset},${h / 2}`,
    `Q ${cx},${h / 2} ${cx},${h / 2 + tipGap}`,
    `L ${cx},${h * 0.75}`,
    `Q ${cx},${h - inset} ${w - inset},${h - inset}`,
  ].join(' ')
}

/**
 * 形状的真实轮廓（**ShapeView 的显示、命中层、选中描示三处唯一出处**）。
 *
 * 之前只有 ShapeView 知道三角形长什么样，于是覆盖层只能画包围盒——一个三角形
 * 选中时显示成矩形，用户既认不出选的是谁，还会在三个空白角上误点中它。
 *
 * 单位随调用方（ShapeView 传世界 px，覆盖层传屏幕 px），三个参数同单位即可。
 * `rect` / `line` 返回 null：矩形的包围盒就是它自己，直线走端点那套。
 */
export type ShapeOutline =
  | { kind: 'poly'; points: [number, number][] }
  | { kind: 'ellipse'; cx: number; cy: number; rx: number; ry: number }
  | { kind: 'path'; d: string }
  | null

export function shapeOutline(
  shape: ShapeKind,
  w: number,
  h: number,
  inset: number,
  sides?: number,
): ShapeOutline {
  switch (shape) {
    case 'ellipse':
      return {
        kind: 'ellipse',
        cx: w / 2,
        cy: h / 2,
        rx: Math.max(w / 2 - inset, 0.001),
        ry: Math.max(h / 2 - inset, 0.001),
      }
    case 'triangle':
      return {
        kind: 'poly',
        points: [
          [w / 2, inset],
          [w - inset, h - inset],
          [inset, h - inset],
        ],
      }
    case 'diamond':
      return {
        kind: 'poly',
        points: [
          [w / 2, inset],
          [w - inset, h / 2],
          [w / 2, h - inset],
          [inset, h / 2],
        ],
      }
    case 'polygon':
      return { kind: 'poly', points: polygonPoints(sides ?? 6, w, h, inset) }
    case 'brace':
      return { kind: 'path', d: bracePath(w, h, inset) }
    default:
      return null
  }
}

/**
 * 命中要按真实轮廓走的形状。矩形不在里面——它的包围盒就是它本身，让外层
 * div 接着管命中最省；直线另有沿线命中（`isLinear`）。
 */
export const PATH_HIT_SHAPES: ReadonlySet<ShapeKind> = new Set<ShapeKind>([
  'ellipse',
  'triangle',
  'diamond',
  'polygon',
  'brace',
])

/**
 * 线型的段长表（线宽的倍数，「画 / 空 / 画 / 空…」）；与后端 `rendercore/geometry.DASH_RATIOS` 严格同源
 * （共享向量 `tests/golden/shape_geometry_vectors.json`，ADR 0174）。
 * `dotted` 不在表里：它的「点」是 0.01×线宽 的段加圆线帽画出来的，段长另算。
 */
export const DASH_RATIOS: Readonly<Record<string, readonly number[]>> = {
  dashed: [4, 2.5],
  longdash: [8, 3],
  dashdot: [4, 2.5, 1, 2.5],
  longdashdot: [8, 3, 1, 3],
}

/** 虚线间距按线宽比例换算；与后端 dash_pattern 同一比例 */
export function dashArray(dash: DashStyle | undefined, sw: number): string | undefined {
  if (dash === 'dotted') return `${sw * 0.01} ${sw * 2}`
  const ratios = dash ? DASH_RATIOS[dash] : undefined
  return ratios ? ratios.map((r) => sw * r).join(' ') : undefined
}

/** 缺省线端：圆头（加线端 / 拐角属性之前就是这么画的） */
export const DEFAULT_LINE_CAP: LineCap = 'round'

/**
 * 实际生效的线端。`dotted` 的「点」靠圆线帽才有面积（段长只有 0.01×线宽），
 * 所以点线**强制圆头**，用户选的平头 / 方头在点线下不生效（属性栏里那一行也置灰）。
 * 与后端 `geometry.stroke_cap` 同一条规则。
 */
export function strokeCap(cap: LineCap | undefined, dash: DashStyle | undefined): LineCap {
  if (dash === 'dotted') return 'round'
  return cap === 'butt' || cap === 'square' || cap === 'round' ? cap : DEFAULT_LINE_CAP
}

/**
 * 缺省拐角：封闭形状 / 箭头是圆角；直线与大括号是尖角（后端一直是 miter，
 * 画布以前写 round——它们没有可见的拐角，两种写法画出来一样，这里把画布对齐到后端）。
 */
export function defaultLineJoin(shape?: ShapeKind): LineJoin {
  return shape === 'line' || shape === 'brace' ? 'miter' : 'round'
}

/** 尖角的斜接上限：PDF 的缺省是 10，SVG 的缺省是 4——不写死的话很尖的三角在画布上被削成斜角、导出却是尖的 */
export const STROKE_MITER_LIMIT = 10

/** 描边不透明度：缺省 / ≥1 = 不透明（返回 undefined，不写属性）；非有限数同缺省 */
export function strokeOpacityOf(v: number | undefined): number | undefined {
  if (v == null || !Number.isFinite(v) || v >= 1) return undefined
  return Math.max(0, v)
}

/**
 * 形状的描边内缩量：描边居中，内缩半个线宽让外沿正好贴合包围盒；**无边框时不内缩**，
 * 填充铺满整个包围盒（与后端 `shape_paths` 同一条）。直线 / 大括号永远有描边。
 */
export function shapeInset(o: Pick<ShapeObject, 'shape' | 'stroke'>, sw: number): number {
  return shapeStrokeOn(o) ? sw / 2 : 0
}

/**
 * 一条描边的 SVG 属性（**ShapeView / ArrowView / 缩略图 ThumbShape 共用**）。
 * `sw` 已是该处单位的线宽；`withDash` 为 false 时给箭头端型（端型不画虚线）。
 */
export function svgStroke(
  o: {
    color: string
    dash?: DashStyle
    strokeOpacity?: number
    lineCap?: LineCap
    lineJoin?: LineJoin
  },
  sw: number,
  join: LineJoin,
  withDash = true,
) {
  return {
    stroke: o.color,
    strokeWidth: sw,
    strokeDasharray: withDash ? dashArray(o.dash, sw) : undefined,
    strokeLinecap: withDash ? strokeCap(o.lineCap, o.dash) : strokeCap(o.lineCap, undefined),
    strokeLinejoin: o.lineJoin ?? join,
    strokeMiterlimit: STROKE_MITER_LIMIT,
    strokeOpacity: strokeOpacityOf(o.strokeOpacity),
  }
}

/**
 * 圆角矩形的实际圆角半径（x/y 同值）；与后端 `rendercore/geometry.py` 的 rect 分支同一钳制
 * （半径相对短边、上限为短边的一半——旧后端 pymupdf 的 `draw_rect(radius=frac)` 也是
 * 这个语义，RenderCore 照抄）。永远是正圆角。SVG `<rect>` 只写 rx 时 ry 虽然继承 rx，
 * 但 rx / ry 之后各自按半宽、半高**独立**钳制：w=60 h=6 r=4 画出来是
 * 4×3 的椭圆角（ry 撞上半高），导出却是 3×3 的正圆角。所以这里先把同一个
 * 半径算出来，两个方向都写它。
 *
 * 三个参数同单位（前端传世界 px），w/h 必须是描边内缩之后的矩形尺寸
 * ——那才是后端那个 Rect。
 */
export function cornerRadius(radius: number, w: number, h: number): number {
  return Math.min(radius, Math.min(w, h) / 2)
}

/** 细长线状对象的命中带下限（屏幕 CSS px），与 OverlaySvg 参考线的 7px 同一量级 */
export const HIT_PX = 8

/**
 * 箭头 / 直线的透明命中线宽度 —— 纯命中用，不参与渲染也不参与导出。
 *
 * 换算：世界层只有一处 CSS 变换 `scale(zoom)`（CanvasStage 的世界变换 div），
 * ArrowView / ShapeView 的 `<svg>` 不带 viewBox，所以
 * 1 SVG 用户单位 == 1 世界 px == zoom 个屏幕 CSS px。要让命中带在任何缩放下
 * 都不窄于 HIT_PX 个屏幕像素，本层宽度就得取 `HIT_PX / zoom`
 * （zoom∈[0.25,8] → 1~32 世界 px，即 0.26~8.5mm，吞不掉相邻标注）。
 * 外层 div 的 `rotate()` 是等距变换，不影响这条换算。
 * 再与可见线宽、箭头帽全宽取最大，保证「看得见的地方都点得中」。
 */
export function hitStrokeWidth(strokeW: number, zoom: number, minWidth = 0): number {
  return Math.max(strokeW, HIT_PX / Math.max(zoom, 0.01), minWidth)
}

type Pt = { x: number; y: number }

/** 端型三档的缩放系数；缺省 / `medium` = 1（加档位之前的尺寸）。与后端 `geometry.HEAD_SIZE_FACTOR` 同源 */
export const HEAD_SIZE_FACTOR: Readonly<Record<HeadSize, number>> = {
  small: 0.7,
  medium: 1,
  large: 1.5,
}

export const headSizeFactor = (size: HeadSize | undefined): number =>
  (size && HEAD_SIZE_FACTOR[size]) || 1

/**
 * 箭头的一个端型的几何：triangle / stealth / diamond = 实心多边形点串；dot = 实心圆；
 * open / bar = 描边路径。
 */
export type ArrowHeadShape =
  | { kind: 'triangle' | 'stealth' | 'diamond'; points: string }
  | { kind: 'dot'; cx: number; cy: number; r: number }
  | { kind: 'open' | 'bar'; d: string }

/** 实心端型（用描边色填充、不描边）：画布 / 缩略图一律走这一条判别 */
export const isFilledHead = (h: ArrowHeadShape): h is Exclude<ArrowHeadShape, { kind: 'open' | 'bar' }> =>
  h.kind !== 'open' && h.kind !== 'bar'

/**
 * 线段在这个端型处回缩的距离（帽长的倍数）：让杆收在端型里、不戳出尖。
 * 只有实心端型回缩（open / bar 线到尖）。与后端 `geometry.HEAD_TRIM` 同源。
 * dot 的回缩是半径（= 帽半宽，不是帽长的倍数），在 `arrowGeometry` 里单算。
 */
export const HEAD_TRIM: Readonly<Record<string, number>> = {
  triangle: 0.75,
  stealth: 0.6,
  diamond: 0.5,
}
/** 燕尾凹口离尖的距离（帽长的倍数） */
export const STEALTH_NOTCH = 0.7

/**
 * 箭头的真实几何（**ArrowView 与导出预览 `CanvasThumb` 的箭头共用的唯一出处**）。
 * 与后端 `geometry.arrow_paths` 一一对应：帽长 4×线宽、帽半宽 1.7×线宽（再乘端型大小档位）、
 * 实心端型回缩线段（`HEAD_TRIM`）。`w` / `h` 是包围盒、`sw` 是线宽，三者同单位
 * （画布传世界 px，预览传页面 mm）；结果在包围盒左上角为原点的坐标里。
 * `headLen` / `headHalf` 取两端里较大的（命中带用）。
 */
export function arrowGeometry(
  obj: Pick<
    ArrowObject,
    'start' | 'end' | 'head' | 'headStart' | 'headEnd' | 'headSizeStart' | 'headSizeEnd'
  >,
  w: number,
  h: number,
  sw: number,
): { a: Pt; b: Pt; p1: Pt; p2: Pt; headLen: number; headHalf: number; startHead: ArrowHeadShape | null; endHead: ArrowHeadShape | null } {
  const a = { x: obj.start.rx * w, y: obj.start.ry * h }
  const b = { x: obj.end.rx * w, y: obj.end.ry * h }
  const dx = b.x - a.x
  const dy = b.y - a.y
  const len = Math.hypot(dx, dy) || 1
  const ux = dx / len
  const uy = dy / len
  const nx = -uy
  const ny = ux
  const heads = arrowHeads(obj as ArrowObject)
  const fStart = headSizeFactor(obj.headSizeStart)
  const fEnd = headSizeFactor(obj.headSizeEnd)

  /** 一端：尖在 tip，`sign` = 1 终点（向后是 -u），-1 起点；返回形状与线段在这一端回缩的距离 */
  const headOf = (
    kind: string,
    tip: Pt,
    sign: 1 | -1,
    f: number,
  ): { shape: ArrowHeadShape | null; trim: number } => {
    const L = sw * 4 * f
    const H = sw * 1.7 * f
    // 沿箭杆向后 d、横向偏 s 的点
    const at = (d: number, s: number): Pt => ({
      x: tip.x - sign * ux * d + nx * s,
      y: tip.y - sign * uy * d + ny * s,
    })
    const pts = (...ps: Pt[]) => ps.map((p) => `${p.x},${p.y}`).join(' ')
    switch (kind) {
      case 'triangle':
        return {
          shape: { kind, points: pts(tip, at(L, H), at(L, -H)) },
          trim: L * HEAD_TRIM.triangle,
        }
      case 'stealth':
        return {
          shape: { kind, points: pts(tip, at(L, H), at(L * STEALTH_NOTCH, 0), at(L, -H)) },
          trim: L * HEAD_TRIM.stealth,
        }
      case 'diamond':
        return {
          shape: { kind, points: pts(tip, at(L / 2, H), at(L, 0), at(L / 2, -H)) },
          trim: L * HEAD_TRIM.diamond,
        }
      case 'dot': {
        const c = at(H, 0)
        return { shape: { kind, cx: c.x, cy: c.y, r: H }, trim: H }
      }
      case 'open': {
        const p1 = at(L, H)
        const p2 = at(L, -H)
        return {
          shape: { kind, d: `M ${p1.x},${p1.y} L ${tip.x},${tip.y} L ${p2.x},${p2.y}` },
          trim: 0,
        }
      }
      case 'bar': {
        const p1 = at(0, H)
        const p2 = at(0, -H)
        return { shape: { kind, d: `M ${p1.x},${p1.y} L ${p2.x},${p2.y}` }, trim: 0 }
      }
      default:
        return { shape: null, trim: 0 }
    }
  }

  const s = headOf(heads.start, a, -1, fStart)
  const e = headOf(heads.end, b, 1, fEnd)
  const p1 = { x: a.x + ux * s.trim, y: a.y + uy * s.trim }
  const p2 = { x: b.x - ux * e.trim, y: b.y - uy * e.trim }
  const fMax = Math.max(heads.start !== 'none' ? fStart : 1, heads.end !== 'none' ? fEnd : 1)
  return {
    a,
    b,
    p1,
    p2,
    headLen: sw * 4 * fMax,
    headHalf: sw * 1.7 * fMax,
    startHead: s.shape,
    endHead: e.shape,
  }
}
