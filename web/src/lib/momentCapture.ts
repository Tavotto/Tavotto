import type { DocumentFormat } from '@/lib/documentFormat'
import type { FigureDocument } from '@/types/document'

/** Shared by the synchronous source freezer and the thumbnail compositor. */
export const TIMELINE_THUMB_PX = 360
export type PanelSource = { svg?: string; url?: string; png?: Promise<string | null> } | null
export type ThumbSources = ReadonlyMap<string, PanelSource> | null

export interface MomentDocumentState {
  doc: FigureDocument
  activeCanvasId: string
  documentId: string
  documentFormat: DocumentFormat
  projectMeta: { id: string; name: string; createdAt: number }
}

/** The same context key is consumed by the timeline and the save writer. */
export const timelineCtxKey = (gen: number, docId: string, format?: DocumentFormat) =>
  format === 'v4' ? JSON.stringify([gen, format, docId]) : `${gen}:${docId}`

export function activeCanvasIdentityOf(state: MomentDocumentState, pj: string | null) {
  return {
    documentId: state.documentId,
    format: state.documentFormat,
    versionIdentity: {
      project: { id: state.projectMeta.id, name: state.projectMeta.name },
      canvasId: state.activeCanvasId,
      createdAt: state.projectMeta.createdAt,
      updatedAt: Date.now(),
    },
    pj,
    doc: state.doc,
    canvasId: state.activeCanvasId,
    canvasName: state.doc.name,
  }
}

export interface MomentSnapshot {
  readonly ctx: string
  readonly identity: ReturnType<typeof activeCanvasIdentityOf>
  readonly thumb: ThumbSources
  readonly thumbDoc?: FigureDocument
  readonly bakedInto?: ReadonlyMap<string, string>
}

type SourceCapture = (doc: FigureDocument) => ThumbSources
let captureSources: SourceCapture | null = null

/** The real renderer supplies this synchronous port at module initialization. */
export function installMomentSources(capture: SourceCapture): void {
  if (typeof capture !== 'function') throw new TypeError('A synchronous moment source capture is required.')
  captureSources = capture
}

export class MomentCaptureUnavailable extends Error {
  constructor() {
    super('Moment sources have not been initialized.')
    this.name = 'MomentCaptureUnavailable'
  }
}

/** Freeze identity and actual source references now, before any writer await. */
export function captureDocumentMoment(
  state: MomentDocumentState,
  pj: string | null,
  gen: number,
  doc?: FigureDocument,
): MomentSnapshot {
  if (!captureSources) throw new MomentCaptureUnavailable()
  const identity = activeCanvasIdentityOf(state, pj)
  if (doc) identity.doc = doc
  return {
    ctx: timelineCtxKey(gen, identity.documentId, identity.format),
    identity,
    thumb: captureSources(identity.doc),
  }
}
