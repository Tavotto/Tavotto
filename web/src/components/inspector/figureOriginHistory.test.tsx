import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { literal } from '@/i18n'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { setCurrentProjectId } from '@/lib/session'
import { useDocumentStore } from '@/store/documentStore'
import { useAssetStore } from '@/store/assetStore'
import { useRenderStore } from '@/store/renderStore'
import { clearVariantPngCache } from '@/hooks/useVariantPng'
import { emptyProject, type PanelObject } from '@/types/document'
import { useWriteBackRecords, writeBackRecordKey, WriteBackRecordsPage, type RecordsOf } from './WriteBackRecords'

globalThis.IS_REACT_ACT_ENVIRONMENT = true

const panel = (receipt: string): PanelObject => ({
  id: receipt, type: 'panel', fileId: 'History.pdf', fileKind: 'pdf', x: 0, y: 0, w: 80, h: 60,
  nativeW: 80, nativeH: 60, overrides: [{ gid: 'line', prop: 'linewidth', value: 12 }],
  figureOrigin: { version: 1, snapshot: { version: 1, sourceId: 'History.pdf', patchHash: 'P', receiptId: receipt, snapshotId: receipt },
    patches: [{ gid: 'line', prop: 'linewidth', value: 2 }] },
})
let root: Root
let host: HTMLDivElement
let fetcher: ReturnType<typeof vi.fn>
let records: Record<string, RecordsOf>
function Probe() {
  const panels = useDocumentStore(s => s.doc.objects) as PanelObject[]
  records = useWriteBackRecords(panels.map(p => p.fileId), true, panels)
  return null
}
beforeEach(async () => {
  setCurrentProjectId('origin-history-A')
  useRenderStore.getState().clear()
  useAssetStore.getState().clear()
  clearVariantPngCache()
  fetcher = vi.fn(async () => Response.json({}, { status: 404 }))
  vi.stubGlobal('fetch', fetcher)
  await useDocumentStore.getState().switchDocument(emptyProject(), 'history-doc')
  fetcher.mockClear()
  records = {}
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
})
afterEach(async () => {
  await act(async () => root.unmount())
  host.remove(); clearVariantPngCache(); useRenderStore.getState().clear()
  setCurrentProjectId(null); vi.unstubAllGlobals()
})

it('same file A/B have separate history requests and lists, late A cannot replace B', async () => {
  const a = panel('A'), b = panel('B')
  useDocumentStore.getState().commit(literal('fixture'), d => { d.objects.push(a, b) })
  let finishA!: (r: Response) => void
  fetcher.mockImplementation((url: unknown, init: RequestInit) => {
    if (!String(url).includes('/api/engine/history?')) return Promise.resolve(Response.json({}, { status: 404 }))
    const body = JSON.parse(String(init.body))
    if (body.figure_origin.snapshot.receiptId === 'A') return new Promise<Response>(resolve => { finishA = resolve })
    return Promise.resolve(Response.json({ figure_origin: b.figureOrigin, versions: [{ n: 4, ts: '', count: 1, patches: b.overrides }] }))
  })
  await act(async () => root.render(<Probe />))
  const requests = fetcher.mock.calls.filter(([url]) => String(url).includes('/api/engine/history?'))
  expect(requests).toHaveLength(2)
  expect(requests.every(([url, init]) => String(url).includes('pj=origin-history-A') && init.method === 'POST')).toBe(true)
  await act(async () => {
    finishA(Response.json({ figure_origin: a.figureOrigin, versions: [{ n: 1, ts: '', count: 1, patches: a.overrides }] }))
  })
  expect(records[writeBackRecordKey(a)]).toMatchObject([{ n: 1 }])
  expect(records[writeBackRecordKey(b)]).toMatchObject([{ n: 4 }])
  expect(Object.keys(records)).toHaveLength(2)
})

it('switching document before a history response cannot expose the previous source list', async () => {
  const a = panel('A')
  useDocumentStore.getState().commit(literal('fixture'), d => { d.objects.push(a) })
  let finish!: (r: Response) => void
  fetcher.mockImplementation((url: unknown) => String(url).includes('/api/engine/history?')
    ? new Promise<Response>(resolve => { finish = resolve }) : Promise.resolve(Response.json({}, { status: 404 })))
  await act(async () => root.render(<Probe />))
  // Empty destination has no source to query; this check cannot be satisfied by a second reply.
  await act(async () => {
    setCurrentProjectId('origin-history-B')
    await useDocumentStore.getState().switchDocument(emptyProject(), 'other-history-doc')
  })
  await act(async () => finish(Response.json({ figure_origin: a.figureOrigin, versions: [{ n: 1 }] })))
  expect(records).toEqual({})
})

it('each historical row previews its own Q through paired PNG, never the current history GET', async () => {
  const a = panel('A')
  useDocumentStore.getState().commit(literal('fixture'), d => { d.objects.push(a) })
  const versions = [{ n: 1, ts: '', count: 1, patches: [{ gid: 'line', prop: 'linewidth', value: 7 }] }]
  fetcher.mockImplementation(async (url: unknown, init: RequestInit) => {
    if (!String(url).includes('/api/engine/preview_png')) return Response.json({}, { status: 404 })
    const body = JSON.parse(String(init.body))
    return Response.json({ figure_origin: body.figure_origin, png: 'eA==', manifest: { size_mm: [80, 60], elements: [] } })
  })
  await act(async () => root.render(<TooltipProvider><WriteBackRecordsPage panels={[a]} byFile={{ [writeBackRecordKey(a)]: versions }} onDone={() => {}} /></TooltipProvider>))
  const previews = fetcher.mock.calls.filter(([url]) => String(url).includes('/api/engine/preview_png'))
  expect(previews).toHaveLength(2)
  expect(previews.map(([, init]) => JSON.parse(init.body).patches)).toEqual([[], versions[0].patches])
  for (const [, init] of previews) expect(JSON.parse(init.body).figure_origin).toEqual(a.figureOrigin)
  expect([...document.body.querySelectorAll('img')].every(img => img.src.startsWith('data:image/png'))).toBe(true)
  expect(fetcher.mock.calls.some(([url]) => String(url).includes('/history/'))).toBe(false)
})


it('a restore confirmation belongs to its historical source, not the next same-name record list', async () => {
  const a = panel('A'), b = panel('B')
  useDocumentStore.getState().commit(literal('fixture'), d => { d.objects.push(a, b) })
  const versions = [{ n: 1, ts: '', count: 1, patches: a.overrides }]
  fetcher.mockResolvedValue(Response.json({}, { status: 404 }))
  const show = (p: PanelObject) => act(async () => root.render(<TooltipProvider><WriteBackRecordsPage panels={[p]} byFile={{ [writeBackRecordKey(p)]: versions }} onDone={() => {}} /></TooltipProvider>))
  await show(a)
  await act(async () => (document.body.querySelector('[data-write-back="restore"]') as HTMLButtonElement).click())
  expect(document.body.querySelector('[role="dialog"]')).not.toBeNull()
  await show(b)
  expect(document.body.querySelector('[role="dialog"]')).toBeNull()
  expect(fetcher.mock.calls.some(([url]) => String(url).includes('/history/restore'))).toBe(false)
})
