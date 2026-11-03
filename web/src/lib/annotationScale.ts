import { rectsIntersect } from '@/lib/geometry'
import type { CanvasObject, PanelObject } from '@/types/document'

/**
 * 「连同标注」（ADR 0161）：多图排版里等比缩放一张面板时，叠在它上面的标注跟着缩。
 *
 * 标注 = 非面板对象（文字 / 箭头 / 形状…）。判据只看**几何与状态**：与面板起手时的可见框相交
 * （含被它包含）、没锁定、没隐藏。缩放是对"固定点"的位似：位置（取框中心，旋转过的标注
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

/** 此刻会跟着这张面板缩放的标注 */
export function annotationsOver(objects: readonly CanvasObject[], panel: PanelObject): CanvasObject[] {
  const box = { x: panel.x, y: panel.y, w: panel.w, h: panel.h }
  return objects.filter(
    (o) => o.type !== 'panel' && !o.locked && !o.hidden && rectsIntersect(o, box),
  )
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
