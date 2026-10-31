import type { FigureSnapshotInvalidation } from '@/lib/api'
import { capturedFigureSources, executedFigureSources, figureExecutionOwnerKey } from '@/lib/capturedFigureSources'
import { currentProjectId, onCurrentProjectChange } from '@/lib/session'
import { registerFigureExecutionConsumer } from '@/lib/figureExecutionEvents'
import { getRestoreGate, invalidateFigureSnapshots, useDocumentStore } from '@/store/documentStore'
import { useRuntimeAssetStore } from '@/store/runtimeAssetStore'
import type { CanvasObject } from '@/types/document'

type ExecutionOwner = Pick<FigureSnapshotInvalidation, 'script' | 'run_config'>
type SourceFact = { sourceId: string } | { receiptId: string } | { owner: ExecutionOwner }
// Accepted execution facts outlive the callback's project epoch while its document is restoring.
// Retain only matching fields, coalesced by project/source, never reports, argv, or one Promise per run.
// Consume after the first actual ready document; later explicit historical opens are not replayed.
const pending = new Map<string | null, Map<string, SourceFact>>()
// Responses/builds can repeat the same execution after a document has been opened explicitly.
// IDs are opaque and project-scoped; keep only these small identities for this application lifetime.
interface AcceptedExecution {
  owner: boolean
  sources: Set<string>
  receipts: Set<string>
  consumed?: { documentId: string; loadSeq: number }
}
const acceptedExecutions = new Map<string, AcceptedExecution>()
export function hasAcceptedFigureExecution(pj: string | null, executionId: string): boolean {
  return acceptedExecutions.has(JSON.stringify([pj, executionId]))
}
const pendingExecutions = new Map<string | null, Set<AcceptedExecution>>()
const currentInstallation = () => {
  const { documentId, loadSeq } = useDocumentStore.getState()
  return { documentId, loadSeq }
}
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
    const installation = currentInstallation()
    for (const execution of pendingExecutions.get(pj) ?? []) execution.consumed = installation
    pendingExecutions.delete(pj)
    applyFacts([...facts.values()], pj)
  })
}

function recordFacts(facts: SourceFact[], pj: string | null, execution: AcceptedExecution): void {
  // Callers already checked their project epoch before accepting trusted execution evidence.
  // Pending accepted A facts survive A→B→A; an unaccepted stale callback must never enter here.
  if (currentProjectId() !== pj) return
  const gate = getRestoreGate()
  // Once initialized, ordinary explicit document opens do not rewrite gate.docId.
  const ready = gate.state === 'ready' && (gate.reason === 'initial' || gate.pj === pj)
  const installation = currentInstallation()
  if (execution.consumed) {
    // New archive bindings for an already-consumed token belong to that document installation.
    // A later explicit historical open is not another chance to replay the earlier execution.
    if (!ready || gate.reason === 'initial' || execution.consumed.documentId !== installation.documentId ||
        execution.consumed.loadSeq !== installation.loadSeq) return
  }
  if (!ready || gate.reason === 'initial') {
    let retained = pending.get(pj)
    if (!retained) pending.set(pj, (retained = new Map()))
    let waiting = pendingExecutions.get(pj)
    if (!waiting) pendingExecutions.set(pj, (waiting = new Set()))
    waiting.add(execution)
    for (const fact of facts) {
      const key = 'sourceId' in fact ? `source:${fact.sourceId}` :
        'receiptId' in fact ? `receipt:${fact.receiptId}` : `owner:${figureExecutionOwnerKey(fact.owner)}`
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
  if (ready) {
    if (gate.reason !== 'initial') execution.consumed = installation
    if (facts.length) applyFacts(facts, pj)
  } else scheduleDrain()
}

/** A backend execution fact is authoritative even when the operation produced no usable figure. */
export function invalidateFigureExecution(execution: FigureSnapshotInvalidation, pj: string | null): void {
  if (currentProjectId() !== pj) return
  const key = JSON.stringify([pj, execution.execution_id])
  let accepted = acceptedExecutions.get(key)
  if (!accepted) acceptedExecutions.set(key, (accepted = { owner: false, sources: new Set(), receipts: new Set() }))
  const known = (useRuntimeAssetStore.getState().assets ?? []).flatMap((a) => a.descriptor ? [a.descriptor] : [])
  const sources = execution.scope === 'owner' && !accepted.owner
    ? executedFigureSources(execution, [], known) : execution.source_ids
  const facts: SourceFact[] = []
  if (execution.scope === 'owner' && !accepted.owner) {
    accepted.owner = true
    facts.push({ owner: { script: execution.script, run_config: execution.run_config } })
  }
  // The same execution can disclose more exact bindings at a later native barrier/build.
  // Replays are inert, but never discard newly confirmed source/receipt IDs with the same token.
  for (const sourceId of sources) if (!accepted.sources.has(sourceId)) {
    accepted.sources.add(sourceId)
    facts.push({ sourceId })
  }
  for (const receiptId of execution.receipt_ids ?? []) if (!accepted.receipts.has(receiptId)) {
    accepted.receipts.add(receiptId)
    facts.push({ receiptId })
  }
  recordFacts(facts, pj, accepted)
}

function applyFacts(facts: readonly SourceFact[], pj: string | null): void {
  if (!facts.length) return
  const state = useDocumentStore.getState()
  const objects = state.buildProject().canvases.flatMap((c) => c.objects)
  // A removed panel may return through undo after an in-flight save responds. Its old source still
  // needs a generation bump, using exact owner/source evidence without deriving ownership from stems.
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
  const ids = facts.flatMap((fact) => 'sourceId' in fact ? [fact.sourceId] : [])
  const receipts = facts.flatMap((fact) => 'receiptId' in fact ? [fact.receiptId] : [])
  const owners = facts.flatMap((fact) => 'owner' in fact ? [fact.owner] : [])
  const sources = new Set(capturedFigureSources([], objects, ids))
  const known = (useRuntimeAssetStore.getState().assets ?? []).flatMap((a) => a.descriptor ? [a.descriptor] : [])
  for (const fact of facts) {
    if (!('owner' in fact)) continue
    for (const source of executedFigureSources({ ...fact.owner, source_ids: [] }, objects, known)) sources.add(source)
  }
  if (owners.length) invalidateFigureSnapshots([...sources], pj, receipts, owners)
  else if (receipts.length) invalidateFigureSnapshots([...sources], pj, receipts)
  else invalidateFigureSnapshots([...sources], pj)
}

registerFigureExecutionConsumer(invalidateFigureExecution)
