import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { literal } from '@/i18n'
import { buildExportRequest } from '@/lib/exportRequest'
import { setCurrentProjectId } from '@/lib/session'
import { emptyProject, type FigureSnapshot, type PanelObject, type ProjectDocument } from '@/types/document'
import {
  applyDerivedUpdate, captureFigureSnapshotSave, invalidateFigureSnapshots,
  flushAutosave, mergeSavedFigureSnapshots, readAutosaveDoc, saveNow, startAutosave, useDocumentStore,
} from './documentStore'

const s = () => useDocumentStore.getState()
const ref = (sourceId = 'one.pdf', snapshotId = 'snap-one'): FigureSnapshot => ({
  version: 1, sourceId, patchHash: 'patch-one', receiptId: 'receipt-one', snapshotId,
})
const panel = (id = 'p1', fileId = 'one.pdf'): PanelObject => ({
  id, fileId, type: 'panel', fileKind: 'pdf', figureFrame: 1,
  nativeW: 80, nativeH: 60, x: 0, y: 0, w: 80, h: 60, overrides: [],
})
const currentPanel = () => s().doc.objects[0] as PanelObject
const saved = () => captureFigureSnapshotSave(s().buildProject())
let stopAutosave: (() => void) | undefined
let fetchMock: ReturnType<typeof vi.fn>

beforeEach(async () => {
  localStorage.clear()
  setCurrentProjectId(null)
  fetchMock = vi.fn(async (_url: unknown, init?: RequestInit) => new Response(
    JSON.stringify(init?.method === 'PUT' ? { ok: true, saved_at: 1, revision: 'r1' } : {}),
    { status: init?.method === 'PUT' ? 200 : 404 },
  ))
  vi.stubGlobal('fetch', fetchMock)
  await s().switchDocument(emptyProject(), `snapshot-${Math.random()}`)
  s().commit(literal('panel'), (d) => { d.objects.push(panel()) })
  // switchDocument flushes the previous fixture asynchronously. Finish that real disk queue before
  // measuring this test's writes, including after cases that explicitly open another history doc.
  await saveNow()
  fetchMock.mockClear()
})
afterEach(async () => {
  stopAutosave?.()
  stopAutosave = undefined
  await saveNow()
  vi.unstubAllGlobals()
})

/** Real autosave API/queue boundaries with a controllable in-memory disk, not a mocked store callback. */
function autosaveDisk() {
  const slots = new Map<string, ProjectDocument>()
  const puts: { id: string; doc: ProjectDocument }[] = []
  let snapshots: Record<string, FigureSnapshot> = { p1: ref() }
  let hold: Promise<void> | undefined
  let completed = 0
  fetchMock.mockImplementation(async (url: unknown, init?: RequestInit) => {
    const id = String(url).match(/\/api\/autosave\/([^/?]+)/)?.[1]
    if (!id) return new Response('{}', { status: 404 })
    if (init?.method !== 'PUT') {
      const doc = slots.get(id)
      return new Response(JSON.stringify(doc ?? {}), {
        status: doc ? 200 : 404, headers: doc ? { 'X-Tavotto-Revision': `disk-${puts.length}` } : undefined,
      })
    }
    const doc = JSON.parse(String(init.body)) as ProjectDocument
    const captured = snapshots
    const pending = hold
    hold = undefined
    puts.push({ id, doc: structuredClone(doc) })
    if (pending) await pending
    for (const canvas of doc.canvases) for (const object of canvas.objects) {
      if (object.type !== 'panel') continue
      if (captured[object.id]) object.figureSnapshot = { ...captured[object.id] }
      else delete object.figureSnapshot
    }
    slots.set(id, doc)
    completed += 1
    return new Response(JSON.stringify({
      ok: true, saved_at: Date.now(), revision: `disk-${puts.length}`, figure_snapshots: captured,
    }))
  })
  return {
    slots, puts, completed: () => completed,
    snapshots: (next: Record<string, FigureSnapshot>) => { snapshots = next },
    holdNext: () => {
      let release!: () => void
      hold = new Promise<void>((resolve) => { release = resolve })
      return release
    },
  }
}

