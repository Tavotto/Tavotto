import { beforeEach, expect, it, vi } from 'vitest'
import { settleLegendCorner } from './legendCornerSettle'
import { amendOverrides } from '@/store/actions'
import { useDocumentStore, type HistoryEntry } from '@/store/documentStore'
import { renderKeyOf, useRenderStore } from '@/store/renderStore'
import { useMountedPngStore } from '@/store/mountedPngStore'
import type { Manifest } from '@/lib/api'
import type { PanelObject } from '@/types/document'

vi.mock('@/store/actions', () => ({ amendOverrides: vi.fn() }))
vi.mock('@/store/svgPreviewStore', () => ({ retargetPreview: vi.fn() }))

beforeEach(() => {
  vi.clearAllMocks()
  useRenderStore.getState().clear()
  useMountedPngStore.setState({ byPanel: {} })
})

it('legend corner correction waits for the paired PNG and uses that measured box', () => {
  const panel = {
    id: 'panel', type: 'panel', fileId: 'F.pdf', overrides: [
      { gid: 'legend', prop: 'loc_frac', value: [.2, .4] },
    ],
  } as unknown as PanelObject
  const entry = {} as HistoryEntry
  useDocumentStore.setState((s) => ({ doc: { ...s.doc, objects: [panel] }, past: [entry], txn: null }))
  const canonical = { size_mm: [100, 80], elements: [
    { gid: 'legend', bbox: [.1, .2, .3, .2] },
  ] } as unknown as Manifest
  const paired = { ...canonical, elements: [
    { gid: 'legend', bbox: [.2, .3, .3, .2] },
  ] } as unknown as Manifest
  const key = renderKeyOf(panel)
  useRenderStore.getState().patch(key, {
    fileId: panel.fileId, rev: 1, status: 'ready', manifest: canonical,
    lastPatches: JSON.stringify(panel.overrides), svg: null,
    preview: { mode: 'raster', reason: 'svg_hard_limit', svg_bytes: 1, rasterized_artist_count: 0 },
  })
  const store = useMountedPngStore.getState()
  store.show(panel.id, key, 1, canonical, 'data:png')
  const stop = settleLegendCorner({ panelId: panel.id, gid: 'legend', corner: 'se', fixed: [.1, .2], entry })
  expect(amendOverrides).not.toHaveBeenCalled()
  store.loaded(panel.id, { key, rev: 1, source: canonical, url: 'data:png', manifest: paired })
  expect(amendOverrides).toHaveBeenCalledOnce()
  expect(amendOverrides).toHaveBeenCalledWith(panel.id, entry, [
    { gid: 'legend', prop: 'loc_frac', value: [.1, .3] },
  ])
  stop()
})
