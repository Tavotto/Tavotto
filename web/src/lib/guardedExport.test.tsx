import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ExportDialog } from '@/components/ExportDialog'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { formatMessage, literal } from '@/i18n'
import { engineErrorMsg, type ExportJob, type ExportRequest } from './api'
import { ArtifactValidationError, type ArtifactValidation } from './artifactValidation'
import { buildExportRequest, originalAvailability, type ExportRequestInput } from './exportRequest'
import { contextFigureId, findExportPanel, listExportableFigures } from './exportFigures'
import { getOriginalOutputSpec } from './originalSpec'
import { useAssetStore } from '@/store/assetStore'
import { useDocumentStore } from '@/store/documentStore'
import {
  applyExportJob, liveRevision, prepareExport, resetExportState, runExport,
  useExportStore, validateExportRequest,
} from '@/store/exportStore'
import { exactPanelRender, renderKeyOf, useRenderStore } from '@/store/renderStore'
import { useRuntimeAssetStore } from '@/store/runtimeAssetStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { useWorkspaceStore } from '@/store/workspace'
import { seedExactRender } from '@/test/renderFixtures'
import { emptyProject, type PanelObject } from '@/types/document'

vi.mock('@/lib/api', async (original) => ({
  ...(await original<typeof import('@/lib/api')>()),
  postTelemetryEvent: vi.fn(async () => ({ accepted: true })),
  fetchExportDefaultsRemote: vi.fn(async () => undefined),
}))

globalThis.IS_REACT_ACT_ENVIRONMENT = true
const frame = { gid: 'figure', prop: 'frame', value: 'figsize' }
const guard: ArtifactValidation = {
  version: 1, requiredFrame: 'figsize', sourceId: 'plot.png',
  bytesSha256: 'a'.repeat(64), sizeBytes: 123,
}
const panel = (id = 'a', overrides = [frame]): PanelObject => ({
  id, type: 'panel', fileId: 'plot.png', fileKind: 'raster', script: 'plot.py',
  x: 7, y: 9, w: 40, h: 30, nativeW: 50.8, nativeH: 25.4,
  pxW: 600, pxH: 300, overrides, artifactValidation: guard,
})
const editedPanel = (id: string, size: number) => panel(id, [
  frame, { gid: 'figure', prop: 'size_inches', value: String(size) },
])
const state = () => useDocumentStore.getState()
const put = (panels: PanelObject[]) => state().commit(literal('Test panels'), (doc) => {
  doc.objects = panels
})
const currentPanel = (id: string) => state().doc.objects.find((o) => o.id === id) as PanelObject
const inputOf = (over: Partial<ExportRequestInput> = {}): ExportRequestInput => ({
  scope: 'canvas', formats: ['png'], filename: 'export', ppi: 300,
  documentId: state().documentId, doc: state().doc, ...over,
})
const originalInput = (id: string): ExportRequestInput => {
  const chosen = currentPanel(id)
  return inputOf({
    scope: 'original', figureId: chosen.fileId, panel: chosen,
    spec: getOriginalOutputSpec(chosen.fileId, chosen),
  })
}
const doneJob = (over: Partial<ExportJob> = {}): ExportJob => ({
  job_id: 'export-job', status: 'done', outputs: [], warnings: [], conflicts: [], error: null, ...over,
})
const seed = (chosen: PanelObject, size: [number, number]) => {
  seedExactRender(chosen, { stem: 'plot', size_mm: size, elements: [] })
  useRenderStore.getState().patch(renderKeyOf(chosen), { artifactValidation: chosen.artifactValidation })
}
const hidePngBehindPdf = () => {
  const pdf = { ...useAssetStore.getState().byId['plot.png'], id: 'plot.pdf', kind: 'pdf' }
  useAssetStore.setState({ panels: [pdf], byId: { 'plot.pdf': pdf } } as never)
}
let exportCalls: { url: string; body: ExportRequest }[]
let root: Root | null
let container: HTMLDivElement | null

