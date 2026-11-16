import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { literal } from '@/i18n'
import { clearFigureExecutionProofs, recordFigureExecutionProof } from '@/lib/figureExecutionProof'
import { migrateFigureFrames } from '@/lib/figureFrameMigration'
import { currentProjectId, setCurrentProjectId } from '@/lib/session'
import { emptyProject, type FigureOrigin, type FigureSnapshot, type PanelObject } from '@/types/document'
import { captureFigureSnapshotSave, mergeSavedFigureSnapshots, saveNow, useDocumentStore } from './documentStore'
import { restoreLayoutVersion } from './actions'

const state = () => useDocumentStore.getState()
const baseline = [{ gid: 'line', prop: 'linewidth', value: 2 }, { gid: 'line', prop: 'linewidth', value: 3 }]
const owner = { script: 'plot.py', run_config: null }
const reference: FigureSnapshot = { version: 1, sourceId: 'History.pdf', patchHash: 'patch-P', receiptId: 'receipt-A', snapshotId: 'snapshot-A', executionOwner: owner }
const panel = (): PanelObject => ({ id: 'p', type: 'panel', fileId: 'History.pdf', fileKind: 'pdf',
  figureFrame: 1, x: 0, y: 0, w: 80, h: 60, nativeW: 80, nativeH: 60,
  overrides: structuredClone(baseline), figureSnapshot: { ...reference } })
const current = () => state().doc.objects[0] as PanelObject
const origin: FigureOrigin = { version: 1, snapshot: reference, patches: baseline }
/** The backend proof for the render which produced `reference` with `baseline`. */
const prove = (ref: FigureSnapshot = reference, patches = baseline) =>
  recordFigureExecutionProof({
    version: 1, sourceId: ref.sourceId, receiptId: ref.receiptId,
    executionOwner: ref.executionOwner ?? owner, patchHash: ref.patchHash,
  }, { pj: currentProjectId(), fileId: ref.sourceId, overrides: patches })

beforeEach(async () => {
  clearFigureExecutionProofs()
  localStorage.clear()
  setCurrentProjectId(null)
  vi.stubGlobal('fetch', vi.fn(async (_url: unknown, init?: RequestInit) => new Response(JSON.stringify(
    init?.method === 'PUT' ? { ok: true, saved_at: 1, revision: 'saved' } : {},
  ), { status: init?.method === 'PUT' ? 200 : 404 })))
  await state().switchDocument(emptyProject(), `origin-${Math.random()}`)
  state().commit(literal('panel'), d => { d.objects.push(panel()) })
  await saveNow()
})
afterEach(async () => { await saveNow(); vi.unstubAllGlobals() })

