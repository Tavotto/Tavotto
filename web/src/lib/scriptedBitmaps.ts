import type { PanelFontFacts, PanelInfo } from './api'
import { isAdoptedPanel, isSourceBackedZeroEdit, isUnadoptedPngSource } from './artifactValidation'
import { embedsDiskBitmap, type ExportRoute } from './exportRoute'
import { figureOriginFor } from './figureSnapshot'
import type { CanvasObject, PanelObject, PanelOverride } from '@/types/document'

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
 * 另有 PDF 原件：零编辑、且原件的字体事实（`facts`，后端只读文件得出）说它用了 Type 3 或有缺字。
 * 精确采用 / 换成矢量主文件 / 带编辑的面板显示的已是引擎图，不给。只读文档，不读文件、不执行任何东西
 * （打开项目零执行，ADR 0013 §6）。历史版本（`figureOrigin`）按它自己的执行重画，不给。
 */
export function redrawOffered(o: PanelObject, facts?: PanelFontFacts): boolean {
  if (o.hidden || !o.script || figureOriginFor(o) !== undefined) return false
  if (o.fileKind === 'pdf') return pdfRedrawCandidate(o) && pdfFontProblem(facts) !== null
  if (o.fileKind !== 'raster') return false
  return isUnadoptedPngSource(o) || isSourceBackedZeroEdit(o)
}

/**
 * PDF 原件改由脚本重画的那一条 override：`figure.frame = "savefig"`——按脚本自己存盘的图幅出图（ADR 0098），
 * 与磁盘上那份 PDF 同一个框，所以版面不动；它本身什么都不改，只让面板「有图内修改」→ 画布与导出都走引擎重画
 * （`figureFrameMigration.renderedByEngine`、引擎 `exportroute.wants_execution` 同一条判据）。撤销 / 重置即回原件。
 */
export const REDRAW_FRAME_OVERRIDE: Readonly<PanelOverride> = Object.freeze({ gid: 'figure', prop: 'frame', value: 'savefig' })

/**
 * 有脚本、零编辑的 PDF 原件（T-005 补充）：画布显示的是磁盘上那份 PDF 本身。
 * 只看文档；给不给角标还要看原件的字体事实（`pdfFontProblem`）——好好的 PDF 原件不打扰。
 */
export function pdfRedrawCandidate(o: PanelObject): boolean {
  return o.fileKind === 'pdf' && !o.hidden && !!o.script && figureOriginFor(o) === undefined && o.overrides.length === 0
}

/** 一组对象里的 PDF 原件候选（`pdfRedrawCandidate`）：导出结果「用脚本重画并重新导出」先问它们的字体事实 */
export function pdfRedrawPanels(objects: readonly CanvasObject[]): PanelObject[] {
  return objects.filter((o): o is PanelObject => o.type === 'panel' && pdfRedrawCandidate(o))
}

/** 原件的字体问题（角标文案键，workspace:panelBadge.*）：缺字压过 Type 3（缺字是看得见的错，Type 3 只是不好改） */
export function pdfFontProblem(facts?: PanelFontFacts): 'pdfMissingGlyphs' | 'pdfType3' | null {
  if (!facts?.readable) return null
  if (facts.missing_glyphs) return 'pdfMissingGlyphs'
  if (facts.type3) return 'pdfType3'
  return null
}