beforeEach(async () => {
  root = null
  container = null
  localStorage.clear()
  resetExportState()
  exportCalls = []
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const body = JSON.parse(String(init?.body ?? '{}'))
    if (url.includes('/api/export')) exportCalls.push({ url, body })
    const result = url.includes('/api/export/validate')
      ? { ok: true, artifact_sources: { 'plot.png': {
          render_policy: 'selected-figsize-v1', bytes_sha256: guard.bytesSha256,
          size_bytes: guard.sizeBytes, origin: 'static', kind: 'png', source_id: '/figs/plot.png',
        } } }
      : url.includes('/api/export') ? doneJob() : { panels: [], styles: [], specs: [] }
    return new Response(JSON.stringify(result), {
      status: 200, headers: { 'Content-Type': 'application/json' },
    })
  }))
  await state().switchDocument(emptyProject(), 'guarded-export')
  useRenderStore.setState({ byKey: {}, latest: {}, recent: {}, tracked: {}, building: {} })
  useWorkspaceStore.setState({ mode: 'layout', activePanelId: null })
  useSelectionStore.getState().clear()
  useRuntimeAssetStore.setState({ assets: [], byId: {}, previewNonce: {} })
  const info = {
    id: 'plot.png', kind: 'raster', mtime: 1, script: 'plot.py',
    native_w_mm: 50.8, native_h_mm: 25.4,
    original_spec: {
      source_kind: 'raster', logical_w_mm: 50.8, logical_h_mm: 25.4,
      px_w: 600, px_h: 300, dpi: 300, dpi_source: 'metadata',
      viewport_pt: null, transparent: true,
    },
  }
  useAssetStore.setState({ panels: [info], byId: { 'plot.png': info } } as never)
  useUiStore.getState().setExportOpen(false)
})

