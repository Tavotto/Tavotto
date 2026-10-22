import type { CanvasObject } from '@/types/document'
import { objectRotation } from '@/types/document'

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

/** 一个对象在页面上占的轴对齐包围盒（文字 / 标注 / 形状带任意角度旋转时取旋转后的外接框） */
function objectBounds(o: CanvasObject): CropRect {
  const deg = objectRotation(o)
  if (!deg) return { x: o.x, y: o.y, w: o.w, h: o.h }
  const rad = (deg * Math.PI) / 180
  const c = Math.abs(Math.cos(rad))
  const s = Math.abs(Math.sin(rad))
  const w = o.w * c + o.h * s
  const h = o.w * s + o.h * c
  const cx = o.x + o.w / 2
  const cy = o.y + o.h / 2
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
