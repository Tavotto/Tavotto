import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { literal } from '@/i18n'
import { setCurrentProjectId } from '@/lib/session'
import { setEngineTransport, type EngineTransport } from '@/lib/engineTransport'
import { figureSnapshotSourceKey } from '@/lib/panelSourceEvidence'
import { useAssetStore } from './assetStore'
import { useDocumentStore } from './documentStore'
import { useRenderStore } from './renderStore'
import { cancelScheduled, listIssues, originalValidationFor, prepareValidationSources, rawIssuesFor, runValidation, startValidation, VALIDATION_DEBOUNCE_MS } from './validationStore'
import { emptyProject, type PanelObject, type FigureSnapshot } from '@/types/document'

const png: PanelObject = {
  type: 'panel', id: 'p', fileId: 'same.png', fileKind: 'raster',
  nativeW: 32, nativeH: 24, w: 32, h: 24, x: 0, y: 0, overrides: [],
}
const facts = {
  id: 'same.png', stem: 'same', kind: 'raster', original_spec: {
    source_kind: 'raster', logical_w_mm: 32, logical_h_mm: 24,
    px_w: 64, px_h: 48, dpi: 600, dpi_source: 'assumed', transparent: false, viewport_pt: null,
  },
}
function archive(letter = 'a'): PanelObject {
  const fileId = 'runtime:opaque'
  return { ...png, fileId, fileKind: 'pdf', figureSnapshot: {
    version: 1, sourceId: fileId, snapshotId: letter.repeat(64), patchHash: 'c'.repeat(64),
    receiptId: 'd'.repeat(64), executionOwner: { script: 'plot.py', run_config: null },
  } }
}
const archiveResponse = (reference: FigureSnapshot) => new Response(JSON.stringify({
  preview_source: 'figure_snapshot', figure_snapshot: reference,
  svg: '<svg xmlns="http://www.w3.org/2000/svg"/>', size_mm: [32, 24],
}))
function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>(r => { resolve = r })
  return { promise, resolve }
}
const archiveCalls = () => fetcher.mock.calls.filter(([url]) => String(url).includes('/figure-preview'))
let stop: (() => void) | undefined
let documentSequence = 0
const fetcher = vi.fn<typeof fetch>()
const missing = () => listIssues().filter(i => i.ruleCode === 'missing-asset')
async function install(panel = png) {
  await useDocumentStore.getState().switchDocument(emptyProject(), `d_source_checks_${++documentSequence}`)
  useDocumentStore.getState().commit(literal('fixture'), d => { d.objects = [structuredClone(panel)] })
}
async function settle() {
  await vi.advanceTimersByTimeAsync(VALIDATION_DEBOUNCE_MS + 1)
}
beforeEach(async () => {
  vi.useFakeTimers()
  setEngineTransport(null)
  setCurrentProjectId('A')
  useAssetStore.getState().clear()
  useRenderStore.setState({ byKey: {}, latest: {} })
  fetcher.mockReset().mockImplementation(async () => new Response('{}', { status: 404 }))
  vi.stubGlobal('fetch', fetcher)
  await install()
})
afterEach(() => {
  stop?.(); stop = undefined
  cancelScheduled()
  vi.useRealTimers()
  vi.unstubAllGlobals()
  setEngineTransport(null)
})

it('exact hidden PNG facts feed the main issue list and both report projections without adding an inventory row', async () => {
  runValidation()
  expect(missing()).toHaveLength(1)
  fetcher.mockResolvedValue(new Response(JSON.stringify(facts)))
  await useAssetStore.getState().ensureOriginalSource(png.fileId)
  runValidation()
  expect(missing()).toEqual([])
  const canvas = useDocumentStore.getState().activeCanvasId
  expect(rawIssuesFor(canvas).some(i => i.id === 'missing-asset')).toBe(false)
  const original = originalValidationFor(canvas, { objectId: png.id, widthMm: 32, heightMm: 24, formats: ['png'] })
  expect(original.failed).toBe(false)
  expect(original.result?.issues.some(i => i.ruleCode === 'missing-asset')).toBe(false)
  expect(useAssetStore.getState().byId).toEqual({})
})

