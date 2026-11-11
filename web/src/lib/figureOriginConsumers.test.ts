import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { literal } from '@/i18n'
import { enginePreviewPng, engineSpecfix, fetchHistory, restoreHistory, syncOverrides, updateSourceFiles, verifyFigureOriginRender } from './api'
import { canEditPanel } from './panelEditability'
import { getOriginalOutputSpec } from './originalSpec'
import { materializePaste, parsePayload } from './clipboard'
import { CLIPBOARD_FORMAT } from './brand'
import { setCurrentProjectId } from './session'
import { emptyProject, type FigureOrigin, type PanelObject } from '@/types/document'
import { useDocumentStore } from '@/store/documentStore'
import { useAssetStore } from '@/store/assetStore'
import { useRenderStore } from '@/store/renderStore'
import { enterElementEdit } from '@/store/actions'
import { useUiStore } from '@/store/uiStore'

const origin: FigureOrigin = { version: 1, snapshot: {
  version: 1, sourceId: 'History.pdf', patchHash: 'P', receiptId: 'A', snapshotId: 'archive-A',
}, patches: [{ gid: 'line', prop: 'linewidth', value: 2 }, { gid: 'line', prop: 'linewidth', value: 3 }] }
const q = [{ gid: 'line', prop: 'linewidth', value: 12 }]
const panel = (): PanelObject => ({ id: 'history', type: 'panel', fileId: 'History.pdf', fileKind: 'pdf',
  x: 0, y: 0, w: 80, h: 60, nativeW: 80, nativeH: 60, figureFrame: 1,
  overrides: structuredClone(q), figureOrigin: structuredClone(origin) })
let fetcher: ReturnType<typeof vi.fn>
beforeEach(async () => {
  setCurrentProjectId('A')
  useRenderStore.getState().clear(); useAssetStore.getState().clear()
  fetcher = vi.fn(async () => Response.json({}, { status: 404 }))
  vi.stubGlobal('fetch', fetcher)
  await useDocumentStore.getState().switchDocument(emptyProject(), 'origin-consumer')
  fetcher.mockClear()
})
afterEach(() => { useRenderStore.getState().clear(); setCurrentProjectId(null); vi.unstubAllGlobals() })