describe('immutable Figure snapshot association', () => {
  it('verified snapshot owners isolate shared static sources and survive serialization', () => {
    const ownerA = { script: 'plot.py', run_config: 'A' }
    const ownerB = { script: 'plot.py', run_config: 'B' }
    const a = { ...ref(), executionOwner: ownerA }
    const b = { ...ref(), receiptId: 'receipt-B', executionOwner: ownerB }
    s().commit(literal('shared'), (d) => { d.objects.push(panel('p2')) })
    mergeSavedFigureSnapshots(saved(), { p1: a, p2: b })
    expect(JSON.parse(JSON.stringify(s().buildProject())).canvases[0].objects[1].figureSnapshot.executionOwner).toEqual(ownerB)
    invalidateFigureSnapshots(['one.pdf'], null, [], [ownerA])
    expect(currentPanel().figureSnapshot).toBeUndefined()
    expect((s().doc.objects[1] as PanelObject).figureSnapshot).toEqual(b)
  })

  it.each(['owner', 'receipt', 'native-receipt-fact'] as const)('corrects an actual late PUT retired by %s even with no live reference', async (kind) => {
    const owner = { script: 'plot.py', run_config: 'A' }
    const old = { ...ref(), executionOwner: owner }
    const disk = autosaveDisk()
    disk.snapshots({})
    await saveNow()
    stopAutosave = startAutosave()
    disk.snapshots({ p1: old })
    const release = disk.holdNext()
    flushAutosave()
    await vi.waitFor(() => expect(disk.puts).toHaveLength(2))
    expect(currentPanel().figureSnapshot).toBeUndefined()
    if (kind === 'owner') invalidateFigureSnapshots([], null, [], [owner])
    else if (kind === 'receipt') invalidateFigureSnapshots([], null, ['receipt-one'])
    else {
      const { invalidateFigureExecution } = await import('./figureSnapshotInvalidation')
      invalidateFigureExecution({
        scope: 'sources', script: 'plot.py', run_config: null, source_ids: [],
        receipt_ids: ['receipt-one'], execution_id: 'native-receipt-only-late-put',
      }, null)
    }
    disk.snapshots({})
    release()
    await vi.waitFor(() => expect(disk.completed()).toBe(2))
    expect(currentPanel().figureSnapshot).toBeUndefined()
    expect(s().dirty).toBe(true)
    await vi.waitFor(() => expect(disk.puts).toHaveLength(3), { timeout: 2200 })
    await vi.waitFor(() => expect(s().saveState).toBe('saved'))
    const reopened = await readAutosaveDoc(s().documentId)
    expect((reopened.doc!.canvases[0].objects[0] as PanelObject).figureSnapshot).toBeUndefined()
    await new Promise((resolve) => setTimeout(resolve, 1100))
    expect(disk.puts).toHaveLength(3)
  })

  it('receipt retirement preserves another owner of the same static source and blocks late replies', () => {
    const ownerB = { ...ref(), receiptId: 'receipt-owner-B', snapshotId: 'snapshot-owner-B' }
    s().commit(literal('shared source'), (d) => { d.objects.push(panel('p2')) })
    mergeSavedFigureSnapshots(saved(), { p1: ref(), p2: ownerB })
    const beforeExecution = saved()
    invalidateFigureSnapshots([], null, ['receipt-one'])
    expect(currentPanel().figureSnapshot).toBeUndefined()
    expect((s().doc.objects[1] as PanelObject).figureSnapshot).toEqual(ownerB)
    mergeSavedFigureSnapshots(beforeExecution, { p1: ref(), p2: ownerB })
    expect(currentPanel().figureSnapshot).toBeUndefined()
    expect((s().doc.objects[1] as PanelObject).figureSnapshot).toEqual(ownerB)
    mergeSavedFigureSnapshots(saved(), { p1: ownerB, p2: ownerB })
    expect(currentPanel().figureSnapshot).toEqual(ownerB)
  })

  it('records a retired receipt even without any live ref, but does not replay into later history opens', async () => {
    const context = saved()
    invalidateFigureSnapshots([], null, ['receipt-one'])
    mergeSavedFigureSnapshots(context, { p1: ref() })
    expect(currentPanel().figureSnapshot).toBeUndefined()
    const historical = structuredClone(s().buildProject())
    ;(historical.canvases[0].objects[0] as PanelObject).figureSnapshot = ref()
    await s().switchDocument(historical, 'explicit-historical')
    expect(currentPanel().figureSnapshot).toEqual(ref())
    s().commit(literal('move'), (d) => { d.objects[0].x += 1 })
    expect(currentPanel().figureSnapshot).toEqual(ref())
  })

  it('merges a verified save response without edits, history, dirty state or another autosave', async () => {
    stopAutosave = startAutosave()
    const before = s()
    mergeSavedFigureSnapshots(saved(), { p1: ref() })
    expect(currentPanel().figureSnapshot).toEqual(ref())
    expect(s().past).toBe(before.past)
    expect(s().dirty).toBe(before.dirty)
    expect(s().saveState).toBe(before.saveState)
    await new Promise((resolve) => setTimeout(resolve, 1100))
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === 'PUT')).toHaveLength(0)
  })

  it('forwards backend snapshots through the actual autosave writer', async () => {
    fetchMock.mockImplementation(async (_url: unknown, init?: RequestInit) => new Response(
      JSON.stringify(init?.method === 'PUT'
        ? { ok: true, saved_at: 1, revision: 'r2', figure_snapshots: { p1: ref() } } : {}),
      { status: init?.method === 'PUT' ? 200 : 404 },
    ))
    await saveNow()
    expect(currentPanel().figureSnapshot).toEqual(ref())
    expect(s().saveState).toBe('saved')
  })

  it.each(['cleared', 'new-receipt'] as const)('automatically persists rerun invalidation and reloads only the %s result', async (outcome) => {
    const disk = autosaveDisk()
    await saveNow()
    const id = s().documentId
    expect(currentPanel().figureSnapshot).toEqual(ref())
    expect((disk.slots.get(id)!.canvases[0].objects[0] as PanelObject).figureSnapshot).toEqual(ref())
    const nextRef = outcome === 'new-receipt' ? { ...ref(), receiptId: 'receipt-two', snapshotId: 'snap-two' } : undefined
    disk.snapshots(nextRef ? { p1: nextRef } : {})
    stopAutosave = startAutosave()
    invalidateFigureSnapshots(['one.pdf'])
    // No manual Save or unload flush: the actual debounce subscriber must enqueue this write itself.
    await vi.waitFor(() => expect(disk.puts).toHaveLength(2), { timeout: 2200 })
    await vi.waitFor(() => expect(s().saveState).toBe('saved'))
    expect((disk.puts[1].doc.canvases[0].objects[0] as PanelObject).figureSnapshot).toBeUndefined()
    expect((disk.slots.get(id)!.canvases[0].objects[0] as PanelObject).figureSnapshot).toEqual(nextRef)
    // The backend response may attach a new reference, but that already-persisted metadata must not loop.
    await new Promise((resolve) => setTimeout(resolve, 1100))
    expect(disk.puts).toHaveLength(2)
    stopAutosave()
    stopAutosave = undefined
    const restored = await readAutosaveDoc(id)
    expect(restored.doc).not.toBeNull()
    await s().switchDocument(restored.doc!, id)
    expect(currentPanel().figureSnapshot).toEqual(nextRef)
  })

  it.each([true, false])('keeps rerun dirty after a stale save reply (live ref=%s)', async (hasLiveRef) => {
    const disk = autosaveDisk()
    if (!hasLiveRef) disk.snapshots({})
    await saveNow()
    expect(!!currentPanel().figureSnapshot).toBe(hasLiveRef)
    disk.snapshots({ p1: ref() })
    stopAutosave = startAutosave()
    const release = disk.holdNext()
    flushAutosave()
    await vi.waitFor(() => expect(disk.puts).toHaveLength(2))
    disk.snapshots({})
    invalidateFigureSnapshots(['one.pdf'])
    release()
    await vi.waitFor(() => expect(disk.completed()).toBe(2))
    expect(currentPanel().figureSnapshot).toBeUndefined()
    expect(s().dirty).toBe(true)
    expect(s().saveState).toBe('dirty')
    await vi.waitFor(() => expect(disk.puts).toHaveLength(3), { timeout: 2200 })
    await vi.waitFor(() => expect(s().saveState).toBe('saved'))
    expect((disk.slots.get(s().documentId)!.canvases[0].objects[0] as PanelObject).figureSnapshot).toBeUndefined()
  })

  it('persists a second rerun queued while the first rerun save still has no live reference', async () => {
    const disk = autosaveDisk()
    await saveNow()
    const id = s().documentId
    stopAutosave = startAutosave()
    disk.snapshots({ p1: { ...ref(), receiptId: 'run-one' } })
    const release = disk.holdNext()
    invalidateFigureSnapshots(['one.pdf'])
    try {
      await vi.waitFor(() => expect(disk.puts).toHaveLength(2), { timeout: 2200 })
      expect(currentPanel().figureSnapshot).toBeUndefined()
      disk.snapshots({ p1: { ...ref(), receiptId: 'run-two' } })
      invalidateFigureSnapshots(['one.pdf'])
      // Let the debounce queue the corrective snapshot behind the blocked first write.
      await new Promise((resolve) => setTimeout(resolve, 1100))
      expect(disk.puts).toHaveLength(2)
    } finally {
      release()
    }
    await vi.waitFor(() => expect(disk.puts).toHaveLength(3))
    await vi.waitFor(() => expect(s().saveState).toBe('saved'))
    expect(currentPanel().figureSnapshot?.receiptId).toBe('run-two')
    expect((disk.slots.get(id)!.canvases[0].objects[0] as PanelObject).figureSnapshot?.receiptId).toBe('run-two')
  })

  it('flushes an inactive-canvas invalidation to the original document when switching away', async () => {
    const disk = autosaveDisk()
    await saveNow()
    const id = s().documentId
    const canvasId = s().activeCanvasId
    s().addCanvas()
    await saveNow()
    disk.snapshots({})
    stopAutosave = startAutosave()
    invalidateFigureSnapshots(['one.pdf'])
    expect(s().dirty).toBe(true)
    await s().switchDocument(emptyProject(), 'after-rerun')
    await vi.waitFor(() => expect(disk.puts.filter((put) => put.id === id)).toHaveLength(3))
    const oldCanvas = disk.slots.get(id)!.canvases.find((canvas) => canvas.id === canvasId)!
    expect((oldCanvas.objects[0] as PanelObject).figureSnapshot).toBeUndefined()
    expect(s().documentId).toBe('after-rerun')
    expect(s().doc.objects).toHaveLength(0)
  })

  it('retains a ref-free recovery copy and an error state if the rerun autosave fails', async () => {
    const disk = autosaveDisk()
    await saveNow()
    const id = s().documentId
    stopAutosave = startAutosave()
    fetchMock.mockImplementation(async () => new Response('{}', { status: 500 }))
    invalidateFigureSnapshots(['one.pdf'])
    await vi.waitFor(() => expect(s().saveState).toBe('save_error'), { timeout: 2200 })
    expect(currentPanel().figureSnapshot).toBeUndefined()
    const recovery = JSON.parse(localStorage.getItem(`tavotto.autosave.${id}`)!) as ProjectDocument
    expect((recovery.canvases[0].objects[0] as PanelObject).figureSnapshot).toBeUndefined()
    // The failed write must not silently report success or erase the only newer recovery copy.
    expect((disk.slots.get(id)!.canvases[0].objects[0] as PanelObject).figureSnapshot).toEqual(ref())
  })

  it('does not schedule a write for an unrelated successful execution', async () => {
    const disk = autosaveDisk()
    await saveNow()
    stopAutosave = startAutosave()
    invalidateFigureSnapshots(['unrelated.pdf'])
    expect(s().dirty).toBe(false)
    await new Promise((resolve) => setTimeout(resolve, 1100))
    expect(disk.puts).toHaveLength(1)
  })

  it('updates all canvases and removes missing candidate refs but leaves newer panels alone', () => {
    mergeSavedFigureSnapshots(saved(), { p1: ref() })
    const first = s().activeCanvasId
    s().addCanvas()
    s().commit(literal('second'), (d) => { d.objects.push(panel('p2', 'two.pdf')) })
    const context = saved()
    s().commit(literal('later'), (d) => { d.objects.push({ ...panel('p3'), figureSnapshot: ref() }) })
    mergeSavedFigureSnapshots(context, { p2: ref('two.pdf') })
    expect((s().doc.objects[0] as PanelObject).figureSnapshot).toEqual(ref('two.pdf'))
    expect((s().doc.objects[1] as PanelObject).figureSnapshot).toEqual(ref())
    s().switchCanvas(first)
    expect(currentPanel().figureSnapshot).toBeUndefined()
  })

  it('an old backend omitting the map does not clear saved refs', () => {
    mergeSavedFigureSnapshots(saved(), { p1: ref() })
    mergeSavedFigureSnapshots(saved(), undefined)
    expect(currentPanel().figureSnapshot).toEqual(ref())
  })

  it.each([
    { ...ref(), version: 2 }, { ...ref(), sourceId: 'other.pdf' }, { ...ref(), receiptId: '' },
  ])('rejects unsupported/mismatched response references: %j', (bad) => {
    mergeSavedFigureSnapshots(saved(), { p1: ref() })
    mergeSavedFigureSnapshots(saved(), { p1: bad as FigureSnapshot })
    expect(currentPanel().figureSnapshot).toBeUndefined()
  })

  it.each(['override', 'source'] as const)('clears refs when %s is edited and ignores late saves', (kind) => {
    mergeSavedFigureSnapshots(saved(), { p1: ref() })
    const context = saved()
    s().commit(literal('edit'), (d) => {
      const p = d.objects[0] as PanelObject
      if (kind === 'override') p.overrides.push({ gid: 'line', prop: 'color', value: 'red' })
      else p.fileId = 'other.pdf'
    })
    expect(currentPanel().figureSnapshot).toBeUndefined()
    mergeSavedFigureSnapshots(context, { p1: ref() })
    expect(currentPanel().figureSnapshot).toBeUndefined()
    s().undo()
    expect(currentPanel().figureSnapshot).toBeUndefined()
  })

  it('keeps refs for geometry, crop, flip, rotation and exact duplication', () => {
    mergeSavedFigureSnapshots(saved(), { p1: ref() })
    const context = saved()
    s().commit(literal('geometry'), (d) => {
      const p = d.objects[0] as PanelObject
      p.x = 15; p.w = 40; p.rotation = 90; p.flipH = true
      p.crop = { x: 0.1, y: 0.1, w: 0.8, h: 0.8 }
      d.objects.push({ ...p, id: 'copy' })
    })
    mergeSavedFigureSnapshots(context, { p1: ref('one.pdf', 'new-snapshot') })
    expect(currentPanel().figureSnapshot?.snapshotId).toBe('new-snapshot')
    expect((s().doc.objects[1] as PanelObject).figureSnapshot).toEqual(ref())
  })

  it('clears ref during a transactional or silent override change', () => {
    mergeSavedFigureSnapshots(saved(), { p1: ref() })
    s().beginTxn(literal('edit'))
    s().txnUpdate((d) => { (d.objects[0] as PanelObject).overrides.push({ gid: 'g', prop: 'color', value: 'red' }) })
    expect(currentPanel().figureSnapshot).toBeUndefined()
    s().endTxn()
    mergeSavedFigureSnapshots(saved(), { p1: ref() })
    s().silent((d) => { (d.objects[0] as PanelObject).overrides = [] })
    expect(currentPanel().figureSnapshot).toBeUndefined()
  })

  it('clears a source association changed by derived metadata', () => {
    mergeSavedFigureSnapshots(saved(), { p1: ref() })
    applyDerivedUpdate({ doc: { ...s().doc, objects: [{ ...currentPanel(), fileId: 'new.pdf' }] } })
    expect(currentPanel().figureSnapshot).toBeUndefined()
  })

  it('successful rerun clears refs, blocks delayed replies and becomes dirty without an undo entry', () => {
    mergeSavedFigureSnapshots(saved(), { p1: ref() })
    stopAutosave = startAutosave()
    const before = s()
    const context = saved()
    invalidateFigureSnapshots(['one.pdf'])
    mergeSavedFigureSnapshots(context, { p1: ref() })
    expect(currentPanel().figureSnapshot).toBeUndefined()
    expect(s().dirty).toBe(true)
    expect(s().saveState).toBe('dirty')
    expect(s().past).toBe(before.past)
    mergeSavedFigureSnapshots(saved(), { p1: { ...ref(), receiptId: 'new-run' } })
    s().commit(literal('move'), (d) => { d.objects[0].x += 1 })
    expect(currentPanel().figureSnapshot?.receiptId).toBe('new-run')
  })

  it('even a rerun with no live reference blocks the older in-flight reply', () => {
    const context = saved()
    invalidateFigureSnapshots(['one.pdf'])
    mergeSavedFigureSnapshots(context, { p1: ref() })
    expect(currentPanel().figureSnapshot).toBeUndefined()
  })

  it('rerun cannot resurrect removed references through undo, but reopening keeps saved refs', async () => {
    mergeSavedFigureSnapshots(saved(), { p1: ref() })
    const diskDocument = s().buildProject()
    s().commit(literal('remove'), (d) => { d.objects = [] })
    invalidateFigureSnapshots(['one.pdf'])
    s().undo()
    expect(currentPanel().figureSnapshot).toBeUndefined()
    await s().switchDocument(diskDocument, 'reopened')
    expect(currentPanel().figureSnapshot).toEqual(ref())
  })

  it('does not merge across document reload or project changes', async () => {
    const context = saved()
    await s().switchDocument(s().buildProject(), s().documentId)
    mergeSavedFigureSnapshots(context, { p1: ref() })
    expect(currentPanel().figureSnapshot).toBeUndefined()
    const fresh = saved()
    setCurrentProjectId('another-project')
    mergeSavedFigureSnapshots(fresh, { p1: ref() })
    invalidateFigureSnapshots(['one.pdf'], null)
    expect(currentPanel().figureSnapshot).toBeUndefined()
    setCurrentProjectId(null)
  })

  it('omits a variant snapshot when original FRAME-only guarded export strips its overrides', () => {
    const p = {
      ...panel('guarded', 'plot.png'), fileKind: 'raster' as const,
      overrides: [{ gid: 'figure', prop: 'frame', value: 'figsize' }],
      artifactValidation: {
        version: 1 as const, requiredFrame: 'figsize' as const, sourceId: 'plot.png',
        bytesSha256: 'a'.repeat(64), sizeBytes: 10,
      },
      figureSnapshot: ref('plot.png'),
    }
    const request = buildExportRequest({
      scope: 'original', figureId: p.fileId, panel: p,
      filename: 'out', formats: ['svg'], ppi: 600, doc: s().doc, documentId: s().documentId,
    }).request
    expect(request.original).not.toHaveProperty('figure_snapshot')
    expect(request.original?.overrides).toBeUndefined()
  })

  it('transmits refs only for the corresponding source in original and canvas exports', () => {
    mergeSavedFigureSnapshots(saved(), { p1: ref() })
    const input = { scope: 'canvas' as const, filename: 'out', formats: ['svg'], ppi: 600, doc: s().doc, documentId: s().documentId }
    expect(buildExportRequest(input).request.canvas?.objects[0]).toMatchObject({ figure_snapshot: ref() })
    expect(buildExportRequest({ ...input, scope: 'original', figureId: 'one.pdf', panel: currentPanel() })
      .request.original).toMatchObject({ figure_snapshot: ref() })
    expect(buildExportRequest({ ...input, scope: 'original', figureId: 'two.pdf', panel: currentPanel() })
      .request.original).not.toHaveProperty('figure_snapshot')
    expect(buildExportRequest({ ...input, scope: 'original', figureId: 'one.pdf' })
      .request.original).not.toHaveProperty('figure_snapshot')
    const legacy = { ...s().doc, objects: [panel()] }
    expect(buildExportRequest({ ...input, doc: legacy }).request.canvas?.objects[0]).not.toHaveProperty('figure_snapshot')
  })
})
