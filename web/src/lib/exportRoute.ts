import type { PanelInfo } from './api'
import { originalArtifactOverrides } from './artifactValidation'
import type { PanelObject } from '@/types/document'

/** 这次导出落在哪个范围：画布合成，还是单图「原图」导出。 */
export type ExportRouteScope = 'canvas' | 'original'

/**
 * 面板在**本次导出**里嵌入的，是不是磁盘上那张位图（而不是脚本重画出的矢量）。
 *
 * 前端唯一的这条判据；预检的面板 `kind` 读它，不再看文件后缀。它镜像后端两处真值，
 * 严格同源对由 `tests/golden/export_route_vectors.json` 看护（后端真实代码求期望：
 * `tests/test_export_route_vectors.py`；本函数：`exportRoute.test.ts`）：
 *
 * - 原图范围：`app._resolve_panel_source`——发给后端的 overrides 非空且注册表有脚本，
 *   就由 worker 重画成临时 PDF；没脚本退回磁盘文件。发给后端的是
 *   `originalArtifactOverrides()`（受守卫 PNG 仅 FRAME = 未编辑 = 空）。守卫无效时
 *   这次导出本身会被拒，按位图口径（保守，不抛）。
 * - 画布范围：`rendercore.sources.needs_execution`——overrides 非空（原样发，含仅 FRAME）
 *   就要 worker 现画；没有脚本时后端报 `source_needs_execution` 而不是退回磁盘文件，
 *   所以画布范围下「有无脚本」不影响结论。
 *
 * - 原图范围还受**所选格式**影响：要 EPS 且不要 SVG 时后端 `_export_produce_original` 传
 *   `rerender=True`（`app._original_rerender`），有脚本就重画，哪怕没有 override——此时
 *   未编辑的 PNG 嵌入的也是重画的矢量。画布范围与格式无关。
 *
 * 有无脚本看 `assets[fileId].script`，与 `epsAvailability` 同源，不读文档里可能陈旧的 `o.script`。
 */
/** 镜像 `app._original_rerender`：原图范围里所选格式是否让有脚本的图现画（要 EPS 且不要 SVG） */
export function originalRerenders(formats: readonly string[]): boolean {
  return formats.includes('eps') && !formats.includes('svg')
}

function legacyEmbedsDiskBitmap(
  o: PanelObject,
  assets: Record<string, PanelInfo>,
  scope: ExportRouteScope,
  formats: readonly string[],
): boolean {
  if (o.fileKind !== 'raster') return false
  if (scope === 'canvas') return o.overrides.length === 0
  let sent = o.overrides
  try {
    sent = originalArtifactOverrides(o)
  } catch {
    return true
  }
  const hasScript = !!assets[o.fileId]?.script
  return !hasScript || (sent.length === 0 && !originalRerenders(formats))
}

/* ------------------------- 后端发布的 route（#1209） -------------------------- */

/** 一种格式的做法（`engine/exportroute.py`）：copy 原件逐字复制 / convert 同一份原件转换 / redraw 脚本重画 / unavailable 带原因码 */
export interface RouteFormat {
  how: 'copy' | 'convert' | 'redraw' | 'unavailable'
  reason?: string
  conditions?: string[]
}

export interface PanelRoute {
  source: 'disk' | 'redraw' | 'snapshot' | 'unavailable'
  embeds_disk_bitmap: boolean
  formats: Record<string, RouteFormat>
  /** EPS 给不给得出——不论用户勾没勾 */
  offers?: { eps?: RouteFormat }
}

/** `/api/export/validate` 响应里的 `route`。缺它（旧后端 / 后端算不出 / 校验没回来）= 没有 route，见 `embedsDiskBitmap` 的退路 */
export interface ExportRoute {
  scope: ExportRouteScope
  formats: string[]
  panels: Record<string, PanelRoute>
}

const sameSet = (a: readonly string[], b: readonly string[]) =>
  a.length === b.length && a.every((f) => b.includes(f))

