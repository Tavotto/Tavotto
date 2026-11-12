import { chartSourceFields } from './chartSource'
import { figureOriginFields, figureOriginFor, figureSnapshotFor } from './figureSnapshot'
import { artifactRequestFields } from './artifactValidation'
import type { ExportObject } from './api'
import type { CanvasObject } from '@/types/document'
import { panelRotation, shapeStrokeOn } from '@/types/document'
import { DEFAULT_LINE_CAP, defaultLineJoin, strokeOpacityOf } from './shapeGeometry'

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
        ...(figureOriginFor(o) === undefined ? artifactRequestFields(o) : {}),
        id: o.fileId,
        ...(o.chart
          ? {
              chart: structuredClone(o.chart),
              // 坐标合同随面板走（/2 的 chartFrame）；/1 面板发既有字面量
              chart_render_profile: o.chartFrame?.renderProfile ?? 'native-manifest/1',
            }
          : {}),
        ...chartSourceFields(o.chartSource),
        ...figureOriginFields(o),
        ...(figureSnapshotFor(o) ? { figure_snapshot: figureSnapshotFor(o) } : {}),
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
        // 原图字体的身份（ADR 0164）：没有就不发；有就原样发（后端 `sourcefont.parse_source_font` 校验）
        source_font: o.sourceFont,
        // 同理：`interpretation` 没设过 = auto = 后端缺省，不发。
        interpretation: o.interpretation,
        // 同理：`textFormat` 没设过 = 旧标记 = 后端缺省，不发（ADR 0152）。
        text_format: o.textFormat,
        // 排向：缺席 = 横排 = 后端缺省，不发（ADR 0163）。
        writing_mode: o.writingMode,
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
        // 端型大小：缺省 / 中 = 不发（ADR 0174）
        head_size_start: o.headSizeStart && o.headSizeStart !== 'medium' ? o.headSizeStart : undefined,
        head_size_end: o.headSizeEnd && o.headSizeEnd !== 'medium' ? o.headSizeEnd : undefined,
        dash: o.dash && o.dash !== 'solid' ? o.dash : undefined,
        stroke_opacity: strokeOpacityOf(o.strokeOpacity),
        line_cap: o.lineCap && o.lineCap !== DEFAULT_LINE_CAP ? o.lineCap : undefined,
        line_join: o.lineJoin && o.lineJoin !== 'round' ? o.lineJoin : undefined,
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
        // 无边框只对封闭形状发（直线 / 大括号只有描边）；无边框时描边相关的其余字段都不发
        stroke: shapeStrokeOn(o) ? undefined : false,
        stroke_opacity: shapeStrokeOn(o) ? strokeOpacityOf(o.strokeOpacity) : undefined,
        line_cap: o.lineCap && o.lineCap !== DEFAULT_LINE_CAP ? o.lineCap : undefined,
        line_join: o.lineJoin && o.lineJoin !== defaultLineJoin(o.shape) ? o.lineJoin : undefined,
        rotation_deg: o.rotationDeg || undefined,
      }
  }
}
