import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { literal } from '@/i18n'
import { buildExportRequest } from '@/lib/exportRequest'
import { setCurrentProjectId } from '@/lib/session'
import { emptyProject, type FigureSnapshot, type PanelObject } from '@/types/document'
import {
  applyDerivedUpdate, captureFigureSnapshotSave, invalidateFigureSnapshots,
  mergeSavedFigureSnapshots, saveNow, startAutosave, useDocumentStore,
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
})
afterEach(async () => {
  stopAutosave?.()
  stopAutosave = undefined
  await saveNow()
  vi.unstubAllGlobals()
})

describe('immutable Figure snapshot association', () => {
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

  it('successful rerun clears refs, blocks delayed replies and does not dirty the document', () => {
    mergeSavedFigureSnapshots(saved(), { p1: ref() })
    stopAutosave = startAutosave()
    const before = s()
    const context = saved()
    invalidateFigureSnapshots(['one.pdf'])
    mergeSavedFigureSnapshots(context, { p1: ref() })
    expect(currentPanel().figureSnapshot).toBeUndefined()
    expect(s().dirty).toBe(before.dirty)
    expect(s().saveState).toBe(before.saveState)
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