it('opening the normal validation driver resolves a hidden PNG without opening ExportDialog', async () => {
  fetcher.mockImplementation(async url => String(url).includes('/api/panel-stem')
    ? new Response(JSON.stringify(facts)) : new Response('{}', { status: 404 }))
  stop = startValidation()
  await settle()
  expect(missing()).toEqual([])
  expect(fetcher.mock.calls.some(([url]) => String(url).includes('/api/panel-stem'))).toBe(true)
})

it('a verified archived runtime source removes missing from canvas and original, without inventing a manifest', async () => {
  const panel = archive()
  await install(panel)
  fetcher.mockImplementation(async (url, init) => String(url).includes('/figure-preview')
    ? archiveResponse(JSON.parse(String(init?.body)).figure_snapshot) : new Response('{}', { status: 404 }))
  stop = startValidation()
  await settle()
  expect(missing()).toEqual([])
  expect(listIssues().some(i => i.ruleCode === 'panel-text-not-verifiable')).toBe(true)
  const canvas = useDocumentStore.getState().activeCanvasId
  expect(originalValidationFor(canvas, { objectId: panel.id, widthMm: 32, heightMm: 24, formats: ['pdf'] })
    .result?.issues.some(i => i.ruleCode === 'missing-asset')).toBe(false)
  expect(archiveCalls()).toHaveLength(1)
  const [url, init] = archiveCalls()[0]
  expect(String(url)).toContain('pj=A')
  expect(new Headers(init?.headers).get('X-Tavotto-Project')).toBe('A')
  expect(JSON.parse(String(init?.body))).toEqual({ id: panel.fileId, patches: [], figure_snapshot: panel.figureSnapshot })
})

it('another full reference or original patch order needs its own proof, including identical source ids', async () => {
  const a = archive(), b = archive('b')
  fetcher.mockResolvedValueOnce(archiveResponse(a.figureSnapshot!))
  expect(await useAssetStore.getState().ensureFigureSnapshotSource(a)).toBe(true)
  await install(b)
  runValidation()
  expect(missing()).toHaveLength(1)
  fetcher.mockResolvedValueOnce(archiveResponse(b.figureSnapshot!))
  await useAssetStore.getState().ensureFigureSnapshotSource(b)
  runValidation()
  expect(missing()).toEqual([])
  const patched = { ...b, overrides: [{ gid: 'line', prop: 'color', value: 'red' }, { gid: 'line', prop: 'color', value: 'blue' }] }
  fetcher.mockResolvedValueOnce(archiveResponse(b.figureSnapshot!))
  await useAssetStore.getState().ensureFigureSnapshotSource(patched)
  await install({ ...patched, overrides: [...patched.overrides].reverse() })
  runValidation()
  expect(missing()).toHaveLength(1)
  fetcher.mockResolvedValueOnce(new Response('{}', { status: 422 }))
  expect(await useAssetStore.getState().ensureFigureSnapshotSource({ ...patched, overrides: [...patched.overrides].reverse() })).toBe(false)
  expect(archiveCalls()).toHaveLength(4)
})

it('bad or mismatched archive replies cannot borrow current disk bytes or the legacy runtime exception', async () => {
  const a = { ...archive(), fileKind: 'runtime' as const }
  await install(a)
  useAssetStore.setState({ byId: { [a.fileId]: { id: a.fileId } } } as never)
  fetcher.mockResolvedValueOnce(archiveResponse(archive('b').figureSnapshot!))
  expect(await useAssetStore.getState().ensureFigureSnapshotSource(a)).toBe(false)
  runValidation()
  expect(missing()).toHaveLength(1)
  await install({ ...a, figureSnapshot: null } as unknown as PanelObject)
  stop = startValidation()
  await settle()
  expect(missing()).toHaveLength(1)
  expect(archiveCalls()).toHaveLength(1)
})

it('archive negative facts coalesce and cache until refresh, which also invalidates prior positives', async () => {
  const a = archive()
  fetcher.mockResolvedValueOnce(new Response('{}', { status: 404 }))
  const first = useAssetStore.getState().ensureFigureSnapshotSource(a)
  const second = useAssetStore.getState().ensureFigureSnapshotSource(a)
  expect(await first).toBe(false)
  expect(await second).toBe(false)
  expect(await useAssetStore.getState().ensureFigureSnapshotSource(a)).toBe(false)
  expect(archiveCalls()).toHaveLength(1)
  fetcher.mockImplementation(async url => String(url).includes('/api/panels')
    ? new Response(JSON.stringify({ panels: [], figures_dir: '' })) : archiveResponse(a.figureSnapshot!))
  await useAssetStore.getState().load({ force: true })
  expect(await useAssetStore.getState().ensureFigureSnapshotSource(a)).toBe(true)
  fetcher.mockImplementation(async url => String(url).includes('/api/panels')
    ? new Response(JSON.stringify({ panels: [], figures_dir: '' })) : new Response('{}', { status: 404 }))
  await useAssetStore.getState().load({ force: true })
  expect(await useAssetStore.getState().ensureFigureSnapshotSource(a)).toBe(false)
  expect(archiveCalls()).toHaveLength(3)
})

