import { effectiveFillGradient, effectiveStrokeGradient, type ReadGradient } from './gradient'
import { figureSnapshotFor } from './figureSnapshot'
import { artifactRequestFields } from './artifactValidation'
import type { ExportClipPath, ExportObject } from './api'
import { clipEntriesOf, groupMasks, groupPathOf, isActiveMask } from './groupTree'
import { maskAsPath } from './clipMask'
import type { CanvasObject, Gradient, PathTextObject } from '@/types/document'
import { DEFAULT_STAR_POINTS, DEFAULT_STAR_RATIO, isKnownObject, isStrokeOnlyShape, panelRotation, readPath, shapeStrokeOn } from '@/types/document'
import { DEFAULT_LINE_CAP, defaultLineJoin, miterLimitOf, STROKE_MITER_LIMIT, strokeOpacityOf, validDashPattern } from './shapeGeometry'
import { PATH_DEFAULT_JOIN } from './pathGeom'

/**
 * 画布对象 → 导出载荷。
 *
 * 两条契约必须原样保持：
 * - **顺序即 z 序**（数组序，底 → 顶），后端按同样顺序合成；
 * - **隐藏对象不发**，locked 照常导出（锁只挡编辑，不改变成图）。
 */
export function toExportObjects(
  objects: readonly CanvasObject[],
  /** 找剪切蒙版用的全集（蒙版本身是隐藏的，调用方给的列表可能已经把它滤掉了）；缺省 = `objects` */
  context: readonly CanvasObject[] = objects,
): ExportObject[] {
  // 不认识的类型（更新的构建写进来的）不进载荷：`toExportObject` 对它回 undefined，序列化成 null 后被后端
  // 静默丢掉。界面在导出前用 `newerObjectCount` 拦下，这里只保证载荷里没有空洞。
  // 剪切组里生效的蒙版不画（ADR 0191）：它只作为被裁对象的 `clip` 出现
  const masks = groupMasks(context)
  return objects
    .filter((o) => !o.hidden && isKnownObject(o) && !isActiveMask(o, masks))
    .map((o) => {
      const e = o.type === 'pathText' ? pathTextExport(o, lineWidthOf(o, context)) : toExportObject(o)
      const chain = clipEntriesOf(o, masks)
      // 名字与组树只给 SVG（ADR 0208 §七之三）：对象名写成 `id`、文档的组写成嵌套的 `<g>`、剪切组的蒙版只在组上写一次。
      // PDF / EPS / 位图不看这几个键；没有名字、不在组里 = 一个键都不多，载荷与以前逐字节相同
      const path = groupPathOf(o)
      return {
        ...e,
        ...(typeof o.name === 'string' && o.name.trim() ? { name: o.name } : {}),
        ...(path.length ? { group_path: path } : {}),
        // `group` 说明这个蒙版是哪一层组的：后端据此把蒙版挂在组上，而不是每个被裁的对象各复制一份
        ...(chain.length ? { clip: chain.map((c) => ({ ...toExportClip(c.mask), group: c.gid })) } : {}),
      }
    })
}

/** 蒙版 → 载荷里的轮廓（只有几何）。换不出路径的（退化成一个点的形状）发一条空轮廓 = 全部裁掉，与画布同一条 */
export function toExportClip(m: CanvasObject): ExportClipPath {
  const p = maskAsPath(m)
  if (!p) return { type: 'path', x_mm: m.x, y_mm: m.y, w_mm: m.w, h_mm: m.h, subpaths: [] }
  return {
    type: 'path',
    x_mm: p.x,
    y_mm: p.y,
    w_mm: p.w,
    h_mm: p.h,
    subpaths: p.subpaths.map((sp) => ({
      closed: sp.closed,
      nodes: sp.nodes.map((n) => ({ p: n.p, ...(n.in ? { in: n.in } : {}), ...(n.out ? { out: n.out } : {}) })),
    })),
    ...(p.fillRule === 'evenodd' ? { fill_rule: 'evenodd' as const } : {}),
    ...(p.rotationDeg ? { rotation_deg: p.rotationDeg } : {}),
  }
}

