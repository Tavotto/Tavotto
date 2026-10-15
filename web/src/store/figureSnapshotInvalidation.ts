import type { CapturedFigureDescriptor } from '@/lib/api'
import { capturedFigureSources, type CapturedFigureSource } from '@/lib/capturedFigureSources'
import { currentProjectId, onCurrentProjectChange } from '@/lib/session'
import { getRestoreGate, invalidateFigureSnapshots, useDocumentStore } from '@/store/documentStore'
import type { CanvasObject } from '@/types/document'

type SourceFact = { sourceId: string } | { captured: CapturedFigureSource }
// Accepted execution facts outlive the callback's project epoch while its document is restoring.
// Retain only matching fields, coalesced by project/source, never reports, argv, or one Promise per run.
// Consume after the first actual ready document; later explicit historical opens are not replayed.
const pending = new Map<string | null, Map<string, SourceFact>>()
let watching = false
let drainQueued = false

function scheduleDrain(): void {
  if (drainQueued || !pending.size) return
  drainQueued = true
  queueMicrotask(() => {
    drainQueued = false
    const pj = currentProjectId()
    const gate = getRestoreGate()
    // supersedeRestore publishes ready before switchDocument installs its body. Run after that
    // synchronous installation, not during installingUnderToken or its final saveState='clean'.
    if (gate.state !== 'ready' || gate.reason === 'initial' || gate.pj !== pj ||
        gate.docId !== useDocumentStore.getState().documentId) return
    const facts = pending.get(pj)
    if (!facts) return
    pending.delete(pj) // Delete before writing: dirty/autosave notifications must not consume twice.
    applyFacts([...facts.values()], pj)
  })
}

function recordFacts(facts: SourceFact[], pj: string | null): void {
  // Callers already checked their execution owner before accepting the successful result.
  // Pending accepted A facts survive A→B→A; an unaccepted stale callback must never enter here.
  if (currentProjectId() !== pj || !facts.length) return
  const gate = getRestoreGate()
  // Once initialized, ordinary explicit document opens do not rewrite gate.docId.
  const ready = gate.state === 'ready' && (gate.reason === 'initial' || gate.pj === pj)
  if (!ready || gate.reason === 'initial') {
    let retained = pending.get(pj)
    if (!retained) pending.set(pj, (retained = new Map()))
    for (const fact of facts) {
      const key = 'sourceId' in fact ? `source:${fact.sourceId}` : `capture:${fact.captured.asset_id}`
      retained.set(key, fact)
    }
    if (!watching) {
      watching = true
      useDocumentStore.subscribe((state, prev) => {
        if (state.restoreGate !== prev.restoreGate || state.loadSeq !== prev.loadSeq) scheduleDrain()
      })
      onCurrentProjectChange(scheduleDrain)
    }
  }
  if (ready) applyFacts(facts, pj)
  else scheduleDrain()
}

/** Rebuilds have an exact source ID; capture entrypoints additionally carry source-matching evidence. */
export function invalidateExecutedFigureSnapshots(sourceIds: readonly string[], pj: string | null): void {
  recordFacts([...new Set(sourceIds)].map((sourceId) => ({ sourceId })), pj)
}

/** Register only an accepted successful execution, not replay, refresh, or an old-attempt response. */
export function invalidateCapturedFigureSnapshots(
  descriptors: readonly CapturedFigureDescriptor[],
  pj: string | null,
): void {
  recordFacts([
    ...descriptors.map((d) => ({ captured: {
      asset_id: d.asset_id, script: d.script, stem: d.stem,
      original_artifact: d.original_artifact, run_config: d.run_config,
    } })),
    // A later capture of the same runtime asset can identify a different original artifact.
    // Keep every observed exact source independently; reuse the sole path-normalization authority.
    ...capturedFigureSources(descriptors, []).map((sourceId) => ({ sourceId })),
  ], pj)
}

function applyFacts(facts: readonly SourceFact[], pj: string | null): void {
  const state = useDocumentStore.getState()
  const objects = state.buildProject().canvases.flatMap((c) => c.objects)
  // A removed panel may return through undo after an in-flight save responds. Its old source still
  // needs a generation bump, including alternative disk formats sharing the captured Figure stem.
  const seen = new WeakSet<object>()
  const appendHistoricalPanels = (value: unknown): void => {
    if (!value || typeof value !== 'object' || seen.has(value)) return
    seen.add(value)
    const object = value as Record<string, unknown>
    if (object.type === 'panel' && typeof object.fileId === 'string' && Array.isArray(object.overrides)) {
      objects.push(object as unknown as CanvasObject)
      return
    }
    for (const child of Object.values(object)) appendHistoricalPanels(child)
  }
  appendHistoricalPanels([state.past, state.future, state.canvasSessions, state.txn])
  const descriptors = facts.flatMap((fact) => 'captured' in fact ? [fact.captured] : [])
  const ids = facts.flatMap((fact) => 'sourceId' in fact ? [fact.sourceId] : [])
  invalidateFigureSnapshots(capturedFigureSources(descriptors, objects, ids), pj)
}
