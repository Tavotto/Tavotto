import type { CanvasObject, FigureDocument } from '@/types/document'
import { objectRotation } from '@/types/document'
import { ptToMm } from './units'

/**
 * 单图档退路导出（原图口径不可用、静默改按画布）的**裁切**：导出区域 = 画布上
 * 可见对象的并集包围盒，而不是整个页面——页面比图大，按整页导会四周带白边。
 *
 * 纯投影：只改「送去合成的那份请求」，不改文档、不进撤销；协议不变（页面尺寸换成
 * 包围盒尺寸，对象坐标整体平移 -x/-y，其余字段原样）。隐藏对象与导出载荷同一条
 * 规则（不发、不计入）。没有可见对象时不裁（整页）。
 */
export interface CropRect {
  x: number
  y: number
  w: number
  h: number
}

/**
 * 描边 / 箭头头部越过逻辑框的外扩量（mm）。**与 RenderCore 的绘制同源**
 * （`rendercore/geometry.py` / `plan.py`）：
 * * 矩形 / 椭圆 / 多边形等封闭形状把描边内缩半宽，不越框；
 * * 直线 / 花括号的描边居中在路径上，圆线帽 → 越出半个线宽；
 * * 箭头：帽半宽 1.7×线宽（垂直于箭杆），加圆线帽半宽 → 有端型时 2.2×线宽，无端型 0.5×；
 * * 文本框描边居中在框边上 → 越出半个 `border_pt`（缺省 0.75）；
 * * 线宽下限 0.05 pt。
 */
function strokeOutset(o: CanvasObject): number {
  switch (o.type) {
    case 'text':
      return o.borderColor ? ptToMm(Math.max(o.borderPt ?? 0.75, 0.05) / 2) : 0
    case 'shape':
      return o.shape === 'line' || o.shape === 'brace' ? ptToMm(Math.max(o.strokePt, 0.05) / 2) : 0
    case 'arrow': {
      const sw = Math.max(o.strokePt, 0.05)
      // 与 `geometry.arrow_heads` 同一判据：新字段任一在就只看新字段，否则看旧 head（缺省 end）
      const anyHead =
        o.headStart !== undefined || o.headEnd !== undefined
          ? (o.headStart ?? 'none') !== 'none' || (o.headEnd ?? 'none') !== 'none'
          : (o.head ?? 'end') !== 'none'
      return ptToMm(anyHead ? sw * 2.2 : sw / 2)
    }
    default:
      return 0
  }
}

/** 一个对象在页面上实际画到的轴对齐包围盒（含描边 / 箭头头部外扩；带任意角度旋转时取旋转后的外接框） */
function objectBounds(o: CanvasObject): CropRect {
  const m = strokeOutset(o)
  const w0 = o.w + 2 * m
  const h0 = o.h + 2 * m
  const cx = o.x + o.w / 2
  const cy = o.y + o.h / 2
  const deg = objectRotation(o)
  if (!deg) return { x: cx - w0 / 2, y: cy - h0 / 2, w: w0, h: h0 }
  const rad = (deg * Math.PI) / 180
  const c = Math.abs(Math.cos(rad))
  const sn = Math.abs(Math.sin(rad))
  const w = w0 * c + h0 * sn
  const h = w0 * sn + h0 * c
  return { x: cx - w / 2, y: cy - h / 2, w, h }
}

/** 可见对象的并集包围盒；没有可见对象（或退化为零面积）回 null */
export function visibleContentBounds(objects: readonly CanvasObject[]): CropRect | null {
  let x0 = Infinity
  let y0 = Infinity
  let x1 = -Infinity
  let y1 = -Infinity
  for (const o of objects) {
    if (o.hidden) continue
    const b = objectBounds(o)
    x0 = Math.min(x0, b.x)
    y0 = Math.min(y0, b.y)
    x1 = Math.max(x1, b.x + b.w)
    y1 = Math.max(y1, b.y + b.h)
  }
  if (!Number.isFinite(x0) || x1 - x0 <= 0 || y1 - y0 <= 0) return null
  return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 }
}

/**
 * 按包围盒裁出「导出区域」：返回新页面尺寸与整体平移后的对象（新数组、新对象，
 * 不碰入参）。没有可见对象时原样返回整页。
 */
export function cropToVisibleContent<T extends CanvasObject>(
  page: { w: number; h: number },
  objects: readonly T[],
): { page: { w: number; h: number }; objects: T[]; rect: CropRect | null } {
  const rect = visibleContentBounds(objects)
  if (!rect) return { page: { w: page.w, h: page.h }, objects: [...objects], rect: null }
  return {
    page: { w: rect.w, h: rect.h },
    objects: objects.map((o) => ({ ...o, x: o.x - rect.x, y: o.y - rect.y })),
    rect,
  }
}

/**
 * 单图退路导出用的「裁后的文档」：页面 = 包围盒尺寸、对象平移。**检查 / proof report / 请求 / 预览
 * 全吃这一份**（调用方算一次），不再另有裁切开关。没有可见对象时原样返回入参。
 */
export function croppedDocument(doc: FigureDocument): FigureDocument {
  const c = cropToVisibleContent(doc.page, doc.objects)
  if (!c.rect) return doc
  return { ...doc, page: { ...doc.page, w: c.page.w, h: c.page.h }, objects: c.objects }
}
