import type {
  ArrowHeadType,
  ArrowObject,
  CanvasObject,
  HeadSize,
  LineJoin,
  PathObject,
  ShapeObject,
} from '@/types/document'
import { arrowHeads, isStrokeOnlyShape, legacyHead, readPath, shapeStrokeOn } from '@/types/document'
import { PATH_DEFAULT_JOIN } from './pathGeom'
import { defaultLineJoin } from './shapeGeometry'

/**
 * 标注外观的**能力层**（形状 / 自由路径 / 箭头）：「这个对象有没有填充、能不能关描边、缺省拐角是什么、
 * 有没有端型、端型怎么读写」只在这里回答——属性栏、浮动栏、样式剪贴板读同一组函数，组件里不按对象类型分支
 * （与 `typography-capability-layer.md`「不许按对象类型 switch 写属性」同一条纪律）。
 *
 * 自由路径（ADR 0185）的填充 / 描边字段与 `ShapeObject` 逐字相同，所以它与形状走**同一套控件**；端型字段与
 * `ArrowObject` 同名同义（只对开放子路径生效），所以端型控件也是箭头那一组。
 */

/** 有填充 / 描边这一组字段的标注：形状与自由路径（属性栏「外观」那一套控件的对象） */
export type FillStrokeMark = ShapeObject | PathObject
/** 有描边颜色 / 线宽的标注：再加箭头（浮动栏的颜色 + 线宽） */
export type StrokedMark = FillStrokeMark | ArrowObject
/** 有端型字段的标注 */
export type HeadedMark = ArrowObject | PathObject

export const isFillStrokeMark = (o: CanvasObject): o is FillStrokeMark => o.type === 'shape' || o.type === 'path'
export const isStrokedMark = (o: CanvasObject): o is StrokedMark => isFillStrokeMark(o) || o.type === 'arrow'

/** 能不能填充：直线 / 大括号只有描边；路径都能（开放子路径按隐式闭合填，与 SVG / PDF 同一语义） */
export const markFillable = (o: FillStrokeMark): boolean =>
  o.type === 'path' || !isStrokeOnlyShape(o.shape)

/** 有没有「描边开关」（能关成无边框）：与能不能填充同一批对象 */
export const markStrokeToggle = markFillable

/** 此刻有没有描边（读取只走 `shapeStrokeOn` / `readPath`） */
export const markStrokeOn = (o: FillStrokeMark): boolean =>
  o.type === 'path' ? readPath(o).strokeOn : shapeStrokeOn(o)

/** 关描边：只对有开关的对象写 `stroke: false` */
export function setMarkStrokeOff(o: FillStrokeMark): void {
  if (markStrokeToggle(o)) o.stroke = false
}

/** 缺省拐角（等于它就不写字段） */
export const markDefaultJoin = (o: FillStrokeMark): LineJoin =>
  o.type === 'path' ? PATH_DEFAULT_JOIN : defaultLineJoin(o.shape)

/** 只有一段、没有拐角可言（拐角行不出现）：直线 */
export const markHasNoJoin = (o: FillStrokeMark): boolean => o.type === 'shape' && (o.shape === 'line' || o.shape === 'arc')

/** 形状自己的参数（圆角 / 边数）只属于对应的那一种形状 */
export const markCornerRadiusCapable = (o: FillStrokeMark): o is ShapeObject => o.type === 'shape' && o.shape === 'rect'
export const markSidesCapable = (o: FillStrokeMark): o is ShapeObject =>
  o.type === 'shape' && (o.shape === 'polygon' || o.shape === 'star')
/** 描边对齐：封闭形状（有描边开关的形状；路径的对齐要做偏移，留给 M2） */
export const markStrokeAlignCapable = (o: FillStrokeMark): o is ShapeObject => o.type === 'shape' && !isStrokeOnlyShape(o.shape)
/** 路径的描边对齐（ADR 0185 M2）：全部子路径闭合才给（开放路径没有「里外」） */
export const markPathAlignCapable = (o: FillStrokeMark): o is PathObject =>
  o.type === 'path' && o.subpaths.length > 0 && o.subpaths.every((sp) => sp.closed)
/** 对象自己的缺省对齐：形状内侧，路径居中 */
export const strokeAlignOf = (o: ShapeObject | PathObject): 'inside' | 'center' | 'outside' =>
  o.strokeAlign ?? (o.type === 'path' ? 'center' : 'inside')
/** 填充规则：有填充的路径（形状只有一条轮廓，规则不起作用） */
export const markFillRuleCapable = (o: FillStrokeMark): o is PathObject => o.type === 'path' && !!o.fill
/** 星形的内径比 */
export const markStarCapable = (o: FillStrokeMark): o is ShapeObject => o.type === 'shape' && o.shape === 'star'

/** 端型能不能起作用：箭头恒能；路径要有开放子路径（ADR 0185：端型只对开放子路径生效） */
export function markHeadCapable(o: CanvasObject): o is HeadedMark {
  if (o.type === 'arrow') return true
  if (o.type !== 'path') return false
  return readPath(o).subpaths.some((sp) => !sp.closed && sp.nodes.length >= 2)
}

/** 一组对象里端型能起作用的那几个 */
export const headedOf = (objs: readonly CanvasObject[]): HeadedMark[] => objs.filter(markHeadCapable)

/** 两端端型（箭头读旧 head 兼容推导；路径缺省 none） */
export function markHeads(o: HeadedMark): { start: ArrowHeadType; end: ArrowHeadType } {
  if (o.type === 'arrow') return arrowHeads(o)
  const r = readPath(o)
  return { start: r.headStart, end: r.headEnd }
}

/**
 * 写一端端型。箭头：两个新字段一起写、同步维护旧 `head`（规则在 `types/document.legacyHead`）；
 * 路径：没有旧字段，`none` 不落盘。
 */
export function setMarkHead(o: HeadedMark, which: 'start' | 'end', v: ArrowHeadType): void {
  const prev = markHeads(o) // 先取旧值：设了新字段后旧 head 不再参与推导
  const next = { ...prev, [which]: v }
  if (o.type === 'arrow') {
    o.headStart = next.start
    o.headEnd = next.end
    o.head = legacyHead(next)
    return
  }
  if (next.start === 'none') delete o.headStart
  else o.headStart = next.start
  if (next.end === 'none') delete o.headEnd
  else o.headEnd = next.end
}

/** 写端型大小；`medium` 不落盘 */
export function setMarkHeadSize(o: HeadedMark, which: 'start' | 'end', v: HeadSize): void {
  const key = which === 'start' ? 'headSizeStart' : 'headSizeEnd'
  if (v === 'medium') delete o[key]
  else o[key] = v
}

/**
 * 样式剪贴板（格式刷）的「族」：同族之间能互刷。形状与路径的填充 / 描边字段同名同义，是一族；
 * 其余类型各自一族。
 */
export type StyleFamily = 'panel' | 'text' | 'arrow' | 'fillStroke'
export const styleFamilyOf = (type: string): StyleFamily | null =>
  type === 'shape' || type === 'path'
    ? 'fillStroke'
    : type === 'panel' || type === 'text' || type === 'arrow'
      ? type
      : null
