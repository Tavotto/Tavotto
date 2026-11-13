import type { PanelSourceFacts } from './api'
import { validFigureOrigin, validFigureSnapshot } from './figureSnapshot'
import { figureSnapshotPreviewIdentity } from './figureSnapshotPreview'
import type { FigureSnapshot, PanelObject, PanelOverride } from '@/types/document'

/** 只读来源事实；不是 inventory、manifest 或可编辑资格。 */
export interface PanelSourceEvidence {
  originalSources: Readonly<Record<string, PanelSourceFacts | null>>
  figureSnapshots: Readonly<Record<string, boolean>>
}

/** 来源基准存在不等于当前 Q 已渲染；这里只证明可读取的不可变附件。 */
export function figureArchiveSourceInput(panel: PanelObject): { reference: FigureSnapshot; patches: PanelOverride[] } | null {
  if (panel.chart !== undefined || panel.chartSource !== undefined) return null
  if (panel.figureOrigin !== undefined && !validFigureOrigin(panel.figureOrigin, panel.fileId)) return null
  if (panel.figureSnapshot !== undefined) return validFigureSnapshot(panel.figureSnapshot, panel.fileId)
    ? { reference: panel.figureSnapshot, patches: panel.overrides } : null
  return panel.figureOrigin === undefined ? null
    : { reference: panel.figureOrigin.snapshot, patches: panel.figureOrigin.patches }
}

export function needsFigureArchiveSource(panel: PanelObject): boolean {
  return panel.figureOrigin !== undefined || (panel.figureSnapshot !== undefined && panel.chart === undefined && panel.chartSource === undefined)
}

/** 同名文件的不同历史、不同原序编辑各自验证；坏 ref 没有可复用键。 */
export function figureSnapshotSourceKey(panel: PanelObject): string | null {
  const input = figureArchiveSourceInput(panel)
  if (!input) return null
  const reference = figureSnapshotPreviewIdentity(input.reference)
  return reference === null ? null : JSON.stringify([reference, input.patches])
}
