import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { CanvasThumb } from './CanvasThumb'
import { beginRestore, settleRestore, pinDocumentOwner, resetRestoreGateForTests, useDocumentStore } from '@/store/documentStore'
import { useRenderStore } from '@/store/renderStore'
import { clearVariantPngCache } from '@/hooks/useVariantPng'
import { setCurrentProjectId } from '@/lib/session'
import { emptyProject, type PanelChartState, type PanelObject } from '@/types/document'
import chartVectors from '../../../tests/golden/chart_model_vectors.json'
import { LayoutSnapshot } from './VersionDialog'

globalThis.IS_REACT_ACT_ENVIRONMENT = true
let host: HTMLDivElement
let root: Root
const requests: { pj: string | null; header: string | null; body: Record<string, unknown> }[] = []
const runtimeRequests: { pj: string | null; header: string | null; body: Record<string, unknown> }[] = []
let serial = 0
let previewResponse: ((pj: string | null, body: Record<string, unknown>) => Promise<Response>) | undefined

function project(chart: boolean) {
  const value = emptyProject()
  value.schema = 4
  const vector = chartVectors.vectors.find(v => v.expect === 'ok')!
  const model = structuredClone({ model: vector.model, binding: vector.binding }) as unknown as PanelChartState
  const panel: PanelObject = { id: 'panel', type: 'panel', fileId: 'same.svg', fileKind: 'svg',
    nativeW: 100, nativeH: 80, x: 0, y: 0, w: 100, h: 80,
    overrides: [{ gid: 'line', prop: 'linewidth', value: 5 }],
    ...(chart ? { chart: model, chartSource: { schema: 1 as const, sourceId: 'same.svg',
      bytesSha256: 'a'.repeat(64), sizeBytes: 100, sceneDigest: model.model.scene_digest } } : {}),
  }
  value.canvases[0].objects = [panel]
  return value
}

beforeEach(() => {
  resetRestoreGateForTests()
  setCurrentProjectId('A')
  useRenderStore.getState().clear()
  clearVariantPngCache()
  requests.length = 0
  runtimeRequests.length = 0
  previewResponse = undefined
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    const parsed = new URL(url, 'http://localhost')
    if (parsed.pathname === '/api/runtime/preview') {
      runtimeRequests.push({ pj: parsed.searchParams.get('pj'),
        header: new Headers(init?.headers).get('X-Tavotto-Project'), body: JSON.parse(String(init?.body)) })
      return new Response('runtime-state-A', { headers: { 'X-Tavotto-Preview-Exact': '1' } })
    }
    if (parsed.pathname === '/api/engine/preview_png') {
      const pj = parsed.searchParams.get('pj')
      const body = JSON.parse(String(init?.body))
      requests.push({ pj, header: new Headers(init?.headers).get('X-Tavotto-Project'), body })
      if (previewResponse) return previewResponse(pj, body)
      return body.with_manifest
        ? Response.json({ png: btoa(pj ?? 'default'), chart_revision: `chart:${pj}`,
          manifest: { stem: 'same', size_mm: [100, 80], elements: [] } })
        : new Response(`pixels:${pj}`)
    }
    return Response.json({}, { status: 404 })
  }))
  vi.stubGlobal('URL', URL)
  URL.createObjectURL = vi.fn(() => `blob:preview-${++serial}`)
  URL.revokeObjectURL = vi.fn()
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})

afterEach(() => {
  act(() => root.unmount())
  host.remove()
  clearVariantPngCache()
  setCurrentProjectId(null)
  vi.unstubAllGlobals()
})

