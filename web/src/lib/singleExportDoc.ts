import type { CanvasObject, FigureDocument, PanelObject } from '@/types/document'
import { panelFullSize, panelRotation } from '@/types/document'
import type { OriginalOutputSpec } from './originalSpec'

/**
 * 单图编辑（原图口径）带入画布上的标注（QA-WIN-20）。
 *
 * 单图编辑的舞台画这张画布上的全部对象（图 + 标注 / 文字 / 形状，`workflows-and-original-spec.md`），而
 * `scope=original` 的请求只有一张图自己——标注因此在预览与导出文件里一起丢了。这里**不写第二套标注绘制**：
 * 有可见标注时，把「图 + 标注」合成一份**以原图图幅为页面**的文档：预览画它，请求的 `original.objects`
 * 取其中的标注部分（`annotationsOf`）；请求仍是 `scope=original`，后端用整版导出那条 RenderCore 合成路径
 * 把标注画在原图上（`app._export_produce_original_annotated`），没有第二套标注绘制。
 *
 * 合成口径（与 ADR 0028 §2.3「原图导出」一致）：
 * * 页面 = 原图图幅（`OriginalOutputSpec` 的 mm，即请求里的 `w_mm` / `h_mm`；没有规格不合成），面板落在 (0, 0)，
 *   且**不套用**画布上的缩放 / 裁剪 / 旋转 / 翻转 / 透明度——与不带标注的原图导出是同一张图；
 * * 标注位置从画布映射到原图坐标：以「图内容原点」为原点、按 图幅 / 画布上图的完整显示尺寸 的比例缩放 x / y / w / h；
 *   字号、线宽等 pt 量**不缩**（共享规则 §8：不因画布缩放缩字号）。单图编辑里图幅被静默校正为真实图幅，
 *   比例通常恰为 1，此时只是平移；
 * * 图幅之外的标注被页面裁掉（页面就是图幅），不扩大页面。
 *
 * 没有可见标注（或不是恰好一张可见的图）返回 `null`：调用方照旧发纯原图请求，载荷与从前逐字节一致。
 */
export function singleAnnotatedDocument(
  doc: FigureDocument,
  panel: PanelObject | null,
  spec: OriginalOutputSpec | null,
): FigureDocument | null {
  if (!panel || panel.hidden) return null
  const visible = doc.objects.filter((o) => !o.hidden)
  if (visible.filter((o) => o.type === 'panel').length !== 1) return null
  if (!visible.some((o) => o.type !== 'panel')) return null
  if (!visible.some((o) => o.id === panel.id)) return null

  const full = panelFullSize(panel)
  // 页面 = 请求里 `original.w_mm / h_mm` 的那个图幅（同一份 spec，含占位规格）：没有规格就没有图幅，不合成
  if (!spec || !(spec.widthMm > 0) || !(spec.heightMm > 0)) return null
  const frame = { w: spec.widthMm, h: spec.heightMm }

  // 图内容（未裁剪、未旋转）左上角在画布上的位置；旋转 / 翻转时内容原点无法由 x/y 单独还原，退回面板框左上角
  const plain = panelRotation(panel) === 0 && !panel.flipH && !panel.flipV
  const ox = plain ? panel.x - (panel.crop?.x ?? 0) * full.w : panel.x
  const oy = plain ? panel.y - (panel.crop?.y ?? 0) * full.h : panel.y
  const sx = full.w > 0 ? frame.w / full.w : 1
  const sy = full.h > 0 ? frame.h / full.h : 1

  const mapped: CanvasObject[] = doc.objects.map((o) => {
    if (o.id === panel.id) {
      const rest: PanelObject = { ...panel, x: 0, y: 0, w: frame.w, h: frame.h }
      delete rest.crop
      delete rest.rotation
      delete rest.opacity
      delete rest.flipH
      delete rest.flipV
      return rest
    }
    return { ...o, x: (o.x - ox) * sx, y: (o.y - oy) * sy, w: o.w * sx, h: o.h * sy }
  })
  return { ...doc, page: { ...doc.page, w: frame.w, h: frame.h }, objects: mapped }
}

/** 合成文档里的标注部分（非面板、可见）——就是请求里 `original.objects` 要带的那些，坐标已在原图图幅里 */
export function annotationsOf(annotated: FigureDocument): CanvasObject[] {
  return annotated.objects.filter((o) => o.type !== 'panel' && !o.hidden)
}
