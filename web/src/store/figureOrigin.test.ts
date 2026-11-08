import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { literal } from '@/i18n'
import { validFigureOrigin } from '@/lib/figureSnapshot'
import { setCurrentProjectId } from '@/lib/session'
import { emptyProject, type FigureOrigin, type FigureSnapshot, type PanelObject } from '@/types/document'
import { captureFigureSnapshotSave, mergeSavedFigureSnapshots, saveNow, useDocumentStore } from './documentStore'

const state = () => useDocumentStore.getState()
const baseline = [{ gid: 'line', prop: 'linewidth', value: 2 }, { gid: 'line', prop: 'linewidth', value: 3 }]
const reference: FigureSnapshot = { version: 1, sourceId: 'History.pdf', patchHash: 'patch-P', receiptId: 'receipt-A', snapshotId: 'snapshot-A' }
const panel = (): PanelObject => ({ id: 'p', type: 'panel', fileId: 'History.pdf', fileKind: 'pdf',
  figureFrame: 1, x: 0, y: 0, w: 80, h: 60, nativeW: 80, nativeH: 60,
  overrides: structuredClone(baseline), figureSnapshot: { ...reference } })
const current = () => state().doc.objects[0] as PanelObject
const origin: FigureOrigin = { version: 1, snapshot: reference, patches: baseline }

beforeEach(async () => {
  localStorage.clear()
  setCurrentProjectId(null)
  vi.stubGlobal('fetch', vi.fn(async (_url: unknown, init?: RequestInit) => new Response(JSON.stringify(
    init?.method === 'PUT' ? { ok: true, saved_at: 1, revision: 'saved' } : {},
  ), { status: init?.method === 'PUT' ? 200 : 404 })))
  await state().switchDocument(emptyProject(), `origin-${Math.random()}`)
  state().commit(literal('panel'), d => { d.objects.push({ ...panel(), figureOrigin: structuredClone(origin) }) })
  await saveNow()
})
afterEach(async () => { await saveNow(); vi.unstubAllGlobals() })

