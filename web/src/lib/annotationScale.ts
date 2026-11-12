import { visualBounds, type Rect } from '@/lib/geometry'
import type { CanvasObject, PanelObject } from '@/types/document'

/**
 * 图内标注跟着图走（ADR 0161 修订 2026-11-04，取代「连同标注」开关）：拖动 / 方向键移动一张面板时
 * 平移，多图排版里等比缩放它时按同一固定点、同一比例缩放。
 *
 * 标注 = 非面板对象（文字 / 箭头 / 形状…）。判据只看**几何与状态**（`annotationsInside`）：看得见的盒
 * （旋转过的取外接框）**整个落在**面板起手时的可见框里、没锁定、没隐藏；成组的标注要整组都满足才跟
 * （只带走半个组等于把组拆散）。压在图边上、伸到图外的标注是独立的，不跟。缩放是对"固定点"的位似：位置（取框中心，旋转过的标注
 * 也不漂）、框尺寸、以及所有按**页面 pt / mm 计的量**一起乘同一个比例。
 *
 * 要乘的字段是一张表，不按对象类型 switch：对象上有哪个就乘哪个（字号 `sizePt`、线宽
 * `strokePt`、文本框描边 `borderPt`、文本框内边距 `padding`、圆角 `cornerRadius`）。
 * 端点 `start` / `end` 是相对框的比例，不变。新增一个按页面尺寸计的字段，加进这张表。
 */
export const ANNOTATION_SCALED_FIELDS = [
  'sizePt',
  'strokePt',
  'borderPt',
  'padding',
  'cornerRadius',
] as const

type ScaledField = (typeof ANNOTATION_SCALED_FIELDS)[number]

export interface AnnotationOrigin {
  id: string
  /** 框中心（页面 mm） */
  cx: number
  cy: number
  w: number
  h: number
  fields: Partial<Record<ScaledField, number>>
}

const EPS_MM = 1e-6

function containsRect(outer: Rect, inner: Rect): boolean {
  return (
    inner.x >= outer.x - EPS_MM &&
    inner.y >= outer.y - EPS_MM &&
    inner.x + inner.w <= outer.x + outer.w + EPS_MM &&
    inner.y + inner.h <= outer.y + outer.h + EPS_MM
  )
}

/** 此刻在这张面板里、会跟着它移动 / 缩放的标注（文档顺序） */
export function annotationsInside(
  objects: readonly CanvasObject[],
  panel: PanelObject,
): CanvasObject[] {
  const box: Rect = { x: panel.x, y: panel.y, w: panel.w, h: panel.h }
  const inside = (o: CanvasObject) =>
    o.type !== 'panel' && !o.locked && !o.hidden && containsRect(box, visualBounds(o))
  return objects.filter(
    (o) =>
      inside(o) &&
      (!o.groupId || objects.every((m) => m.groupId !== o.groupId || inside(m))),
  )
}

/**
 * 一组要移动的对象补上跟着它们走的图内标注：其中每张面板里的标注（`annotationsInside`），
 * 已在组里的不重复加。返回文档顺序。
 */
export function withFollowers(
  objects: readonly CanvasObject[],
  moving: readonly CanvasObject[],
): CanvasObject[] {
  const ids = new Set(moving.map((o) => o.id))
  for (const o of moving) {
    if (o.type !== 'panel') continue
    for (const a of annotationsInside(objects, o)) ids.add(a.id)
  }
  return objects.filter((o) => ids.has(o.id))
}

export function captureOrigins(objs: readonly CanvasObject[]): AnnotationOrigin[] {
  return objs.map((o) => {
    const fields: AnnotationOrigin['fields'] = {}
    for (const k of ANNOTATION_SCALED_FIELDS) {
      const v = (o as unknown as Record<string, unknown>)[k]
      if (typeof v === 'number' && Number.isFinite(v)) fields[k] = v
    }
    return { id: o.id, cx: o.x + o.w / 2, cy: o.y + o.h / 2, w: o.w, h: o.h, fields }
  })
}

/** 在事务草稿上把标注按"固定点 + 比例"摆好（每帧从起手快照现算，不累乘） */
export function applyAnnotationScale(
  objects: CanvasObject[],
  origins: readonly AnnotationOrigin[],
  fixed: { x: number; y: number },
  s: number,
): void {
  for (const org of origins) {
    const o = objects.find((x) => x.id === org.id)
    if (!o) continue
    const w = org.w * s
    const h = org.h * s
    const cx = fixed.x + (org.cx - fixed.x) * s
    const cy = fixed.y + (org.cy - fixed.y) * s
    o.x = cx - w / 2
    o.y = cy - h / 2
    o.w = w
    o.h = h
    const rec = o as unknown as Record<string, number>
    for (const k of ANNOTATION_SCALED_FIELDS) {
      const v = org.fields[k]
      if (v !== undefined) rec[k] = v * s
    }
  }
}