/**
 * 取某个面板在这份 route 里的那一项。**取不到就是 null**（调用方走退路）：
 * route 不是这个范围的、所选格式与算 route 时不同（格式决定来源：要 EPS 不要 SVG 会让有脚本的图重画）、
 * 或同一张图在画布上摆了好几个实例且各自 route 不同（后端以 `id#n` 区分，这里分不出是哪一个，不替它们裁决）。
 */
export function panelRouteOf(
  route: ExportRoute | null | undefined,
  scope: ExportRouteScope,
  id: string,
  formats: readonly string[],
): PanelRoute | null {
  if (!route || route.scope !== scope || !sameSet(route.formats, formats)) return null
  if (Object.keys(route.panels).some((k) => k.startsWith(`${id}#`))) return null
  return route.panels[id] ?? null
}

/**
 * 面板在**本次导出**里嵌入的，是不是磁盘上那张位图（而不是脚本重画出的矢量）。前端读来源的唯一入口。
 *
 * * 有后端 route（`route` 且对得上范围 / 格式 / 面板）：**只读它**，前端不推算。
 * * 没有（validate 还没回来、旧后端、算不出）：退回下面的 `legacyEmbedsDiskBitmap`——它是后端判据的镜像，
 *   严格同源对由金向量看护。退路**不比旧行为乐观**（就是旧行为），且 route 一到就被替换。
 *   这条退路是前端里**唯一**还在推算来源的地方。
 */
export function embedsDiskBitmap(
  o: PanelObject,
  assets: Record<string, PanelInfo>,
  scope: ExportRouteScope,
  /** 本次所选格式；只有原图范围读它 */
  formats: readonly string[],
  route?: ExportRoute | null,
): boolean {
  const fromRoute = panelRouteOf(route, scope, o.fileId, formats)
  if (fromRoute) return fromRoute.embeds_disk_bitmap
  return legacyEmbedsDiskBitmap(o, assets, scope, formats)
}

/** 这个面板的 route 是否说「画不出」（画布范围：带编辑的图找不到脚本，后端会报 `source_needs_execution`） */
export function unavailablePanels(route: ExportRoute | null | undefined, formats: readonly string[]): string[] {
  if (!route || route.scope !== 'canvas' || !sameSet(route.formats, formats)) return []
  return Object.entries(route.panels)
    .filter(([, p]) => p.source === 'unavailable')
    .map(([id]) => id.replace(/#\d+$/, ''))
}

/**
 * 「原图范围这次会不会照抄源位图」（对话框的像素预览 / 透明背景开关读它）。
 * route 在手：读它；没有 route：`embedsDiskBitmap` 的退路——它把「要 EPS 且不要 SVG 时有脚本的图会被重画」也算进去
 * （旧的对话框内联判据漏了这一条，会对着一张即将被重画的图报源像素网格）。
 */
export function copiesSourceVerbatim(args: {
  scope: ExportRouteScope
  specIsRaster: boolean
  panel: PanelObject | null
  assets: Record<string, PanelInfo>
  formats: readonly string[]
  route?: ExportRoute | null
  /** 面板的受守卫 PNG 没有问题；有问题时本次导出会被拒，不承诺照抄 */
  guardOk: boolean
}): boolean {
  if (args.scope !== 'original' || !args.specIsRaster) return false
  if (!args.panel) return true
  if (!args.guardOk) return false
  return embedsDiskBitmap(args.panel, args.assets, 'original', args.formats, args.route)
}

/** EPS 为什么不可用（与 `exportRequest.EpsBlockReason` 同一个闭集）；后端原因码 → 界面的那一句 */
export function epsFromRoute(
  route: ExportRoute | null | undefined,
  scope: ExportRouteScope,
  id: string,
): { ok: boolean; reason: 'none' | 'canvas_scope' | 'no_script' } | null {
  // offers.eps 与所选格式无关（后端同一结论），只认范围
  if (!route || route.scope !== scope) return null
  const offer = route.panels[id]?.offers?.eps
  if (!offer) return null
  if (offer.how !== 'unavailable') return { ok: true, reason: 'none' }
  if (offer.reason === 'eps_not_for_canvas') return { ok: false, reason: 'canvas_scope' }
  return { ok: false, reason: 'no_script' }
}
