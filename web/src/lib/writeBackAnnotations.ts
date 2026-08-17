import type { ExportObject } from './api'
import { panelFullRect } from './elementGeom'
import { toExportObjects } from './exportPayload'
import { rectsIntersect } from './geometry'
import type { CanvasObject, PanelObject } from '@/types/document'
import { panelRotation } from '@/types/document'

/**
 * 「写回原始文件」携带画布标注：把与面板重叠的标注（文字/箭头/形状）
 * 从画布 mm 换算成**该图自身的 mm**（后端 annotate_asset 按这套坐标直接
 * 用导出合成同一组 _draw_* 绘制）。
 *
 * - 面板带 90° 旋转或翻转时换算对不上（标注要跟着反变换、文字还得转向），
 *   明确不支持——blocked 里给原因，UI 把选项禁掉；
 * - 一条标注同时压着多个写回目标时，只归属重叠面积最大的那个面板，
 *   绝不写进两张图；
 * - 长度类字段（字号/线宽/圆角/内边距）按面板显示比例一起缩放：
 *   画布上看到多大，写回后在图里就是多大。
 */

export interface PanelAnnotations {
  /** 已换算成图自身 mm 的标注载荷（数组序 = z 序） */
  objects: ExportObject[]
  /** 参与写回的画布对象 id（成功后从画布移除用） */
  objectIds: string[]
}

const ANNOTATION_TYPES = new Set(['text', 'arrow', 'shape'])

function overlapArea(a: CanvasObject, p: PanelObject): number {
  const w = Math.min(a.x + a.w, p.x + p.w) - Math.max(a.x, p.x)
  const h = Math.min(a.y + a.h, p.y + p.h) - Math.max(a.y, p.y)
  return w > 0 && h > 0 ? w * h : 0
}

/** 该面板能否携带标注写回；不能时给出人话原因 */
export function annotationsBlocked(panel: PanelObject): string | null {
  if (panelRotation(panel)) return '面板带旋转，标注坐标换算不过去'
  if (panel.flipH || panel.flipV) return '面板带翻转，标注坐标换算不过去'
  if (panel.fileKind !== 'pdf') return '该素材只有位图、没有矢量 PDF'
  return null
}

/**
 * 按「重叠面积最大」把标注分配给各写回目标面板。
 * 返回 Map<panelId, PanelAnnotations>；没有可写回标注的面板不在结果里。
 */
export function collectPanelAnnotations(
  panels: PanelObject[],
  objects: readonly CanvasObject[],
): Map<string, PanelAnnotations> {
  const eligible = panels.filter((p) => !annotationsBlocked(p))
  const byPanel = new Map<string, CanvasObject[]>()
  for (const o of objects) {
    if (!ANNOTATION_TYPES.has(o.type) || o.hidden) continue
    let best: PanelObject | null = null
    let bestArea = 0
    for (const p of eligible) {
      if (!rectsIntersect(o, p)) continue
      const area = overlapArea(o, p)
      if (area > bestArea) {
        bestArea = area
        best = p
      }
    }
    if (best) {
      byPanel.set(best.id, [...(byPanel.get(best.id) ?? []), o])
    }
  }

  const out = new Map<string, PanelAnnotations>()
  for (const p of eligible) {
    const anns = byPanel.get(p.id)
    if (!anns?.length) continue
    out.set(p.id, {
      objectIds: anns.map((o) => o.id),
      objects: toExportObjects(anns).map((o) => toFigureMm(o, p)),
    })
  }
  return out
}

/** 画布 mm → 图自身 mm：位置减去内容原点再缩放；长度类字段按同比例缩放 */
function toFigureMm(o: ExportObject, panel: PanelObject): ExportObject {
  const full = panelFullRect(panel)
  const kx = panel.nativeW / full.w
  const ky = panel.nativeH / full.h
  const k = (kx + ky) / 2 // 字号/线宽这类各向同性的量；面板不等比时取均值
  const scaled: ExportObject = {
    ...o,
    x_mm: (o.x_mm - full.x) * kx,
    y_mm: (o.y_mm - full.y) * ky,
    w_mm: o.w_mm * kx,
    h_mm: o.h_mm * ky,
  }
  if (scaled.type === 'text') {
    scaled.size_pt *= k
    if (scaled.padding_mm != null) scaled.padding_mm *= k
    if (scaled.border_pt != null) scaled.border_pt *= k
  } else if (scaled.type === 'arrow') {
    scaled.stroke_pt *= k
  } else if (scaled.type === 'shape') {
    scaled.stroke_pt *= k
    if (scaled.corner_radius_mm != null) scaled.corner_radius_mm *= k
  }
  return scaled
}