afterEach(async () => {
  if (root) await act(async () => root!.unmount())
  container?.remove()
  resetExportState()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

async function mountClosed() {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  await act(async () => root!.render(<TooltipProvider><ExportDialog /></TooltipProvider>))
}
const click = async (selector: string) => {
  const button = document.querySelector<HTMLButtonElement>(selector)
  expect(button).not.toBeNull()
  await act(async () => button!.click())
}

const invalidPanels = [
  { name: 'null guard', value: { ...panel(), artifactValidation: null } as unknown as PanelObject, reason: 'invalid_guard' },
  { name: 'undefined guard', value: { ...panel(), artifactValidation: undefined }, reason: 'invalid_guard' },
  { name: 'unknown version', value: { ...panel(), artifactValidation: { ...guard, version: 2 } } as unknown as PanelObject, reason: 'invalid_guard' },
  { name: 'malformed hash', value: { ...panel(), artifactValidation: { ...guard, bytesSha256: 'invalid' } }, reason: 'invalid_guard' },
  { name: 'missing frame', value: panel('a', []), reason: 'frame_changed' },
  { name: 'changed effective frame', value: panel('a', [frame, { ...frame, value: 'savefig' }]), reason: 'frame_changed' },
  { name: 'relinked source', value: { ...panel(), fileId: 'changed.png' }, reason: 'source_changed' },
]

describe('saved guarded export refusal', () => {
  it.each(invalidPanels)('$name keeps closed/open dialog usable and refuses without an export request', async ({ value, reason }) => {
    put([value])
    const before = structuredClone(state().doc)
    await mountClosed()
    expect(document.querySelector('[data-export-start]')).toBeNull()
    await act(async () => useUiStore.getState().setExportOpen(true))
    const refused = document.querySelector('[data-export-refusal]')
    expect(refused?.textContent).toBe(formatMessage(engineErrorMsg(new ArtifactValidationError(reason))))
    expect(document.querySelector<HTMLButtonElement>('[data-export-start]')?.disabled).toBe(true)
    await click('[data-export-start]')
    const prepared = prepareExport(inputOf())
    expect(prepared.error?.reason).toBe(reason)
    expect(prepared.request).toBeNull()
    await expect(validateExportRequest(inputOf())).resolves.toBeNull()
    await expect(runExport(inputOf())).resolves.toBeNull()
    await expect(runExport(originalInput(value.id))).resolves.toBeNull()
    expect(useExportStore.getState().startError).toEqual({
      code: 'start_failed', message: engineErrorMsg(new ArtifactValidationError(reason)),
    })
    expect(useExportStore.getState().running).toBe(false)
    expect(exportCalls).toEqual([])
    expect(state().doc).toEqual(before)
  })

  it('original scope refuses an invalid selected variant, even when a valid same-file panel comes first', async () => {
    const invalid = { ...editedPanel('b', 2), artifactValidation: { ...guard, bytesSha256: 'bad' } }
    put([panel('a'), invalid])
    useWorkspaceStore.setState({ mode: 'fast_edit', activePanelId: 'b' })
    await mountClosed()
    await act(async () => useUiStore.getState().setExportOpen(true))
    expect(document.querySelector('[data-export-refusal]')).not.toBeNull()
    await click('[data-export-start]')
    await expect(runExport(originalInput('b'))).resolves.toBeNull()
    expect(exportCalls).toEqual([])
  })
})

describe('exact original export variant', () => {
  it('a guarded PNG hidden behind PDF reaches original export preflight with honest saved metadata', async () => {
    put([panel()])
    hidePngBehindPdf()
    const chosen = currentPanel('a')
    expect(originalAvailability(chosen.fileId, { panel: chosen })).toMatchObject({
      ok: true,
      spec: { pixelWidth: 600, pixelHeight: 300, dpiSource: 'derived', transparent: null, stale: true },
    })
    useSelectionStore.getState().set(['a'])
    await mountClosed()
    await act(async () => useUiStore.getState().setExportOpen(true))
    await click('[data-onboarding-anchor="export-scope"] [data-value="original"]')
    const confirm = document.querySelector<HTMLInputElement>('[data-export-confirm]')
    if (confirm && !confirm.checked) await act(async () => confirm.click())
    expect(document.querySelector<HTMLButtonElement>('[data-export-start]')?.disabled).toBe(false)
    await click('[data-export-start]')
    const published = exportCalls.find(call => call.url.includes('/api/export/start'))!
    expect(published.body.original).toMatchObject({
      figure_id: 'plot.png', source_policy: 'selected-figsize-v1',
      expected_source: { bytes_sha256: guard.bytesSha256, size_bytes: guard.sizeBytes },
      source_kind: 'raster', px_w: 600, px_h: 300,
    })
    expect(published.body.original?.overrides).toBeUndefined()
    const validated = exportCalls.findIndex(call => call.url.includes('/api/export/validate'))
    expect(validated).toBeGreaterThanOrEqual(0)
    expect(validated).toBeLessThan(exportCalls.indexOf(published))
    expect(useAssetStore.getState().byId['plot.png']).toBeUndefined()
  })

  it('hidden sources without a matching valid guard remain unavailable', () => {
    hidePngBehindPdf()
    const legacy = panel()
    delete legacy.artifactValidation
    for (const chosen of [legacy, ...invalidPanels.map(p => p.value)]) {
      expect(originalAvailability('plot.png', { panel: chosen }).ok).toBe(false)
    }
  })

  it('a hidden guarded PNG with changed bytes refuses preflight visibly without publishing', async () => {
    put([panel()])
    hidePngBehindPdf()
    const chosen = currentPanel('a')
    seed(chosen, [50.8, 25.4])
    const before = structuredClone(state().doc)
    useSelectionStore.getState().set(['a'])
    await mountClosed()
    await act(async () => useUiStore.getState().setExportOpen(true))
    await click('[data-onboarding-anchor="export-scope"] [data-value="original"]')
    const confirm = document.querySelector<HTMLInputElement>('[data-export-confirm]')
    if (confirm && !confirm.checked) await act(async () => confirm.click())
    const existingFetch = globalThis.fetch
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (!String(input).includes('/api/export/validate')) return existingFetch(input, init)
      exportCalls.push({ url: String(input), body: JSON.parse(String(init?.body)) })
      return new Response(JSON.stringify({
        error: 'Source changed', code: 'artifact_source_unavailable', params: { reason: 'source_changed' },
      }), { status: 409, headers: { 'Content-Type': 'application/json' } })
    }))
    await click('[data-export-start]')
    expect(exportCalls.some(call => call.url.includes('/api/export/validate'))).toBe(true)
    expect(exportCalls.some(call => call.url.includes('/api/export/start'))).toBe(false)
    expect(document.querySelector('[data-export-start-error]')?.textContent)
      .toContain(formatMessage(engineErrorMsg(new ArtifactValidationError('source_changed'))))
    expect(exactPanelRender(useRenderStore.getState(), chosen)).toBeNull()
    expect(state().doc).toEqual(before)
  })

  it('uses fast edit, primary selection and first selected panel precedence for A/B/A', () => {
    put([editedPanel('a', 1), editedPanel('b', 2)])
    seed(currentPanel('a'), [70, 40])
    seed(currentPanel('b'), [90, 60])
    useSelectionStore.getState().set(['a', 'b'])
    expect(findExportPanel('plot.png')?.panel.id).toBe('b')
    for (const id of ['a', 'b', 'a']) {
      useWorkspaceStore.setState({ mode: 'fast_edit', activePanelId: id })
      const figures = listExportableFigures()
      const target = findExportPanel(contextFigureId(figures)!)!
      const spec = getOriginalOutputSpec('plot.png', target.panel)!
      expect(spec.widthMm).toBe(id === 'a' ? 70 : 90)
      const original = buildExportRequest(inputOf({
        scope: 'original', figureId: 'plot.png', panel: target.panel, spec,
      })).request.original!
      expect(original.overrides).toEqual(currentPanel(id).overrides)
      expect(original.w_mm).toBe(spec.widthMm)
      expect(original.source_policy).toBe('selected-figsize-v1')
    }
    useWorkspaceStore.setState({ mode: 'layout', activePanelId: null })
    state().commit(literal('Text selection'), (doc) => {
      doc.objects.push({ id: 'text', type: 'text', text: 'label', x: 0, y: 0, w: 10, h: 5, sizePt: 9, bold: false, color: '#000000', align: 'left' })
    })
    useSelectionStore.getState().set(['b', 'a', 'text'])
    expect(findExportPanel('plot.png')?.panel.id).toBe('b')
  })

  it('dialog starts the chosen B request and updates to A without reusing B metadata', async () => {
    put([editedPanel('a', 1), editedPanel('b', 2)])
    seed(currentPanel('a'), [70, 40])
    seed(currentPanel('b'), [90, 60])
    useSelectionStore.getState().set(['b'])
    await mountClosed()
    await act(async () => useUiStore.getState().setExportOpen(true))
    await click('[data-onboarding-anchor="export-scope"] [data-value="original"]')
    for (const id of ['b', 'a']) {
      await act(async () => useSelectionStore.getState().set([id]))
      const confirm = document.querySelector<HTMLInputElement>('[data-export-confirm]')
      if (confirm && !confirm.checked) await act(async () => confirm.click())
      expect(document.querySelector<HTMLButtonElement>('[data-export-start]')?.disabled).toBe(false)
      await click('[data-export-start]')
      const request = exportCalls.filter((call) => call.url.includes('/api/export/start')).at(-1)!.body
      expect(request.original?.overrides).toEqual(currentPanel(id).overrides)
      expect(request.original?.w_mm).toBe(id === 'a' ? 70 : 90)
    }
  })

  it('live revision stays with the captured object across selection changes, edits and canvas switches', () => {
    put([editedPanel('a', 1), editedPanel('b', 2)])
    const input = originalInput('b')
    const before = liveRevision(input)
    expect(before).toBe(buildExportRequest(input).revision)
    useSelectionStore.getState().set(['a'])
    state().commit(literal('Edit A'), (doc) => {
      (doc.objects[0] as PanelObject).overrides.push({ gid: 'title', prop: 'text', value: 'A' })
    })
    expect(liveRevision(input)).toBe(before)
    state().addCanvas('Other')
    expect(liveRevision(input)).toBe(before)
    state().switchCanvas(state().canvases.find((canvas) => canvas.id !== state().activeCanvasId)!.id)
    state().commit(literal('Edit B'), (doc) => {
      (doc.objects.find((o) => o.id === 'b') as PanelObject).overrides.push({ gid: 'title', prop: 'text', value: 'B' })
    })
    expect(liveRevision(input)).not.toBe(before)
  })

  it.each(['remove', 'relink'] as const)('a captured panel that is later %s does not fall back to same-file A', (change) => {
    put([editedPanel('a', 1), editedPanel('b', 2)])
    const input = originalInput('b')
    state().commit(literal('Change B'), (doc) => {
      if (change === 'remove') doc.objects = doc.objects.filter((o) => o.id !== 'b')
      else (doc.objects.find((o) => o.id === 'b') as PanelObject).fileId = 'other.png'
    })
    expect(findExportPanel('plot.png', 'b')).toBeNull()
    expect(liveRevision(input)).toBeNull()
    expect(prepareExport(input).request?.original?.overrides).toEqual(input.panel!.overrides)
  })

  it('FRAME-only original keeps PNG pixels, density and alpha; edited variants keep their exact render size', () => {
    put([panel()])
    seed(currentPanel('a'), [50.8, 25.4])
    const input = originalInput('a')
    expect(input.spec).toMatchObject({ sourceKind: 'raster', pixelWidth: 600, pixelHeight: 300, dpi: 300, transparent: true })
    expect(buildExportRequest(input).request.original).toMatchObject({
      source_policy: 'selected-figsize-v1', expected_source: { bytes_sha256: guard.bytesSha256, size_bytes: 123 },
      px_w: 600, px_h: 300, source_kind: 'raster',
    })
    expect(buildExportRequest(input).request.original?.overrides).toBeUndefined()
  })

  it('a refused guarded render is not a source for original-size metadata', () => {
    put([editedPanel('a', 1)])
    seed(currentPanel('a'), [90, 60])
    const key = Object.keys(useRenderStore.getState().byKey)[0]
    useRenderStore.getState().patch(key, { stale: true })
    expect(getOriginalOutputSpec('plot.png', currentPanel('a'))?.widthMm).toBe(50.8)
  })

  it('output-level source failure revokes exact selected geometry even in a partial job', () => {
    put([editedPanel('a', 1), editedPanel('b', 2)])
    seed(currentPanel('a'), [70, 40])
    seed(currentPanel('b'), [90, 60])
    useExportStore.setState({ ownedJobId: 'export-job', lastInput: originalInput('b') })
    applyExportJob(doneJob({ status: 'partial', outputs: [{
      format: 'png', name: null, url: null, bytes: 0, dimensions: { px: null, mm: null },
      vector: false, status: 'failed', replaced: false,
      error: { code: 'artifact_source_unavailable', params: { reason: 'source_changed' } },
    }] }))
    expect(exactPanelRender(useRenderStore.getState(), currentPanel('a'))).toBeNull()
    expect(exactPanelRender(useRenderStore.getState(), currentPanel('b'))).toBeNull()
  })
})