/** 会进这次导出、但本构建画不出来的对象个数（看得见且类型不认识，`isKnownObject`）。非 0 时导出不许开始 */
export function newerObjectCount(objects: readonly CanvasObject[]): number {
  return objects.filter((o) => !o.hidden && !isKnownObject(o)).length
}

/**
 * 描边的附加外观（斜接上限、自定义虚线；形状再加描边对齐）：缺省一律不发——老后端拿到的载荷与加属性之前逐字节一致。
 * 读法与画布同一组函数（`miterLimitOf` / `validDashPattern`），后端 `geometry.miter_limit` / `valid_dash_pattern` 同一条判据。
 */
function appearanceExtras(o: {
  miterLimit?: number
  dashPattern?: number[]
  strokeAlign?: 'center' | 'outside' | 'inside'
  type: string
  shape?: string
}): { miter_limit?: number; dash_pattern?: number[]; stroke_align?: 'center' | 'outside' | 'inside' } {
  const ml = miterLimitOf(o.miterLimit)
  const dp = validDashPattern(o.dashPattern)
  // 形状：缺省内侧，只发 center / outside；路径（ADR 0185 M2）：缺省居中，只发 inside / outside。字段原样发，
  // 生效与否（路径要全闭合、有描边）由后端 `geometry.path_stroke_align` 用同一条判据再判
  const align =
    o.type === 'shape' && o.shape && !isStrokeOnlyShape(o.shape)
      ? o.strokeAlign === 'center' || o.strokeAlign === 'outside'
        ? o.strokeAlign
        : undefined
      : o.type === 'path' && (o.strokeAlign === 'inside' || o.strokeAlign === 'outside')
        ? o.strokeAlign
        : undefined
  return {
    ...(ml !== STROKE_MITER_LIMIT ? { miter_limit: ml } : {}),
    ...(dp ? { dash_pattern: dp } : {}),
    ...(align ? { stroke_align: align } : {}),
  }
}

/** 渐变（ADR 0186）：只发生效的那份（填充渐变要有填充、描边渐变要有描边），读过的形状；缺省不发 */
function gradientExtras(
  o: { fill: string | null; fillGradient?: Gradient; strokeGradient?: Gradient },
  strokeOn: boolean,
): { fill_gradient?: ReadGradient; stroke_gradient?: ReadGradient } {
  const f = effectiveFillGradient(o)
  const st = effectiveStrokeGradient(o, strokeOn)
  return { ...(f ? { fill_gradient: f } : {}), ...(st ? { stroke_gradient: st } : {}) }
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
        ...artifactRequestFields(o),
        id: o.fileId,
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
        ...appearanceExtras(o),
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
        sides: o.shape === 'polygon' ? (o.sides ?? 6) : o.shape === 'star' ? (o.sides ?? DEFAULT_STAR_POINTS) : undefined,
        // 星形内径比：缺省不发（后端按同一个缺省补）
        star_ratio: o.shape === 'star' && o.starRatio != null && o.starRatio !== DEFAULT_STAR_RATIO ? o.starRatio : undefined,
        fill_opacity: o.fill && o.fillOpacity != null && o.fillOpacity < 1 ? o.fillOpacity : undefined,
        dash: o.dash && o.dash !== 'solid' ? o.dash : undefined,
        // 无边框只对封闭形状发（直线 / 大括号只有描边）；无边框时描边相关的其余字段都不发
        stroke: shapeStrokeOn(o) ? undefined : false,
        stroke_opacity: shapeStrokeOn(o) ? strokeOpacityOf(o.strokeOpacity) : undefined,
        line_cap: o.lineCap && o.lineCap !== DEFAULT_LINE_CAP ? o.lineCap : undefined,
        line_join: o.lineJoin && o.lineJoin !== defaultLineJoin(o.shape) ? o.lineJoin : undefined,
        ...appearanceExtras(o),
        ...gradientExtras(o, shapeStrokeOn(o)),
        rotation_deg: o.rotationDeg || undefined,
      }
    case 'path': {
      // 自由路径（ADR 0185）：锚点与手柄原样（比例坐标），`smooth` 只是编辑语义、画法用不上，不发；
      // 其余字段与形状 / 箭头同名同义，缺省一律不发（后端 `geometry.read_path` 补同一份缺省）
      const strokeOn = readPath(o).strokeOn
      return {
        ...box,
        type: 'path',
        subpaths: o.subpaths.map((sp) => ({
          closed: sp.closed,
          nodes: sp.nodes.map((n) => ({
            p: n.p,
            ...(n.in ? { in: n.in } : {}),
            ...(n.out ? { out: n.out } : {}),
          })),
        })),
        fill_rule: o.fillRule === 'evenodd' ? 'evenodd' : undefined,
        stroke_pt: o.strokePt,
        color: o.color,
        fill: o.fill,
        fill_opacity: o.fill && o.fillOpacity != null && o.fillOpacity < 1 ? o.fillOpacity : undefined,
        dash: o.dash && o.dash !== 'solid' ? o.dash : undefined,
        stroke: strokeOn ? undefined : false,
        stroke_opacity: strokeOn ? strokeOpacityOf(o.strokeOpacity) : undefined,
        line_cap: o.lineCap && o.lineCap !== DEFAULT_LINE_CAP ? o.lineCap : undefined,
        line_join: o.lineJoin && o.lineJoin !== PATH_DEFAULT_JOIN ? o.lineJoin : undefined,
        ...appearanceExtras(o),
        ...gradientExtras(o, strokeOn),
        head_start: o.headStart && o.headStart !== 'none' ? o.headStart : undefined,
        head_end: o.headEnd && o.headEnd !== 'none' ? o.headEnd : undefined,
        head_size_start: o.headSizeStart && o.headSizeStart !== 'medium' ? o.headSizeStart : undefined,
        head_size_end: o.headSizeEnd && o.headSizeEnd !== 'medium' ? o.headSizeEnd : undefined,
        rotation_deg: o.rotationDeg || undefined,
      }
    }
    case 'pathText':
      // 线宽要看文档里别的对象：`toExportObjects` 带着上下文走 `pathTextExport`；单独转一个时按没有线算
      return pathTextExport(o)
  }
}

