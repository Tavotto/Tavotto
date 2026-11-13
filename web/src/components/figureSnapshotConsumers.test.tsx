import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { CanvasThumb } from './CanvasThumb'
import { LayoutSnapshot } from './VersionDialog'
import { captureDocumentMoment } from '@/lib/momentCapture'
import { setCurrentProjectId } from '@/lib/session'
import { setEngineTransport } from '@/lib/engineTransport'
import { useDocumentStore } from '@/store/documentStore'
import { renderKeyOf, useRenderStore } from '@/store/renderStore'
import { clearVariantPngCache } from '@/hooks/useVariantPng'
import type { PreviewOwner } from '@/lib/previewOwner'
import { canvasToDoc, emptyProject, type FigureSnapshot, type PanelChartState, type PanelObject } from '@/types/document'
import chartVectors from '../../../tests/golden/chart_model_vectors.json'

globalThis.IS_REACT_ACT_ENVIRONMENT = true
const owner: PreviewOwner = { pj: 'A', key: 'document-A', still: () => true, refresh: () => owner }
const requests: { path: string; pj: string | null; header: string | null; body: Record<string, unknown> }[] = []
const created = new Map<string, Blob>()
let root: Root, host: HTMLDivElement, serial = 0
let respond: (body: Record<string, unknown>) => Promise<Response>

function reference(snapshotId = 'snapshot-A', sourceId = 'History.pdf'): FigureSnapshot {
  return { version: 1, sourceId, patchHash: 'same-patches', receiptId: 'same-receipt', snapshotId,
    executionOwner: { script: 'plot.py', run_config: null } }
}
function panel(kind: 'runtime' | 'pdf' = 'pdf'): PanelObject {
  const fileId = kind === 'runtime' ? 'runtime:plot.py#History' : 'History.pdf'
  return { id: 'history', type: 'panel', fileId, fileKind: kind,
    x: 0, y: 0, w: 80, h: 60, nativeW: 80, nativeH: 60, overrides: [], figureSnapshot: reference('snapshot-A', fileId) }
}
function png(body: Record<string, unknown>, value?: string) {
  const ref = body.figure_snapshot as FigureSnapshot
  return new Response(value ?? ref?.snapshotId ?? 'wrong-current-pixels', { headers: {
    'Content-Type': 'image/png', 'X-Tavotto-Figure-Snapshot': JSON.stringify(ref ?? null),
  } })
}
const imageHref = () => host.querySelector('image')?.getAttribute('href') ?? host.querySelector('img')?.getAttribute('src')
async function show(p: PanelObject) {
  await act(async () => { root.render(<CanvasThumb page={{ w: 100, h: 80 }} objects={[p]} owner={owner} />) })
}

beforeEach(() => {
  requests.length = 0; created.clear()
  setCurrentProjectId('A'); setEngineTransport(null); useRenderStore.getState().clear(); clearVariantPngCache()
  respond = async body => png(body)
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    const parsed = new URL(url, 'http://localhost')
    if (!init?.body) return Response.json({}, { status: 404 })
    const body = JSON.parse(String(init.body))
    requests.push({ path: parsed.pathname, pj: parsed.searchParams.get('pj'), header: new Headers(init.headers).get('X-Tavotto-Project'), body })
    if (parsed.pathname === '/api/engine/preview_png') return respond(body)
    return Response.json({}, { status: 404 })
  }))
  URL.createObjectURL = vi.fn(blob => { const url = `blob:archive-${++serial}`; created.set(url, blob); return url })
  URL.revokeObjectURL = vi.fn()
  host = document.createElement('div'); document.body.appendChild(host); root = createRoot(host)
})
afterEach(() => {
  act(() => root.unmount()); host.remove(); clearVariantPngCache(); useRenderStore.getState().clear()
  setCurrentProjectId(null); vi.unstubAllGlobals()
})