describe('background export failures identify only unresolved submitted variants',()=>{
 const color={gid:'figure',prop:'facecolor',value:'red'}
 const owner={file_id:'plot.png',bytes_sha256:guard.bytesSha256,size_bytes:guard.sizeBytes}
 it.each(['sync','background'])('%s refusal preserves an explicit same-source variant',async mode=>{
  const ambiguous=panel('ambiguous',[frame,color])
  const good=panel('good',[frame,color,{gid:'figure',prop:'transparent',value:false} as never])
  put([ambiguous,good]);seed(ambiguous,[50.8,25.4]);seed(good,[50.8,25.4])
  const before=state().doc, exact=exactPanelRender(useRenderStore.getState(),good)
  const error={code:'artifact_source_unavailable',params:{reason:'background_visibility_required',...owner},recoverable:false}
  if(mode==='sync') {
   vi.mocked(fetch).mockResolvedValueOnce(new Response(JSON.stringify(error),{status:409,headers:{'Content-Type':'application/json'}}))
   await runExport(inputOf())
  } else {
   useExportStore.setState({ownedJobId:'export-job',lastInput:inputOf()})
   applyExportJob(doneJob({status:'failed',error}))
  }
  expect(exactPanelRender(useRenderStore.getState(),ambiguous)).toBeNull()
  expect(exactPanelRender(useRenderStore.getState(),good)).toBe(exact);expect(state().doc).toBe(before)
 })
 it.each([{}, {...owner,file_id:'other.png'}, {...owner,bytes_sha256:'b'.repeat(64)}])('missing or mismatched owner does not guess cache invalidation: %j',params=>{
  const ambiguous=panel('ambiguous',[frame,color]);put([ambiguous]);seed(ambiguous,[50.8,25.4])
  const exact=exactPanelRender(useRenderStore.getState(),ambiguous)
  useExportStore.setState({ownedJobId:'export-job',lastInput:inputOf()})
  applyExportJob(doneJob({status:'failed',error:{code:'artifact_source_unavailable',params:{reason:'background_visibility_required',...params},recoverable:false}}))
  expect(exactPanelRender(useRenderStore.getState(),ambiguous)).toBe(exact)
  expect(useExportStore.getState().job?.error?.params?.reason).toBe('background_visibility_required')
 })
})
