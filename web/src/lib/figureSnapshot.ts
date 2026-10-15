import type { FigureSnapshot, PanelObject, ProjectDocument } from '@/types/document'

/** Captured with the exact document sent to disk, before any asynchronous work. */
export interface FigureSnapshotSaveContext {
  documentId: string
  loadSeq: number
  pj: string | null
  document: ProjectDocument
  sourceGenerations: ReadonlyMap<string, number>
  ownerGenerations?: ReadonlyMap<string, number>
  receiptGeneration?: number
}

/** Hash/receipt validity belongs to the backend; the frontend only checks the wire shape and owner. */
export function figureSnapshotFor(panel: PanelObject): FigureSnapshot | undefined {
  return validFigureSnapshot(panel.figureSnapshot, panel.fileId) ? panel.figureSnapshot : undefined
}

export function validFigureSnapshot(value: unknown, sourceId: string): value is FigureSnapshot {
  if (!value || typeof value !== 'object') return false
  const ref = value as Record<string, unknown>
  if (ref.executionOwner !== undefined) {
    if (!ref.executionOwner || typeof ref.executionOwner !== 'object') return false
    const owner = ref.executionOwner as Record<string, unknown>
    if (typeof owner.script !== 'string' || !owner.script ||
        (owner.run_config !== null && (typeof owner.run_config !== 'string' || !owner.run_config))) return false
  }
  return ref.version === 1 && ref.sourceId === sourceId &&
    ['patchHash', 'receiptId', 'snapshotId'].every((key) => typeof ref[key] === 'string' && ref[key].length > 0)
}

/** Geometry changes do not change the captured Figure; source and overrides do. */
export function sameFigureInputs(a: PanelObject, b: PanelObject): boolean {
  return a.fileId === b.fileId && a.fileKind === b.fileKind &&
    JSON.stringify(a.source) === JSON.stringify(b.source) &&
    JSON.stringify(a.overrides) === JSON.stringify(b.overrides)
}
