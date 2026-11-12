import type { ExportObject } from './api'
import { buildExportRequest, type ExportRequestInput } from './exportRequest'

/**
 * EPS 与半透明（导出前提醒的判定，ADR 0174 后续）。
 *
 * PostScript 没有透明。单图「原图 + 画布标注」导出 EPS 时，标注里只要有 alpha < 1，后端
 * （`rendercore/epswriter.overlay_ps`）就会让 EPS 这一项失败 `eps_annotations_unsupported`。
 * 这里在点导出之前先数出来，让用户选：改导出 PDF，或 EPS 按不透明导出（请求 `eps_opaque: true`）。
 *
 * **判据只有一份**：后端 IR 的 `operations_of`（`object_alpha` / `group_opacity`）。这里读的是**载荷里的对象**
 * （`toExportObjects` 的产物：隐藏对象已经不在里面），按 `rendercore/plan.py` / `geometry.py` 怎么读这些字段来判：
 * - 形状：`fill_opacity < 1` 且真有填充、且不是 line / brace（那两种只有描边）→ 填充 alpha；
 *   `stroke_opacity < 1` 且有描边（`stroke !== false`，line / brace 恒有）→ 描边 alpha；
 * - 箭头：`stroke_opacity < 1` → 整支箭头是透明组（`Group.opacity`）；
 * - 文字：颜色 / 底色 / 边框都没有 alpha，永远不算；
 * - 面板不进 `original.objects`（标注只有 text / arrow / shape）。
 * `_unit()`：非有限数、布尔、≥ 1 都按不透明。
 */
const translucent = (v: unknown): boolean =>
  typeof v === 'number' && Number.isFinite(v) && v < 1

/** 这个标注对象里有没有 EPS 画不出的半透明 */
export function isEpsTranslucent(o: ExportObject): boolean {
  if (o.type === 'arrow') return translucent(o.stroke_opacity)
  if (o.type === 'shape') {
    const lineLike = o.shape === 'line' || o.shape === 'brace'
    const strokeOn = lineLike || o.stroke !== false
    return (!lineLike && !!o.fill && translucent(o.fill_opacity)) || (strokeOn && translucent(o.stroke_opacity))
  }
  return false
}

/** 会进 EPS 叠加层的标注里有几个半透明（载荷对象：隐藏的已被 `toExportObjects` 滤掉） */
export function translucentAnnotationCount(objects: readonly ExportObject[]): number {
  return objects.filter(isEpsTranslucent).length
}

/**
 * 这次导出**真会发出去的请求**里，EPS 的标注叠加层有几个半透明对象；0 = 不需要提醒
 * （没选 EPS / EPS 在这张图上给不出而没发 / 不是单图带标注 / 多页 PDF / 没有半透明）。
 */
export function epsTranslucentCount(input: ExportRequestInput): number {
  const { request } = buildExportRequest({ ...input, epsOpaque: false })
  if (request.scope !== 'original' || request.merge_pdf || !request.formats.includes('eps')) return 0
  return translucentAnnotationCount(request.original?.objects ?? [])
}

/** 「改导出 PDF」：EPS 换成 PDF（PDF 已在就只是去掉 EPS），其余格式与次序不动 */
export function swapEpsForPdf(formats: readonly string[]): string[] {
  const out: string[] = []
  for (const f of formats) {
    const next = f === 'eps' ? 'pdf' : f
    if (!out.includes(next)) out.push(next)
  }
  return out
}
