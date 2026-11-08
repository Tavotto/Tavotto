import type { PanelSourceFacts } from './api'
import { validFigureSnapshot } from './figureSnapshot'
import { figureSnapshotPreviewIdentity } from './figureSnapshotPreview'
import type { PanelObject } from '@/types/document'

/** 只读来源事实；不是 inventory、manifest 或可编辑资格。 */
export interface PanelSourceEvidence {
  originalSources: Readonly<Record<string, PanelSourceFacts | null>>
  figureSnapshots: Readonly<Record<string, boolean>>
}

/** 同名文件的不同历史、不同原序编辑各自验证；坏 ref 没有可复用键。 */
export function figureSnapshotSourceKey(panel: PanelObject): string | null {
  if (!validFigureSnapshot(panel.figureSnapshot, panel.fileId)) return null
  const reference = figureSnapshotPreviewIdentity(panel.figureSnapshot)
  return reference === null ? null : JSON.stringify([reference, panel.overrides])
}
