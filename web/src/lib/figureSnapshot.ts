import { figureExecutionProofFor } from '@/lib/figureExecutionProof'
import type { FigureOrigin, FigureSnapshot, PanelObject, ProjectDocument } from '@/types/document'

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

/** Shape only: ordered patch semantics/hash and execution authority belong to the backend. */
export function validFigureOrigin(value: unknown, sourceId: string): value is FigureOrigin {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false
  const origin = value as Record<string, unknown>
  if (Object.keys(origin).some(key => !['version', 'snapshot', 'patches'].includes(key)) ||
      origin.version !== 1 || Array.isArray(origin.snapshot) || !validFigureSnapshot(origin.snapshot, sourceId) || !Array.isArray(origin.patches)) return false
  const ref = origin.snapshot
  if (Object.keys(ref).some(key => !['version', 'sourceId', 'patchHash', 'receiptId', 'snapshotId', 'executionOwner'].includes(key))) return false
  if (ref.executionOwner && (Array.isArray(ref.executionOwner) || Object.keys(ref.executionOwner).some(key => !['script', 'run_config'].includes(key)))) return false
  return origin.patches.every(patch => patch && typeof patch === 'object' && !Array.isArray(patch) &&
    typeof patch.gid === 'string' && patch.gid.length > 0 && typeof patch.prop === 'string' && patch.prop.length > 0 &&
    Object.hasOwn(patch, 'value') && (!Object.hasOwn(patch, 'identity') || (typeof patch.identity === 'string' && patch.identity.length > 0)))
}

/** Member order is immaterial; array order, duplicate patches and every value participate. */
export function figureOriginIdentity(value: unknown): string {
  const canonical = (item: unknown): unknown => Array.isArray(item) ? item.map(canonical)
    : item && typeof item === 'object' ? Object.fromEntries(Object.keys(item).sort()
      .map(key => [key, canonical((item as Record<string, unknown>)[key])])) : item
  return JSON.stringify(canonical(value))
}

export function sameFigureOrigin(expected: unknown, actual: unknown): boolean {
  return figureOriginIdentity(expected) === figureOriginIdentity(actual)
}

/**
 * Only the explicit historical input is an origin.
 *
 * `figureSnapshot` is the **output** archive of some past render; it does not
 * say which execution produced it, so it must never be promoted into a
 * historical edit input. A panel with only a snapshot keeps the ordinary
 * "run the current source" path (the baseline behaviour).
 */
export function figureOriginFor(panel: Pick<PanelObject, 'fileId' | 'figureOrigin'>): FigureOrigin | undefined {
  return panel.figureOrigin !== undefined ? panel.figureOrigin : undefined
}

/**
 * Capture the pre-edit baseline P only when the backend proved that this exact
 * render produced the snapshot we are about to detach from.
 *
 * The proof is per-render evidence (`figure_execution`), not a "current
 * receipt" read after the fact: source id, receipt id, patch hash, owner and
 * the exact ordered override list all have to match the panel's own snapshot
 * and pre-edit inputs.
 */
export function verifiedFigureOrigin(panel: PanelObject, pj: string | null): FigureOrigin | undefined {
  const snapshot = panel.figureSnapshot
  if (snapshot === undefined || !validFigureSnapshot(snapshot, panel.fileId)) return undefined
  const proof = figureExecutionProofFor(pj, panel.fileId, panel.overrides)
  if (proof === undefined) return undefined
  if (proof.sourceId !== panel.fileId || proof.receiptId !== snapshot.receiptId ||
      proof.patchHash !== snapshot.patchHash) return undefined
  if (figureOriginIdentity(proof.executionOwner) !== figureOriginIdentity(snapshot.executionOwner)) return undefined
  // Immer drafts are not structured-cloneable; JSON is the frozen pre-edit input.
  return {
    version: 1,
    snapshot: JSON.parse(JSON.stringify(snapshot)) as FigureSnapshot,
    patches: JSON.parse(JSON.stringify(panel.overrides)) as FigureOrigin['patches'],
  }
}

export function sameFigureSource(a: PanelObject, b: PanelObject): boolean {
  return a.fileId === b.fileId && a.fileKind === b.fileKind &&
    JSON.stringify(a.source) === JSON.stringify(b.source)
}

export function figureOriginFields(panel: Parameters<typeof figureOriginFor>[0]): { figure_origin?: FigureOrigin } {
  const origin = figureOriginFor(panel)
  return origin === undefined ? {} : { figure_origin: structuredClone(origin) }
}

/** Geometry changes do not change the captured Figure; source and overrides do. */
export function sameFigureInputs(a: PanelObject, b: PanelObject): boolean {
  return sameFigureSource(a, b) && sameFigureOrigin(figureOriginFor(a), figureOriginFor(b)) &&
    JSON.stringify(a.overrides) === JSON.stringify(b.overrides)
}