describe('historical Figure input survives the original document writer', () => {
  it('restores another same-file historical baseline through the actual version action and undo/redo', () => {
    prove()
    state().commit(literal('edit A'), d => { (d.objects[0] as PanelObject).overrides[1].value = 12 })
    const version = structuredClone(state().doc)
    const b = version.objects[0] as PanelObject
    b.overrides = [{ gid: 'line', prop: 'linewidth', value: 8 }]
    b.figureSnapshot = { ...reference, receiptId: 'receipt-B', snapshotId: 'snapshot-B', patchHash: 'patch-B' }
    // A restored body owns its own explicit P; it is never derived from its output snapshot.
    const expected: FigureOrigin = { version: 1, snapshot: b.figureSnapshot, patches: b.overrides }
    b.figureOrigin = expected
    restoreLayoutVersion(literal('restore B'), version)
    expect(current().figureOrigin).toEqual(expected)
    expect(current().figureSnapshot).toEqual(b.figureSnapshot)
    state().undo()
    expect(current().figureOrigin).toEqual(origin)
    expect(current().overrides[1].value).toBe(12)
    state().redo()
    expect(current().figureOrigin).toEqual(expected)
    expect(current().overrides).toEqual(b.overrides)
  })
  it('captures the pre-edit ordered baseline once, retaining it through undo, redo and reopen', async () => {
    prove()
    const documentId = state().documentId
    state().commit(literal('edit'), d => { (d.objects[0] as PanelObject).overrides[1].value = 12 })
    expect(current()).toMatchObject({ figureOrigin: origin })
    expect(current().figureSnapshot).toBeUndefined()
    expect(state().documentId).toBe(documentId)
    expect(state().documentFormat).toBe('legacy')
    state().undo()
    expect(current().overrides).toEqual(baseline)
    // Undo returns to the pre-edit body: its snapshot is enough, and the panel
    // is not made to look historical until the edit is actually applied again.
    expect(current()).not.toHaveProperty('figureOrigin')
    expect(current().figureSnapshot).toEqual(reference)
    state().redo()
    expect(current().overrides[1].value).toBe(12)
    expect(current()).toMatchObject({ figureOrigin: origin })
    const persisted = JSON.parse(JSON.stringify(state().buildProject()))
    await state().switchDocument(persisted, 'reopened-origin')
    expect(current()).toMatchObject({ figureOrigin: origin })
    state().commit(literal('second edit'), d => { (d.objects[0] as PanelObject).overrides[1].value = 15 })
    expect(current()).toMatchObject({ figureOrigin: origin })
  })

  it('keeps a cancelled transaction untouched and never borrows a replaced source', () => {
    prove()
    state().beginTxn(literal('drag'))
    state().txnUpdate(d => { (d.objects[0] as PanelObject).overrides = [] })
    state().endTxn({ discard: true })
    expect(current().overrides).toEqual(baseline)
    // Discarding returns to the pre-edit body: the snapshot is still its output
    // archive and no historical input was committed.
    expect(current()).not.toHaveProperty('figureOrigin')
    expect(current().figureSnapshot).toEqual(reference)
    state().commit(literal('replace'), d => { (d.objects[0] as PanelObject).fileId = 'Other.pdf' })
    expect(current()).not.toHaveProperty('figureOrigin')
    expect(current().figureSnapshot).toBeUndefined()
    state().undo()
    expect(current()).toMatchObject({ fileId: 'History.pdf' })
    expect(current().figureSnapshot).toEqual(reference)
  })

  it('never invents a baseline for a snapshotless edit or borrows a replaced source', () => {
    state().commit(literal('no evidence'), d => { delete (d.objects[0] as PanelObject).figureSnapshot })
    state().commit(literal('edit'), d => { (d.objects[0] as PanelObject).overrides = [] })
    expect(current()).not.toHaveProperty('figureOrigin')
    state().commit(literal('different source'), d => {
      const o = d.objects[0] as PanelObject
      o.figureSnapshot = reference
      o.fileId = 'Other.pdf'
      o.overrides = []
    })
    expect(current()).not.toHaveProperty('figureOrigin')
  })

  it('never promotes an unproved snapshot or a proof for another patch list', () => {
    // No backend proof at all: the snapshot is only an output archive.
    state().commit(literal('edit'), d => { (d.objects[0] as PanelObject).overrides[1].value = 12 })
    expect(current()).not.toHaveProperty('figureOrigin')
    expect(current().figureSnapshot).toBeUndefined()

    // A proof for a different ordered P cannot sign this snapshot.
    prove(reference, [])
    state().commit(literal('second edit'), d => { (d.objects[0] as PanelObject).overrides[0].value = 5 })
    expect(current()).not.toHaveProperty('figureOrigin')
  })

  it('carries an explicit baseline through legacy frame migration without inventing one', () => {
    const explicit = panel()
    explicit.figureOrigin = structuredClone(origin)
    delete explicit.figureFrame
    const migrated = migrateFigureFrames([explicit])[0] as PanelObject
    expect(migrated).toMatchObject({ figureOrigin: origin })
    expect(migrated.figureSnapshot).toBeUndefined()
    expect(migrated.overrides).toEqual([...baseline, { gid: 'figure', prop: 'frame', value: 'figsize' }])

    // A bare snapshot from before the historical-input era stays an output only.
    const bare = panel()
    delete bare.figureFrame
    const plain = migrateFigureFrames([bare])[0] as PanelObject
    expect(plain).not.toHaveProperty('figureOrigin')
    expect(plain.figureSnapshot).toBeUndefined()
    expect(bare.figureSnapshot).toEqual(reference)
  })

  it('rejects a late saved output when the same file and current patches now have another historical origin', () => {
    state().commit(literal('edit'), d => { (d.objects[0] as PanelObject).overrides[1].value = 12 })
    const saved = captureFigureSnapshotSave(state().buildProject())
    state().commit(literal('historical source B'), d => {
      Object.assign(d.objects[0], { figureOrigin: { ...origin, snapshot: { ...reference, receiptId: 'receipt-B', snapshotId: 'snapshot-B' } } })
    })
    mergeSavedFigureSnapshots(saved, { p: { ...reference, patchHash: 'patch-Q', snapshotId: 'output-A-Q' } })
    expect(current().figureSnapshot).toBeUndefined()
    expect(current()).toMatchObject({ figureOrigin: { snapshot: { receiptId: 'receipt-B' } } })
  })
})
