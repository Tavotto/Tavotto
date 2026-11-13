import { beforeEach, expect, it, vi } from 'vitest'

let project: string | null = 'A'
vi.mock('@/lib/api', async (original) => ({
  ...(await original<typeof import('@/lib/api')>()),
  fetchPanelSourceFacts: vi.fn(),
  fetchPanels: vi.fn(),
}))
vi.mock('@/lib/session', async (original) => ({
  ...(await original<typeof import('@/lib/session')>()),
  currentProjectId: () => project,
}))

import { fetchPanelSourceFacts, fetchPanels, type PanelSourceFacts } from '@/lib/api'
import { useAssetStore, resetAssetLoadBookkeeping } from '@/store/assetStore'
import { useDocumentStore } from '@/store/documentStore'
import { buildExportRequest, originalAvailability } from '@/lib/exportRequest'
import { getOriginalOutputSpec } from '@/lib/originalSpec'
import { emptyProject, type PanelObject } from '@/types/document'
import { literal } from '@/i18n'
import { setEngineTransport, type EngineTransport } from '@/lib/engineTransport'

const panel: PanelObject = {
  type: 'panel', id: 'p', fileId: 'same.png', fileKind: 'raster',
  nativeW: 32, nativeH: 24, w: 90, h: 60, x: 3, y: 4, overrides: [],
}
const facts = (id = 'same.png', px = 64): PanelSourceFacts => ({
  id, stem: 'same', kind: 'raster', original_spec: {
    source_kind: 'raster', logical_w_mm: px * 25.4 / 600, logical_h_mm: 48 * 25.4 / 600,
    px_w: px, px_h: 48, dpi: 600, dpi_source: 'assumed', transparent: false, viewport_pt: null,
  },
})
function gate<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((a, b) => { resolve = a; reject = b })
  return { promise, resolve, reject }
}

beforeEach(async () => {
  setEngineTransport(null)
  project = 'A'
  vi.mocked(fetchPanelSourceFacts).mockReset()
  vi.mocked(fetchPanels).mockReset()
  resetAssetLoadBookkeeping()
  useAssetStore.getState().clear()
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_sources')
  useDocumentStore.getState().commit(literal('准备'), (d) => { d.objects = [{ ...panel }] })
})

it('panel alone cannot grant source availability; exact positive metadata enables the same request builder', async () => {
  expect(originalAvailability('same.png', { panel }).ok).toBe(false)
  vi.mocked(fetchPanelSourceFacts).mockResolvedValue(facts())
  await useAssetStore.getState().ensureOriginalSource('same.png')
  expect(fetchPanelSourceFacts).toHaveBeenCalledWith('same.png', 'A')
  expect(useAssetStore.getState().panels).toEqual([])
  expect(useAssetStore.getState().byId).toEqual({})
  expect(originalAvailability('same.png', { panel }).ok).toBe(true)
  const spec = getOriginalOutputSpec('same.png', panel)!
  // ADR0028 order and placement remain unchanged; only actual source grid/density fill missing facts.
  expect([spec.widthMm, spec.heightMm, spec.origin]).toEqual([32, 24, 'document'])
  expect([spec.pixelWidth, spec.pixelHeight, spec.dpi, spec.dpiSource]).toEqual([64, 48, 600, 'assumed'])
  const { request } = buildExportRequest({
    scope: 'original', figureId: 'same.png', panel, spec, formats: ['png'], filename: 'exact',
    ppi: 600, documentId: 'd_sources', doc: useDocumentStore.getState().doc,
  })
  expect(request.original).toMatchObject({ figure_id: 'same.png', w_mm: 32, h_mm: 24, px_w: 64, px_h: 48 })
})

it('unknown, network failure and backend negative facts cannot self-authorize a panel', async () => {
  vi.mocked(fetchPanelSourceFacts).mockRejectedValue(new Error('404'))
  expect(await useAssetStore.getState().ensureOriginalSource('same.png')).toBeNull()
  expect(originalAvailability('same.png', { panel }).ok).toBe(false)
  await useAssetStore.getState().ensureOriginalSource('same.png')
  expect(fetchPanelSourceFacts).toHaveBeenCalledTimes(1)
})

