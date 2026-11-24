import type { PanelInfo } from './api'
import { isAdoptedPanel, isSourceBackedZeroEdit, isUnadoptedPngSource } from './artifactValidation'
import { embedsDiskBitmap, type ExportRoute } from './exportRoute'
import { figureOriginFor } from './figureSnapshot'
import type { CanvasObject, PanelObject } from '@/types/document'

/**
 * 画布导出里，哪些面板会把磁盘上的整张位图嵌进去、却其实有脚本能把它重画成矢量（T-005）。
 *
 * 只认「没动过、还没采用的 PNG + 素材清单里有脚本」——`prepareScriptedBitmapsForRedraw` 能处理的正好是这些。
 * 已采用的面板画布导出本来就由脚本重画；带编辑的面板不会嵌原件位图；没脚本的只能照原样。
 * 来源判据只读 `embedsDiskBitmap`（route 在手读 route，否则走它的退路），不在这里另推一遍。
 * 有无脚本看 `assets[fileId].script`，与 `embedsDiskBitmap` 的退路同源。
 */
export function scriptedBitmapPanels(
  objects: readonly CanvasObject[],
  assets: Record<string, PanelInfo>,
  formats: readonly string[],
  route?: ExportRoute | null,
): PanelObject[] {
  return objects.filter((o): o is PanelObject =>
    o.type === 'panel' &&
    !o.hidden &&
    figureOriginFor(o) === undefined &&
    !isAdoptedPanel(o) &&
    isUnadoptedPngSource(o) &&
    !!assets[o.fileId]?.script &&
    embedsDiskBitmap(o, assets, 'canvas', formats, route),
  )
}

/**
 * 画布上给不给「用脚本重画」角标（T-005）：画布此刻显示的是磁盘上的**位图原件**，而它有脚本能重画。
 *
 * 两种：还没采用、没动过的 PNG 源图；来源驱动采用过、零编辑、仍显示原像素的（`showsRedraw` 没开）。
 * 精确采用 / 换成矢量主文件 / 带编辑的面板显示的已是引擎图，不给。只读文档，不读文件、不执行任何东西
 * （打开项目零执行，ADR 0013 §6）。历史版本（`figureOrigin`）按它自己的执行重画，不给。
 */
export function redrawOffered(o: PanelObject): boolean {
  if (o.hidden || o.fileKind !== 'raster' || !o.script || figureOriginFor(o) !== undefined) return false
  return isUnadoptedPngSource(o) || isSourceBackedZeroEdit(o)
}
