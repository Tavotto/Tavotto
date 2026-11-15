import { msg, t as translate, type UiMessage } from '@/i18n'
import { fileUrl, importSvg, type SvgImportResult } from '@/lib/api'
import { importAndPlace, pastedImageName, reportImageResult } from '@/lib/canvasFileIntake'
import { firstReason, placeImported } from '@/lib/svgImport'
import { RASTER_DPI, rasterizeSvg } from '@/lib/svgRaster'
import { groupPathOf, setGroupPath } from '@/lib/groupTree'
import { scaleObject } from '@/lib/transform'
import type { CanvasObject, PanelObject } from '@/types/document'
import { panelRotation } from '@/types/document'
import { useDocumentStore } from './documentStore'
import { finishActiveGesture } from './gestureCoordinator'
import { useSelectionStore } from './selectionStore'
import { useUiStore } from './uiStore'

/**
 * 外部 SVG 落成可编辑对象（ADR 0189）。两个入口：
 *
 * - **粘贴 SVG 标记**（从 Illustrator / Inkscape 复制）：变成路径、文字、组、剪切组，一次 commit；做不准（滤镜、图片……）或
 *   后端读不了 → 整个 SVG 当图片放进来（与粘贴图片文件同一条路），说一句原因。
 * - **「拆成可编辑对象」**：画布上一张静态 SVG 图（没有脚本、没有裁剪 / 旋转 / 翻转）拆成对象，按图在画布上的大小缩放、
 *   原位替换这张图；做不准 → 图不动，说一句原因。拖进来的 SVG 文件照旧是「图」（matplotlib 导出的 SVG 多半要当图排版）。
 */

/**
 * 被受限解析器当成「可能是攻击 / 资源滥用」拒掉的（超大、超时、引用环、坏 XML）：不再交给浏览器栅格化——那一步也要把它完整
 * 读一遍。只有「画不准」的（滤镜、图片、外链……）才退到位图
 */
const NO_RASTER: ReadonlySet<string> = new Set(['too_large', 'too_many_elements', 'timeout', 'reference_cycle', 'bad_xml', 'not_svg', 'script'])

const hist = (key: string, values?: Record<string, unknown>): UiMessage => msg(`history.${key}`, values, 'workspace')
const svgStatus = (key: string, values: Record<string, unknown>, tone: 'info' | 'done') =>
  useUiStore.getState().setStatus(msg(`status.${key}`, values, 'workspace'), tone)
const reasonText = (code: string | null) =>
  translate(`svgImport.reason.${code ?? 'unreadable'}`, { ns: 'workspace', defaultValue: translate('svgImport.reason.unreadable', { ns: 'workspace' }) })

async function runImport(svg: string): Promise<{ res: SvgImportResult | null; reason: string | null }> {
  try {
    const res = await importSvg(svg)
    if (!res.ok) return { res, reason: res.error ?? null }
    const reason = firstReason(res.unsupported)
    return { res, reason }
  } catch {
    return { res: null, reason: null }
  }
}