describe('historical Figure input survives the original document writer', () => {
  it('retains a stored ordered baseline through editing, undo, redo and the real writer/read boundary', async () => {
    const documentId = state().documentId
    state().commit(literal('edit'), d => { (d.objects[0] as PanelObject).overrides[1].value = 12 })
    expect(current()).toMatchObject({ figureOrigin: origin })
    expect(current().figureSnapshot).toBeUndefined()
    expect(state().documentId).toBe(documentId)
    expect(state().documentFormat).toBe('legacy')
    state().undo()
    expect(current().overrides).toEqual(baseline)
    expect(current()).toMatchObject({ figureOrigin: origin })
    state().redo()
    expect(current().overrides[1].value).toBe(12)
    expect(current()).toMatchObject({ figureOrigin: origin })
    await saveNow()
    const calls = vi.mocked(fetch).mock.calls.filter(([, init]) => init?.method === 'PUT')
    const persisted = JSON.parse(String(calls.at(-1)![1]!.body))
    expect(persisted.canvases[0].objects[0].figureOrigin).toEqual(origin)
    await state().switchDocument(persisted, 'reopened-origin')
    expect(current()).toMatchObject({ figureOrigin: origin })
    state().commit(literal('second edit'), d => { (d.objects[0] as PanelObject).overrides[1].value = 15 })
    expect(current()).toMatchObject({ figureOrigin: origin })
  })

  it('preserves the original baseline after a cancelled transaction and a source-changing undo', () => {
    state().beginTxn(literal('drag'))
    state().txnUpdate(d => { (d.objects[0] as PanelObject).overrides = [] })
    state().endTxn({ discard: true })
    expect(current().overrides).toEqual(baseline)
    expect(current()).toMatchObject({ figureOrigin: origin })
    state().commit(literal('replace'), d => { (d.objects[0] as PanelObject).fileId = 'Other.pdf' })
    expect(current()).not.toHaveProperty('figureOrigin')
    expect(current().figureSnapshot).toBeUndefined()
    state().undo()
    expect(current()).toMatchObject({ fileId: 'History.pdf', figureOrigin: origin })
  })

  it('never invents a baseline for a snapshotless edit or borrows a replaced source', () => {
    state().commit(literal('no evidence'), d => {
      delete (d.objects[0] as PanelObject).figureSnapshot
      delete (d.objects[0] as PanelObject).figureOrigin
    })
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

  it('keeps ordinary snapshot editing on the old wire without creating a historical origin', async () => {
    state().commit(literal('ordinary'), d => { d.objects = [panel()] })
    state().commit(literal('edit'), d => { (d.objects[0] as PanelObject).overrides[1].value = 12 })
    expect(current()).not.toHaveProperty('figureOrigin')
    expect(current().figureSnapshot).toBeUndefined()
    await saveNow()
    const calls = vi.mocked(fetch).mock.calls.filter(([, init]) => init?.method === 'PUT')
    expect(JSON.parse(String(calls.at(-1)![1]!.body)).canvases[0].objects[0]).not.toHaveProperty('figureOrigin')
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

  it('preserves exact cloned origins and validates structure without inferring baseline semantics', () => {
    state().commit(literal('clone'), d => { d.objects.push({ ...current(), id: 'copy' }) })
    expect((state().doc.objects[1] as PanelObject).figureOrigin).toEqual(origin)
    expect(validFigureOrigin(origin, 'History.pdf')).toBe(true)
    for (const invalid of [{ ...origin, version: true }, { ...origin, extra: 1 },
      { ...origin, patches: [{ gid: '', prop: 'x', value: 1 }] },
      { ...origin, patches: [{ gid: 'g', prop: 'x' }] },
      { ...origin, patches: [{ gid: 'g', prop: 'x', value: 1, identity: '' }] }]) {
      expect(validFigureOrigin(invalid, 'History.pdf')).toBe(false)
    }
    expect(validFigureOrigin(origin, 'Other.pdf')).toBe(false)
  })

  it('merges only the same execution receipt/owner output for an explicit origin, allowing a different current patch hash', () => {
    state().commit(literal('edit'), d => { (d.objects[0] as PanelObject).overrides[1].value = 12 })
    const context = captureFigureSnapshotSave(state().buildProject())
    for (const wrong of [{ ...reference, receiptId: 'receipt-B' },
      { ...reference, executionOwner: { script: 'same-name.py', run_config: null } }]) {
      mergeSavedFigureSnapshots(context, { p: wrong })
      expect(current().figureSnapshot).toBeUndefined()
    }
    const currentOutput = { ...reference, snapshotId: 'output-Q', patchHash: 'patch-Q' }
    mergeSavedFigureSnapshots(context, { p: currentOutput })
    expect(current().figureSnapshot).toEqual(currentOutput)
    expect(current().figureOrigin).toEqual(origin)
  })

  it('retains valid patch extras and ordered duplicate records through saving and output association', async () => {
    const withExtras: FigureOrigin = { ...origin, patches: baseline.map((patch, index) => ({ ...patch, note: `original-${index}` })) }
    expect(validFigureOrigin(withExtras, 'History.pdf')).toBe(true)
    expect(validFigureOrigin({ ...origin, patches: [{ gid: 'g', prop: 'x', value: 1, identity: undefined }] }, 'History.pdf')).toBe(false)
    state().commit(literal('stored source'), d => { (d.objects[0] as PanelObject).figureOrigin = withExtras })
    state().commit(literal('edit'), d => { (d.objects[0] as PanelObject).overrides = [] })
    const output = { ...reference, snapshotId: 'same-run-current-Q', patchHash: 'Q' }
    mergeSavedFigureSnapshots(captureFigureSnapshotSave(state().buildProject()), { p: output })
    expect(current().figureSnapshot).toEqual(output)
    expect(current().figureOrigin).toEqual(withExtras)
    await saveNow()
    const calls = vi.mocked(fetch).mock.calls.filter(([, init]) => init?.method === 'PUT')
    expect(JSON.parse(String(calls.at(-1)![1]!.body)).canvases[0].objects[0].figureOrigin).toEqual(withExtras)
  })
})
