import type { DashStyle, ShapeKind } from '@/types/document'

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

/** 虚线间距按线宽比例换算；与后端 _dash_pattern 同一比例 */
export function dashArray(dash: DashStyle | undefined, sw: number): string | undefined {
  if (dash === 'dashed') return `${sw * 4} ${sw * 2.5}`
  if (dash === 'dotted') return `${sw * 0.01} ${sw * 2}`
  return undefined
}

/**
 * 圆角矩形的实际圆角半径（x/y 同值）；与后端 _draw_shape 的 rect 分支同一钳制。
 *
 * 后端 `shape.draw_rect(rect, radius=frac)` 的 frac 是**相对短边**的比例，
 * pymupdf 内部按 `min(w,h) * frac` 同时作为 x、y 两个方向的圆角半径 ——
 * 永远是正圆角，且上限为短边的一半。SVG `<rect>` 只写 rx 时 ry 虽然继承 rx，
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