it('A→B→A rejects a late positive and lets the new project query complete without the old request', async () => {
  const a = archive(), old = deferred<Response>()
  fetcher.mockReturnValueOnce(old.promise)
  const pending = useAssetStore.getState().ensureFigureSnapshotSource(a)
  await Promise.resolve()
  setCurrentProjectId('B'); useAssetStore.getState().clear()
  setCurrentProjectId('A'); useAssetStore.getState().clear()
  fetcher.mockResolvedValueOnce(new Response('{}', { status: 404 }))
  expect(await useAssetStore.getState().ensureFigureSnapshotSource(a)).toBe(false)
  old.resolve(archiveResponse(a.figureSnapshot!))
  expect(await pending).toBe(false)
  expect(useAssetStore.getState().figureSnapshots[figureSnapshotSourceKey(a)!]).toBe(false)
})

it('A document with a newly claimed B project does not issue either source query under B', async () => {
  await install(archive())
  setCurrentProjectId('B')
  useAssetStore.getState().clear()
  fetcher.mockClear()
  stop = startValidation()
  await settle()
  expect(archiveCalls()).toEqual([])
  expect(fetcher.mock.calls.some(([url]) => String(url).includes('/api/panel-stem'))).toBe(false)
  stop(); stop = undefined
  await install(png)
  fetcher.mockImplementation(async url => String(url).includes('/api/panel-stem')
    ? new Response(JSON.stringify(facts)) : new Response('{}', { status: 404 }))
  stop = startValidation()
  await settle()
  expect(missing()).toEqual([])
})

it('an alternate host cannot query HTTP archives or use old HTTP positive facts', async () => {
  const a = archive()
  await install(a)
  fetcher.mockResolvedValueOnce(archiveResponse(a.figureSnapshot!))
  await useAssetStore.getState().ensureFigureSnapshotSource(a)
  runValidation()
  expect(missing()).toEqual([])
  setEngineTransport({} as EngineTransport)
  expect(await useAssetStore.getState().ensureFigureSnapshotSource(archive('b'))).toBe(false)
  runValidation()
  expect(missing()).toHaveLength(1)
  expect(archiveCalls()).toHaveLength(1)
})

it('refresh while report preparation waits cannot turn old completion into a current source check', async () => {
  const a = archive(), old = deferred<Response>()
  await install(a)
  fetcher.mockReturnValueOnce(old.promise)
  const preparation = prepareValidationSources()
  await Promise.resolve()
  fetcher.mockResolvedValueOnce(new Response(JSON.stringify({ panels: [], figures_dir: '' })))
  await useAssetStore.getState().load({ force: true })
  old.resolve(archiveResponse(a.figureSnapshot!))
  expect(await preparation).toBe(false)
  expect(useAssetStore.getState().figureSnapshots).toEqual({})
})

it('full ref JSON member order does not trigger another read, but malformed successful replies stay missing', async () => {
  const a = archive()
  const ref = Object.fromEntries(Object.entries(a.figureSnapshot!).reverse()) as unknown as FigureSnapshot
  fetcher.mockResolvedValueOnce(archiveResponse(ref))
  expect(await useAssetStore.getState().ensureFigureSnapshotSource(a)).toBe(true)
  expect(await useAssetStore.getState().ensureFigureSnapshotSource({ ...a, figureSnapshot: ref })).toBe(true)
  expect(archiveCalls()).toHaveLength(1)
  const b = archive('b')
  await install(b)
  fetcher.mockResolvedValueOnce(new Response(JSON.stringify({ preview_source: 'figure_snapshot',
    figure_snapshot: b.figureSnapshot, svg: '', size_mm: [32, 24] })))
  expect(await useAssetStore.getState().ensureFigureSnapshotSource(b)).toBe(false)
  runValidation()
  expect(missing()).toHaveLength(1)
})
