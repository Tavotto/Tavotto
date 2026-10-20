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

export function embedsDiskBitmap(
  o: PanelObject,
  assets: Record<string, PanelInfo>,
  scope: ExportRouteScope,
  /** 本次所选格式；只有原图范围读它 */
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