/** 粘贴进来的 SVG 标记 → 可编辑对象；做不准就整个当图片放。返回落成了什么 */
export async function pasteSvgText(svg: string): Promise<'objects' | 'image'> {
  finishActiveGesture()
  const { res, reason } = await runImport(svg)
  const doc = useDocumentStore.getState().doc
  const size = res?.size_mm ?? [0, 0]
  const placed =
    res?.ok && !reason && res.objects
      ? placeImported(res.objects, { x: Math.max(0, (doc.page.w - size[0]) / 2), y: Math.max(0, (doc.page.h - size[1]) / 2) })
      : null
  if (!placed) {
    // 与粘贴图片文件同一条路（复制进项目文件夹成为素材 → 放上画布）；那条路自己没放成（没有项目文件夹、素材检查不过）
    // 就由它说它的原因，放成了才说「为什么是图片」
    const now = new Date()
    const file = new File([svg], 'pasted.svg', { type: 'image/svg+xml' })
    const why = reasonText(res?.ok ? reason : (reason ?? 'unreadable'))
    const nameOf = (f: File, i: number) => pastedImageName({ name: '', type: f.type }, now, i)
    const r = await importAndPlace([file], { nameOf })
    if (r.aborted) return 'image'
    if (r.placed.length) {
      svgStatus('svgAsImage', { reason: why }, 'info')
      return 'image'
    }
    // 项目里的 SVG 素材检查也不收（滤镜、外链……RenderCore 画不准）：按浏览器画出来的样子栅格化成 PNG 放进来
    const code = res?.ok ? reason : (res?.error ?? reason ?? 'unreadable')
    const png = code && NO_RASTER.has(code) ? null : await rasterizeSvg(svg, res?.size_mm ?? [0, 0])
    const r2 = png ? await importAndPlace([new File([png], 'pasted.png', { type: 'image/png' })], { nameOf }) : null
    if (r2?.placed.length) svgStatus('svgAsRaster', { reason: why, dpi: RASTER_DPI }, 'info')
    else reportImageResult(r2 && !r2.aborted ? r2 : r)
    return 'image'
  }
  useDocumentStore.getState().commit(hist('importSvg', { count: placed.length }), (d) => {
    d.objects.push(...placed)
  })
  useSelectionStore.getState().set(placed.map((o) => o.id))
  svgStatus('svgImported', { count: placed.length }, 'done')
  return 'objects'
}

/** 这张图能不能拆成可编辑对象：静态 SVG（没有脚本），没有裁剪 / 旋转 / 翻转 */
export function canExplodeSvg(o: CanvasObject | undefined): o is PanelObject {
  if (!o || o.type !== 'panel') return false
  const p = o as PanelObject
  return p.fileKind === 'svg' && !p.script && !p.crop && !panelRotation(p) && !p.flipH && !p.flipV && !p.locked
}

/** 拆成可编辑对象：按图在画布上的大小缩放、原位替换（一次 commit）；做不准 = 图不动、说原因 */
export async function explodeSvgPanel(id: string): Promise<boolean> {
  const panel = useDocumentStore.getState().doc.objects.find((o) => o.id === id)
  if (!canExplodeSvg(panel)) return false
  finishActiveGesture()
  let svg = ''
  try {
    const r = await fetch(fileUrl(panel.fileId))
    svg = r.ok ? await r.text() : ''
  } catch {
    svg = ''
  }
  const { res, reason } = svg ? await runImport(svg) : { res: null, reason: null }
  const placed = res?.ok && !reason && res.objects ? placeImported(res.objects, { x: 0, y: 0 }) : null
  const size = res?.size_mm
  if (!placed || !size || !(size[0] > 0) || !(size[1] > 0)) {
    svgStatus('svgExplodeFailed', { reason: reasonText(reason ?? 'unreadable') }, 'info')
    return false
  }
  const kx = panel.w / size[0]
  const ky = panel.h / size[1]
  // 图本来在某个组里：拆出来的对象接着待在这一层（SVG 自己的组挂在它下面）
  const prefix = groupPathOf(panel)
  const fitted = placed.map((o) => {
    const s = scaleObject(o, kx, ky, [0, 0], { strokes: true, text: 'size' })
    const out = { ...s, x: s.x + panel.x, y: s.y + panel.y }
    if (prefix.length) setGroupPath(out, [...prefix, ...groupPathOf(out)])
    return out
  })
  useDocumentStore.getState().commit(hist('explodeSvg'), (d) => {
    const i = d.objects.findIndex((o) => o.id === id)
    if (i < 0) return
    d.objects.splice(i, 1, ...fitted)
  })
  useSelectionStore.getState().set(fitted.map((o) => o.id))
  svgStatus('svgImported', { count: fitted.length }, 'done')
  return true
}