describe('活动缩略图的数据与文档 owner 一起跨切换窗口', () => {
  it.each([true, false])('current 已 B、canvas 仍 A 时零 A 正文+B 项目请求（chart=%s）', async chart => {
    await act(async () => { await useDocumentStore.getState().switchDocument(project(chart), `preview-${++serial}`) })
    const doc = useDocumentStore.getState().doc
    const show = async (pxWidth: number) => {
      await act(async () => { root.render(<CanvasThumb page={doc.page} objects={doc.objects}
        pxWidth={pxWidth} editedPanel={object => object as PanelObject} />) })
    }
    await show(200)
    expect(requests.some(r => r.pj === 'A')).toBe(true)
    requests.length = 0
    pinDocumentOwner()
    setCurrentProjectId('B')
    await show(1600)
    expect(requests).toEqual([])
    expect(requests.filter(r => r.pj === 'B' || r.header === 'B')).toEqual([])
    const images = [...host.querySelectorAll('[data-thumb-panel]')]
    expect(images.every(image => !image.getAttribute('href')?.includes('pj=B'))).toBe(true)
  })

  it.each([true, false])('在切换窗口首次挂载旧 A 正文仍发送 A，不能用此刻 B 认领（chart=%s）', async chart => {
    await act(async () => { await useDocumentStore.getState().switchDocument(project(chart), `first-${++serial}`) })
    const doc = useDocumentStore.getState().doc
    // switchDocument 的离开快照属于上一份正文；本判据只量随后首次挂载的请求。
    requests.length = 0
    pinDocumentOwner()
    setCurrentProjectId('B')
    await act(async () => root.render(<CanvasThumb page={doc.page} objects={doc.objects}
      pxWidth={600} editedPanel={object => object as PanelObject} />))
    expect(requests).toHaveLength(1)
    expect(requests[0]).toMatchObject({ pj: 'A', header: 'A' })
  })

  it('同一已装入正文被恢复门临时拦住、确认保留后重新预览；跨项目旧正文不能续期', async () => {
    await act(async () => { await useDocumentStore.getState().switchDocument(project(false), `restore-${++serial}`) })
    const doc = useDocumentStore.getState().doc
    await act(async () => root.render(<CanvasThumb page={doc.page} objects={doc.objects}
      pxWidth={400} editedPanel={object => object as PanelObject} />))
    expect(requests.filter(r => r.pj === 'A')).toHaveLength(1)
    await act(async () => { beginRestore('A') })
    expect(host.querySelector('[data-thumb-panel]')?.getAttribute('href')).toBeFalsy()
    await act(async () => {
      const token = beginRestore('A')
      settleRestore(token, { state: 'ready', reason: 'superseded' })
    })
    expect(useDocumentStore.getState().doc).toBe(doc)
    expect(requests.filter(r => r.pj === 'A')).toHaveLength(2)
    expect(host.querySelector('[data-thumb-panel]')?.getAttribute('href')).toContain('blob:preview-')
    pinDocumentOwner()
    await act(async () => {
      setCurrentProjectId('B')
      const token = beginRestore('B')
      settleRestore(token, { state: 'needs_user', issue: { pj: 'B', gen: token.gen, kind: 'unavailable', ref: null, alternatives: [] } })
    })
    expect(requests.filter(r => r.pj === 'B')).toEqual([])
    expect(host.querySelector('[data-thumb-panel]')?.getAttribute('href')).toBeFalsy()
  })

  it('历史快照不必等于当前正文引用，正常仍按 A 预览，切换后不能变成 B', async () => {
    await act(async () => { await useDocumentStore.getState().switchDocument(project(false), `history-${++serial}`) })
    const historical = structuredClone(useDocumentStore.getState().doc)
    ;(historical.objects[0] as PanelObject).overrides[0].value = 17
    await act(async () => root.render(<LayoutSnapshot doc={historical} renderOverrides displayWidth={600} />))
    expect(requests.some(r => r.pj === 'A' && (r.body.patches as {value:number}[])[0].value === 17)).toBe(true)
    requests.length = 0
    pinDocumentOwner()
    setCurrentProjectId('B')
    await act(async () => root.render(<LayoutSnapshot doc={historical} renderOverrides displayWidth={1600} />))
    expect(requests).toEqual([])
    expect(host.querySelector('img')?.getAttribute('src')).toBeFalsy()
  })

  it('同名普通素材 A/B 有不同图缓存；A→B→A 的计算图迟到响应不能采纳或填进新代缓存', async () => {
    const show = () => {
      const doc = useDocumentStore.getState().doc
      return act(async () => root.render(<CanvasThumb page={doc.page} objects={doc.objects}
        pxWidth={600} editedPanel={object => object as PanelObject} />))
    }
    await act(async () => { await useDocumentStore.getState().switchDocument(project(false), `ordinary-${++serial}`) })
    await show()
    const a = host.querySelector('[data-thumb-panel]')?.getAttribute('href')
    pinDocumentOwner()
    setCurrentProjectId('B')
    await act(async () => { await useDocumentStore.getState().switchDocument(project(false), `ordinary-${++serial}`) })
    await show()
    const b = host.querySelector('[data-thumb-panel]')?.getAttribute('href')
    expect(a).toContain('blob:preview-')
    expect(b).toContain('blob:preview-')
    expect(b).not.toBe(a)
    expect(requests.some(r => r.pj === 'B' && r.header === 'B')).toBe(true)

    setCurrentProjectId('A')
    await act(async () => { await useDocumentStore.getState().switchDocument(project(true), `aba-${++serial}`) })
    let finish!: (response: Response) => void
    previewResponse = () => new Promise(resolve => { finish = resolve })
    await show()
    pinDocumentOwner()
    setCurrentProjectId('B')
    setCurrentProjectId('A')
    previewResponse = undefined
    await act(async () => { await useDocumentStore.getState().switchDocument(project(true), `aba-${++serial}`) })
    await show()
    expect(host.querySelector('[data-thumb-panel]')?.getAttribute('href')).toBe(`data:image/png;base64,${btoa('A')}`)
    await act(async () => finish(Response.json({ png: btoa('stale-A'), chart_revision: 'stale-A',
      manifest: { stem: 'same', size_mm: [100, 80], elements: [] } })))
    expect(host.querySelector('[data-thumb-panel]')?.getAttribute('href')).toBe(`data:image/png;base64,${btoa('A')}`)
    await act(async () => root.render(null))
    await show()
    expect(host.querySelector('[data-thumb-panel]')?.getAttribute('href')).toBe(`data:image/png;base64,${btoa('A')}`)
  })

  it('原 runtime 按状态退路仍可预览，而且与旧正文 owner 同行', async () => {
    const pd = project(false)
    const panel = pd.canvases[0].objects[0] as PanelObject
    panel.fileId = 'runtime:source.py#same'
    panel.fileKind = 'runtime'
    await act(async () => { await useDocumentStore.getState().switchDocument(pd, `runtime-${++serial}`) })
    const doc = useDocumentStore.getState().doc
    pinDocumentOwner()
    setCurrentProjectId('B')
    previewResponse = async () => Response.json({ error: 'unavailable' }, { status: 422 })
    await act(async () => root.render(<CanvasThumb page={doc.page} objects={doc.objects}
      pxWidth={400} editedPanel={object => object as PanelObject} />))
    expect(runtimeRequests).toEqual([{ pj: 'A', header: 'A', body: { id: panel.fileId,
      patches: [...panel.overrides, { gid: 'figure', prop: 'frame', value: 'figsize' }] } }])
    expect(host.querySelector('[data-thumb-panel]')?.getAttribute('href')).toContain('blob:preview-')
  })
})