describe('归档缩略图消费完整历史引用', () => {
  it.each(['runtime', 'pdf'] as const)('%s零编辑历史面板仍读明确归档，不借当前同名图', async kind => {
    const p = panel(kind)
    await show(p)
    expect(requests).toHaveLength(1)
    expect(requests[0]).toMatchObject({ path: '/api/engine/preview_png', pj: 'A', header: 'A',
      body: { id: p.fileId, patches: [], w: 200, figure_snapshot: p.figureSnapshot } })
    expect(requests[0].body).not.toHaveProperty('with_manifest')
    expect(await created.get(imageHref()!)?.text()).toBe('snapshot-A')
  })

  it('同id同patch的历史A/B不共缓存，换回原完整引用才命中', async () => {
    const a = panel(), b = { ...a, figureSnapshot: reference('snapshot-B') }
    await show(a); const first = imageHref()
    await show(b)
    expect(requests).toHaveLength(2)
    expect(await created.get(imageHref()!)?.text()).toBe('snapshot-B')
    expect(imageHref()).not.toBe(first)
    await show({ ...a, figureSnapshot: { executionOwner: { run_config: null, script: 'plot.py' },
      snapshotId: 'snapshot-A', receiptId: 'same-receipt', patchHash: 'same-patches', sourceId: 'History.pdf', version: 1 } })
    expect(requests).toHaveLength(2)
    expect(imageHref()).toBe(first)
  })

  it('历史A迟到不能覆盖已经显示的历史B或填入B缓存', async () => {
    let finish!: (value: Response) => void
    respond = body => (body.figure_snapshot as FigureSnapshot)?.snapshotId === 'snapshot-A'
      ? new Promise(resolve => { finish = resolve }) : Promise.resolve(png(body))
    const a = panel(), b = { ...a, figureSnapshot: reference('snapshot-B') }
    await show(a); await show(b)
    const accepted = imageHref()
    await act(async () => { finish(png({ figure_snapshot: a.figureSnapshot })) })
    expect(imageHref()).toBe(accepted)
    expect(await created.get(imageHref()!)?.text()).toBe('snapshot-B')
    expect(URL.revokeObjectURL).toHaveBeenCalled()
  })

  it.each([null, { version: 1, sourceId: 'different.pdf' }])('显式坏ref %j不退普通文件或worker', async bad => {
    await show({ ...panel(), figureSnapshot: bad as never })
    expect(requests).toHaveLength(0)
    expect(imageHref()).toBeUndefined()
  })

  it('完整版本预览即使不要求重放overrides，也显示其归档而非磁盘', async () => {
    const project = emptyProject(); project.schema = 4; project.canvases[0].objects = [panel()]
    await act(async () => { await useDocumentStore.getState().switchDocument(project, 'archive-version', undefined, { format: 'v4' }) })
    requests.length = 0
    await act(async () => { root.render(<LayoutSnapshot doc={useDocumentStore.getState().doc} />) })
    expect(requests.filter(row => row.path === '/api/engine/preview_png')).toHaveLength(1)
    expect(await created.get(imageHref()!)?.text()).toBe('snapshot-A')
  })

  it('计算chart附件保原配对声明式请求，不把同对象的旧Figure引用混入', async () => {
    const vector = chartVectors.vectors.find(value => value.expect === 'ok')!
    const chart = structuredClone({ model: vector.model, binding: vector.binding }) as unknown as PanelChartState
    const p: PanelObject = { ...panel(), chart, chartSource: { schema: 1, sourceId: 'input.svg',
      bytesSha256: 'a'.repeat(64), sizeBytes: 100, sceneDigest: chart.model.scene_digest } }
    respond = async () => Response.json({ png: btoa('declared-chart'), chart_revision: 'actual-chart',
      manifest: { stem: 'chart', size_mm: [80, 60], elements: [] } })
    await act(async () => root.render(<CanvasThumb page={{ w: 100, h: 80 }} objects={[p]} owner={owner} editedPanel={() => p} />))
    expect(requests).toHaveLength(1)
    expect(requests[0].body).toMatchObject({ chart, chart_source: p.chartSource, with_manifest: true })
    expect(requests[0].body).not.toHaveProperty('figure_snapshot')
    expect(imageHref()).toBe(`data:image/png;base64,${btoa('declared-chart')}`)
  })

  it('明确null项目的离开归档快照不被当前B补齐', async () => {
    setCurrentProjectId('B')
    const project = emptyProject(); const p = panel(); project.canvases[0].objects = [p]
    const moment = captureDocumentMoment({ doc: canvasToDoc(project.canvases[0]), documentId: 'default-project',
      documentFormat: 'v4', activeCanvasId: project.activeCanvasId,
      projectMeta: { ...project.project, createdAt: project.createdAt } }, null, 1)
    expect(requests[0]).toMatchObject({ pj: null, header: null, body: { figure_snapshot: p.figureSnapshot } })
    expect(await moment.thumb?.get(p.id)?.png).toBe(`data:image/png;base64,${btoa('snapshot-A')}`)
  })

  it('离开快照冻结ref/pj/原序patch和PNG Promise，跨ABA后完成且不借无ref缓存SVG', async () => {
    const project = emptyProject(); const p = panel()
    p.overrides = [{ gid: 'line', prop: 'linewidth', value: 9 }, { gid: 'line', prop: 'linewidth', value: 3 }]
    project.canvases[0].objects = [p]
    const key = renderKeyOf(p)
    useRenderStore.setState({ byKey: { [key]: { fileId: p.fileId, projectId: 'A', svg: '<svg id="unproved-current"/>',
      manifest: null, status: 'ready', stale: false } as never }, latest: { [p.fileId]: key } })
    let finish!: (value: Response) => void
    respond = () => new Promise(resolve => { finish = resolve })
    const doc = canvasToDoc(project.canvases[0])
    const moment = captureDocumentMoment({ doc, documentId: 'leaving-A', documentFormat: 'v4', activeCanvasId: project.activeCanvasId,
      projectMeta: { ...project.project, createdAt: project.createdAt } }, 'A', 2)
    const source = moment.thumb?.get(p.id)
    expect(source?.svg).toBeUndefined(); expect(source?.url).toBeUndefined()
    expect(source?.png).toBeInstanceOf(Promise)
    expect(requests[0]).toMatchObject({ pj: 'A', header: 'A', body: { figure_snapshot: reference(), patches: p.overrides } })
    p.figureSnapshot = reference('mutated-after-capture'); p.overrides.reverse()
    setCurrentProjectId('B'); useRenderStore.getState().clear(); setCurrentProjectId('A')
    finish(png(requests[0].body, 'frozen-A'))
    expect(await source?.png).toBe(`data:image/png;base64,${btoa('frozen-A')}`)
    expect(requests).toHaveLength(1)
  })
})
