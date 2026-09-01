import type { ExportObject } from './api'
import type { CanvasObject } from '@/types/document'
import { panelRotation } from '@/types/document'

/**
 * 画布对象 → 导出载荷。
 *
 * 两条契约必须原样保持：
 * - **顺序即 z 序**（数组序，底 → 顶），后端按同样顺序合成；
 * - **隐藏对象不发**，locked 照常导出（锁只挡编辑，不改变成图）。
 */
export function toExportObjects(objects: CanvasObject[]): ExportObject[] {
  return objects.filter((o) => !o.hidden).map(toExportObject)
}

function toExportObject(o: CanvasObject): ExportObject {
  const box = { x_mm: o.x, y_mm: o.y, w_mm: o.w, h_mm: o.h }

  switch (o.type) {
    case 'panel': {
      // 缺省值不发：老后端与老布局文件的载荷保持一模一样
      const rotation = panelRotation(o)
      const opacity = o.opacity != null && o.opacity < 1 ? Math.max(0, o.opacity) : undefined
      return {
        ...box,
        type: 'panel',
        id: o.fileId,
        overrides: o.overrides.length ? o.overrides : undefined,
        crop: o.crop,
        rotation: rotation || undefined,
        opacity,
        flip_h: o.flipH || undefined,
        flip_v: o.flipV || undefined,
      }
    }
    case 'text':
      return {
        ...box,
        type: 'text',
        text: o.text,
        size_pt: o.sizePt,
        bold: o.bold,
        italic: o.italic === true,
        color: o.color,
        align: o.align,
        // 新属性缺省不发：老后端拿到的载荷与旧版逐字节一致。
        // `fontFamily` 没设过 = 继承默认（衬线），**不发**——后端缺省就是它，
        // 发一个等价的显式值只会让老文档的载荷凭空多一个字段。
        font_family: o.fontFamily,
        underline: o.underline || undefined,
        line_height: o.lineHeight,
        padding_mm: o.padding || undefined,
        bg: o.bg || undefined,
        border_color: o.borderColor || undefined,
        border_pt: o.borderColor ? (o.borderPt ?? 0.75) : undefined,
        rotation_deg: o.rotationDeg || undefined,
      }
    case 'arrow':
      return {
        ...box,
        type: 'arrow',
        start: o.start,
        end: o.end,
        stroke_pt: o.strokePt,
        color: o.color,
        head: o.head,
        // 新端型只在文档写了新字段时发；后端在缺失时按旧 head 推导
        head_start: o.headStart,
        head_end: o.headEnd,
        dash: o.dash && o.dash !== 'solid' ? o.dash : undefined,
        rotation_deg: o.rotationDeg || undefined,
      }
    case 'shape':
      return {
        ...box,
        type: 'shape',
        shape: o.shape,
        // 直线端点只在 line 上有意义；缺省不发，后端按 (0,0.5)→(1,0.5) 兜底
        start: o.shape === 'line' ? o.start : undefined,
        end: o.shape === 'line' ? o.end : undefined,
        stroke_pt: o.strokePt,
        color: o.color,
        fill: o.fill,
        corner_radius_mm: o.cornerRadius || undefined,
        sides: o.shape === 'polygon' ? (o.sides ?? 6) : undefined,
        fill_opacity: o.fill && o.fillOpacity != null && o.fillOpacity < 1 ? o.fillOpacity : undefined,
        dash: o.dash && o.dash !== 'solid' ? o.dash : undefined,
        rotation_deg: o.rotationDeg || undefined,
      }
  }
}
