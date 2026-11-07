import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { captureDocumentMoment } from './momentCapture'
import { setCurrentProjectId } from './session'
import { useRenderStore } from '@/store/renderStore'
import { setEngineTransport } from './engineTransport'
import { canvasToDoc, emptyProject, type PanelChartState, type PanelObject } from '@/types/document'
import chartVectors from '../../../tests/golden/chart_model_vectors.json'

const requests: { pj: string | null; header: string | null; body: Record<string, unknown> }[] = []
let respond: (pj: string | null) => Promise<Response>

function state(chart = true) {
  const project = emptyProject()
  const vector = chartVectors.vectors.find(v => v.expect === 'ok')!
  const model = structuredClone({ model: vector.model, binding: vector.binding }) as unknown as PanelChartState
  const panel: PanelObject = {
    id: 'panel', type: 'panel', fileId: 'same.svg', fileKind: 'svg',
    nativeW: 100, nativeH: 80, x: 0, y: 0, w: 100, h: 80, overrides: [],
    ...(chart ? { chart: model, chartSource: { schema: 1 as const, sourceId: 'same.svg',
      bytesSha256: 'a'.repeat(64), sizeBytes: 100, sceneDigest: model.model.scene_digest } } : {}),
  }
  project.canvases[0].objects = [panel]
  return { doc: canvasToDoc(project.canvases[0]), documentId: 'owned-document',
    documentFormat: chart ? 'v4' as const : 'legacy' as const, activeCanvasId: project.activeCanvasId,
    projectMeta: { ...project.project, createdAt: project.createdAt } }
}

function frame(pj: string | null) {
  return Response.json({ png: btoa(pj ?? 'explicit-default'), chart_revision: `revision:${pj}`,
    manifest: { stem: 'same', size_mm: [100, 80], elements: [] } })
}

beforeEach(() => {
  requests.length = 0
  respond = async pj => frame(pj)
  setCurrentProjectId('B')
  setEngineTransport(null)
  useRenderStore.getState().clear()
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    const parsed = new URL(url, 'http://localhost')
    if (parsed.pathname === '/api/engine/render') return Response.json({ rev: 1,
      svg: `<svg xmlns="http://www.w3.org/2000/svg" data-owner="${parsed.searchParams.get('pj')}"><path stroke-width="9"/></svg>`,
      manifest: { stem: 'same', size_mm: [100, 80], elements: [] } })
    if (parsed.pathname !== '/api/engine/preview_png') return Response.json({}, { status: 404 })
    const pj = parsed.searchParams.get('pj')
    requests.push({ pj, header: new Headers(init?.headers).get('X-Tavotto-Project'), body: JSON.parse(String(init?.body)) })
    return respond(pj)
  }))
})

afterEach(() => {
  useRenderStore.getState().clear()
  setCurrentProjectId(null)
  vi.unstubAllGlobals()
})

describe('保存/离开快照的项目归属随实际图源同行', () => {
  it('current B + 文档 owner A：真实 capture → renderer → HTTP 仍只请求 A 的计算附件', async () => {
    const original = state()
    const moment = captureDocumentMoment(original, 'A', 7)
    expect(moment.identity.pj).toBe('A')
    expect(requests).toHaveLength(1)
    expect(requests[0]).toMatchObject({ pj: 'A', header: 'A', body: {
      id: 'same.svg', w: 360, chart_source: (original.doc.objects[0] as PanelObject).chartSource,
    } })
    expect(await moment.thumb?.get('panel')?.png).toBe(`data:image/png;base64,${btoa('A')}`)
  })

  it('明确 null owner 保原默认项目，不能被当前 B 补齐', async () => {
    const moment = captureDocumentMoment(state(), null, 1)
    expect(requests[0]).toMatchObject({ pj: null, header: null })
    expect(await moment.thumb?.get('panel')?.png).toBe(`data:image/png;base64,${btoa('explicit-default')}`)
  })

  it('普通同名素材的冻结 URL 同样绑定文档 A，不能取 B 的字节', () => {
    const moment = captureDocumentMoment(state(false), 'A', 7)
    const url = new URL(moment.thumb?.get('panel')?.url ?? '', 'http://localhost')
    expect(url.pathname).toBe('/api/file')
    expect(url.searchParams.get('id')).toBe('same.svg')
    expect(url.searchParams.get('pj')).toBe('A')
    expect(requests).toHaveLength(0)
  })

  it('明确离开快照跨 A→B→A 和清缓存仍完成原图 Promise，不借用之后的正文', async () => {
    let finish!: (response: Response) => void
    respond = () => new Promise(resolve => { finish = resolve })
    setCurrentProjectId('A')
    const original = state()
    const moment = captureDocumentMoment(original, 'A', 7)
    const png = moment.thumb?.get('panel')?.png
    setCurrentProjectId('B')
    useRenderStore.getState().clear()
    setCurrentProjectId('A')
    finish(frame('captured-A'))
    expect(await png).toBe(`data:image/png;base64,${btoa('captured-A')}`)
    expect(moment.identity.doc).toBe(original.doc)
    expect(moment.thumb?.get('panel')?.png).toBe(png)
    expect(requests[0]).toMatchObject({ pj: 'A', header: 'A' })
  })

  it('原 render 响应的已编辑 SVG 在 current 已 B、clear 尚未发生的窗口仍属于 A', async () => {
    setCurrentProjectId('A')
    const original = state(false)
    const panel = original.doc.objects[0] as PanelObject
    panel.overrides = [{ gid: 'line', prop: 'linewidth', value: 9 }]
    await useRenderStore.getState().render(panel.fileId, panel.overrides)
    const svg = Object.values(useRenderStore.getState().byKey).find(value => value.svg)?.svg
    expect(svg).toContain('data-owner="A"')
    setCurrentProjectId('B')
    const leaving = captureDocumentMoment(original, 'A', 1)
    expect(leaving.thumb?.get('panel')?.svg).toBe(svg)
    useRenderStore.getState().clear()
    await useRenderStore.getState().render(panel.fileId, panel.overrides)
    expect(Object.values(useRenderStore.getState().byKey).find(value => value.svg)?.svg).toContain('data-owner="B"')
    const older = captureDocumentMoment(original, 'A', 1)
    expect(older.thumb?.get('panel')?.svg).toBeUndefined()
    expect(new URL(older.thumb?.get('panel')?.url ?? '', 'http://localhost').searchParams.get('pj')).toBe('A')
    expect(leaving.thumb?.get('panel')?.svg).toBe(svg)
  })
})