describe('historical source at the existing consumers', () => {
  it('specfix carries P and Q independently and rejects a successful body from another origin', async () => {
    const reply = { ok: true, exit: 'done', patches: q, skipped: [], figure_origin: origin }
    fetcher.mockResolvedValueOnce(Response.json(reply))
    await expect(engineSpecfix('History.pdf', q, 1, {}, undefined, { figure_origin: origin }, 'A')).resolves.toMatchObject(reply)
    const [url, init] = fetcher.mock.calls[0]
    expect(String(url)).toContain('pj=A')
    expect(JSON.parse(init.body)).toEqual({ id: 'History.pdf', patches: q, scale: 1, profile: {}, figure_origin: origin })
    fetcher.mockResolvedValueOnce(Response.json({ ...reply, figure_origin: { ...origin, patches: [] } }))
    await expect(engineSpecfix('History.pdf', q, 1, {}, undefined, { figure_origin: origin })).rejects.toMatchObject({ code: 'svg_source_unavailable' })
  })

  it('writeback retains its existing fields and only accepts the requested origin echo', async () => {
    const reply = { updated: ['History.pdf'], baked: true, figure_origin: origin }
    fetcher.mockResolvedValueOnce(Response.json(reply))
    await expect(updateSourceFiles('History.pdf', q, undefined, 123, { figure_origin: origin }, 'A')).resolves.toEqual(reply)
    expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual({ id: 'History.pdf', patches: q, expected_mtime: 123, figure_origin: origin })
    fetcher.mockResolvedValueOnce(Response.json({ ...reply, figure_origin: undefined }))
    await expect(updateSourceFiles('History.pdf', q, undefined, 123, { figure_origin: origin })).rejects.toMatchObject({ code: 'svg_source_unavailable' })
    fetcher.mockResolvedValueOnce(Response.json({ updated: ['plain.pdf'] }))
    await updateSourceFiles('plain.pdf', [], undefined, 123)
    expect(JSON.parse(fetcher.mock.calls[2][1].body)).toEqual({ id: 'plain.pdf', patches: [], expected_mtime: 123 })
  })

  it('history uses POST with the same baseline, restores Q, and preserves the old GET shape', async () => {
    fetcher.mockResolvedValueOnce(Response.json({ versions: [], figure_origin: origin }))
    await fetchHistory('History.pdf', { figure_origin: origin }, 'A')
    expect(String(fetcher.mock.calls[0][0])).toContain('/api/engine/history?pj=A')
    expect(fetcher.mock.calls[0][1].method).toBe('POST')
    expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual({ id: 'History.pdf', figure_origin: origin })
    fetcher.mockResolvedValueOnce(Response.json({ patches: q, figure_origin: origin }))
    await restoreHistory('History.pdf', 2, 123, { figure_origin: origin }, 'A')
    expect(JSON.parse(fetcher.mock.calls[1][1].body)).toEqual({ id: 'History.pdf', n: 2, expected_mtime: 123, figure_origin: origin })
    fetcher.mockResolvedValueOnce(Response.json({ versions: [] }))
    await expect(fetchHistory('History.pdf', { figure_origin: origin })).rejects.toMatchObject({ code: 'svg_source_unavailable' })
    fetcher.mockResolvedValueOnce(Response.json({ patches: q }))
    await expect(restoreHistory('History.pdf', 2, undefined, { figure_origin: origin })).rejects.toMatchObject({ code: 'svg_source_unavailable' })
    fetcher.mockResolvedValueOnce(Response.json({ versions: [] }))
    await fetchHistory('plain.pdf')
    expect(String(fetcher.mock.calls[4][0])).toContain('/api/engine/history?id=plain.pdf')
    expect(fetcher.mock.calls[4][1]?.body).toBeUndefined()
  })

  it('sync freezes distinct source P/current Q and target P/current Q, and checks both echoes', async () => {
    const targetOrigin = structuredClone(origin)
    targetOrigin.snapshot.sourceId = 'Target.pdf'
    targetOrigin.snapshot.receiptId = 'target-A'
    const targetQ = [{ gid: 'line', prop: 'linewidth', value: 7 }]
    const fields = { source_figure_origin: origin, target_figure_origin: targetOrigin, target_patches: targetQ }
    const reply = { mapped: q, skipped: [], unmatched: [], source_figure_origin: origin, target_figure_origin: targetOrigin }
    fetcher.mockResolvedValueOnce(Response.json(reply))
    await syncOverrides('History.pdf', 'Target.pdf', q, fields, 'A')
    expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual({ from_id: 'History.pdf', to_id: 'Target.pdf', patches: q, ...fields })
    for (const bad of [{ ...reply, source_figure_origin: undefined }, { ...reply, target_figure_origin: { ...targetOrigin, patches: [] } }]) {
      fetcher.mockResolvedValueOnce(Response.json(bad))
      await expect(syncOverrides('History.pdf', 'Target.pdf', q, fields)).rejects.toMatchObject({ code: 'svg_source_unavailable' })
    }
    fetcher.mockResolvedValueOnce(Response.json({ mapped: [], skipped: [], unmatched: [] }))
    await syncOverrides('plain.pdf', 'target.pdf', [])
    expect(JSON.parse(fetcher.mock.calls[3][1].body)).toEqual({ from_id: 'plain.pdf', to_id: 'target.pdf', patches: [] })
  })

  it('PNG requires the complete origin header and actual nonempty image response', async () => {
    const headers = { 'Content-Type': 'image/png', 'X-Tavotto-Figure-Origin': JSON.stringify({ patches: origin.patches, snapshot: origin.snapshot, version: 1 }) }
    fetcher.mockResolvedValueOnce(new Response('pixels-A', { headers }))
    expect(await (await enginePreviewPng('History.pdf', q, 360, undefined, { figure_origin: origin }, 'A')).text()).toBe('pixels-A')
    for (const response of [new Response('pixels-B', { headers: { ...headers, 'X-Tavotto-Figure-Origin': '{}' } }),
      new Response('html', { headers: { ...headers, 'Content-Type': 'text/html' } }), new Response('', { headers })]) {
      fetcher.mockResolvedValueOnce(response)
      await expect(enginePreviewPng('History.pdf', q, 360, undefined, { figure_origin: origin })).rejects.toThrow()
    }
  })

  it('a scriptless historical panel can enter the original edit path without current-file admission', async () => {
    const p = panel()
    useDocumentStore.getState().commit(literal('fixture'), d => { d.objects.push(p) })
    expect(canEditPanel(p)).toBe(true)
    fetcher.mockImplementation(async (_url: unknown, init: RequestInit) => {
      const input = JSON.parse(String(init.body))
      return Response.json({ rev: 1, svg: '<svg/>', manifest: { size_mm: [80, 60], elements: [] }, figure_origin: input.figure_origin })
    })
    expect(enterElementEdit(p.id)).toBe(true)
    await vi.waitFor(() => expect(fetcher.mock.calls.some(([url]) => String(url).includes('/api/engine/render'))).toBe(true))
    const call = fetcher.mock.calls.find(([url]) => String(url).includes('/api/engine/render'))!
    expect(JSON.parse(call[1].body)).toMatchObject({ patches: q, figure_origin: origin })
    expect(JSON.parse(call[1].body)).not.toHaveProperty('source_policy')
    expect(useUiStore.getState().elementPanelId).toBe(p.id)
  })

  it('keeps lineage on exact copies in another project, and explicitly re-linking clears both refs', async () => {
    const p = { ...panel(), figureSnapshot: origin.snapshot }
    const payload = parsePayload(JSON.stringify({ magic: CLIPBOARD_FORMAT, sourceDocId: 'elsewhere', objects: [p], layoutGroups: [] }))!
    setCurrentProjectId('B')
    await useDocumentStore.getState().switchDocument(emptyProject(), 'destination-origin')
    materializePaste(payload, [])
    expect(useDocumentStore.getState().doc.objects[0]).toMatchObject({ figureOrigin: origin, figureSnapshot: origin.snapshot })
    useAssetStore.setState({ byId: { 'Other.pdf': { id: 'Other.pdf', kind: 'pdf', native_w_mm: 40, native_h_mm: 30, name: 'Other' } as never } })
    materializePaste(payload, [{ fileId: p.fileId, name: p.fileId, count: 1, relinkTo: 'Other.pdf' }])
    const linked = useDocumentStore.getState().doc.objects.at(-1)!
    expect(linked).toMatchObject({ fileId: 'Other.pdf' })
    expect(linked).not.toHaveProperty('figureOrigin')
    expect(linked).not.toHaveProperty('figureSnapshot')
  })

  it('historical physical size never borrows a same-name current file or runtime descriptor', () => {
    const p = panel()
    useAssetStore.setState({ byId: { [p.fileId]: { id: p.fileId, original_spec: {
      source_kind: 'vector', logical_w_mm: 999, logical_h_mm: 888, viewport_pt: [1000, 1000], dpi_source: 'unknown',
    } } as never } })
    expect(getOriginalOutputSpec(p.fileId, p)).toMatchObject({ widthMm: 80, heightMm: 60, origin: 'document' })
  })

  it('render verification accepts only a verified replay adoption echo', () => {
    const adoptedSnapshot = { ...origin.snapshot, receiptId: 'B', snapshotId: 'archive-B' }
    const adopted: FigureOrigin = { version: 1, snapshot: adoptedSnapshot, patches: structuredClone(origin.patches) }
    const paired = {
      manifest: { stem: 'History', size_mm: [80, 60] as [number, number], elements: [] },
      preview: { mode: 'raster' as const, reason: 'fallback' as const, svg_bytes: 0, rasterized_artist_count: 0 },
      rev: 1,
    }
    // The backend-verified adoption re-points the input; the caller adopts it.
    expect(verifyFigureOriginRender(origin, { ...paired, figure_origin: adopted, figure_origin_adopted: true })).toEqual(adopted)
    // Without the adoption flag the echo must be exact.
    expect(() => verifyFigureOriginRender(origin, { ...paired, figure_origin: adopted })).toThrow()
    // A drifted P under an adoption flag is a protocol violation: fail closed.
    expect(() => verifyFigureOriginRender(origin, {
      ...paired,
      figure_origin: { version: 1, snapshot: adoptedSnapshot, patches: [] },
      figure_origin_adopted: true,
    })).toThrow()
    // An exact echo adopts nothing.
    expect(verifyFigureOriginRender(origin, { ...paired, figure_origin: origin, figure_origin_adopted: true })).toBeUndefined()
    expect(verifyFigureOriginRender(origin, { ...paired, figure_origin: origin })).toBeUndefined()
  })
})