/**
 * 路径文字那条线的描边宽度（pt）：生成时一起成组的那条线（`lineId`）还在、看得见、有描边就取它的线宽，否则 0。
 * 自动离线要用（ADR 0190）；画布预览与导出载荷同一个判据。
 */
export function lineWidthOf(o: PathTextObject, objects: readonly CanvasObject[]): number {
  if (!o.lineId) return 0
  const l = objects.find((x) => x.id === o.lineId)
  if (!l || l.hidden) return 0
  if (l.type === 'arrow') return Math.max(l.strokePt, 0)
  if (l.type === 'path') return readPath(l).strokeOn ? Math.max(l.strokePt, 0) : 0
  if (l.type === 'shape') return shapeStrokeOn(l) ? Math.max(l.strokePt, 0) : 0
  return 0
}

/** 路径文字的导出字段：缺省不发（后端 `pathtext` 补同一份缺省）。画布预览接口也用这一份 */
export function pathTextExport(o: PathTextObject, lineWidthPt = 0): ExportObject {
  return {
    x_mm: o.x,
    y_mm: o.y,
    w_mm: o.w,
    h_mm: o.h,
    type: 'pathText',
    text: o.text,
    size_pt: o.sizePt,
    bold: o.bold,
    italic: o.italic === true,
    color: o.color,
    font_family: o.fontFamily,
    subpaths: o.subpaths.map((sp) => ({
      closed: sp.closed,
      nodes: sp.nodes.map((n) => ({ p: n.p, ...(n.in ? { in: n.in } : {}), ...(n.out ? { out: n.out } : {}) })),
    })),
    start_offset: o.startOffset || undefined,
    text_align: o.textAlign,
    flip: o.flip === true ? true : undefined,
    baseline_offset_pt:
      typeof o.baselineOffsetPt === 'number' && Number.isFinite(o.baselineOffsetPt) ? o.baselineOffsetPt : undefined,
    line_width_pt: lineWidthPt > 0 ? lineWidthPt : undefined,
    rotation_deg: o.rotationDeg || undefined,
  }
}