it('an exact response for another logical id is not evidence for this one', async () => {
  vi.mocked(fetchPanelSourceFacts).mockResolvedValue(facts('other.png'))
  await useAssetStore.getState().ensureOriginalSource('same.png')
  expect(originalAvailability('same.png', { panel }).ok).toBe(false)
})

it('concurrent reads of one exact id are coalesced without changing the inventory', async () => {
  const pending = gate<PanelSourceFacts>()
  vi.mocked(fetchPanelSourceFacts).mockReturnValue(pending.promise)
  const first = useAssetStore.getState().ensureOriginalSource('same.png')
  const second = useAssetStore.getState().ensureOriginalSource('same.png')
  expect(fetchPanelSourceFacts).toHaveBeenCalledTimes(1)
  pending.resolve(facts())
  expect(await first).toEqual(await second)
  expect(useAssetStore.getState().panels).toEqual([])
})

it('A→B→A clears identity and prevents a late old positive from overwriting the new A result', async () => {
  const old = gate<PanelSourceFacts>()
  vi.mocked(fetchPanelSourceFacts).mockReturnValueOnce(old.promise)
  const late = useAssetStore.getState().ensureOriginalSource('same.png')
  project = 'B'
  useAssetStore.getState().clear()
  project = 'A'
  useAssetStore.getState().clear()
  vi.mocked(fetchPanelSourceFacts).mockResolvedValueOnce(facts('same.png', 21))
  await useAssetStore.getState().ensureOriginalSource('same.png')
  old.resolve(facts())
  expect(await late).toBeNull()
  expect(getOriginalOutputSpec('same.png', panel)?.pixelWidth).toBe(21)
})

it('inventory refresh invalidates precise positives and pending negatives before a new exact request', async () => {
  vi.mocked(fetchPanelSourceFacts).mockResolvedValueOnce(facts())
  await useAssetStore.getState().ensureOriginalSource('same.png')
  const old = gate<PanelSourceFacts>()
  vi.mocked(fetchPanelSourceFacts).mockReturnValueOnce(old.promise)
  const late = useAssetStore.getState().ensureOriginalSource('other.png')
  vi.mocked(fetchPanels).mockResolvedValue({ panels: [], figures_dir: '/project', unsupported: [] })
  await useAssetStore.getState().load({ force: true })
  expect(originalAvailability('same.png', { panel }).ok).toBe(false)
  vi.mocked(fetchPanelSourceFacts).mockResolvedValueOnce(facts('other.png', 33))
  await useAssetStore.getState().ensureOriginalSource('other.png')
  old.reject(new Error('old failure'))
  expect(await late).toBeNull()
  expect(useAssetStore.getState().originalSources['other.png']?.original_spec.px_w).toBe(33)
})

it('runtime and current inventory positives do not probe arbitrary disk paths', async () => {
  useAssetStore.setState({ byId: { 'same.pdf': { id: 'same.pdf' } } } as never)
  expect(await useAssetStore.getState().ensureOriginalSource('runtime:opaque')).toBeNull()
  expect(await useAssetStore.getState().ensureOriginalSource('same.pdf')).toBeNull()
  expect(fetchPanelSourceFacts).not.toHaveBeenCalled()
})

it('an alternate host with no exact-facts contract never calls HTTP or consumes an old HTTP positive', async () => {
  vi.mocked(fetchPanelSourceFacts).mockResolvedValue(facts())
  await useAssetStore.getState().ensureOriginalSource('same.png')
  expect(originalAvailability('same.png', { panel }).ok).toBe(true)
  const restore = setEngineTransport({
    render: vi.fn(), previewPngUrl: vi.fn(), panelSrc: () => null,
  } as EngineTransport)
  try {
    expect(await useAssetStore.getState().ensureOriginalSource('other.png')).toBeNull()
    expect(fetchPanelSourceFacts).toHaveBeenCalledTimes(1)
    expect(originalAvailability('same.png', { panel }).ok).toBe(false)
  } finally { restore() }
})
