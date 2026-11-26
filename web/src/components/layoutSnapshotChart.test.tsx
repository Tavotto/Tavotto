import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { LayoutSnapshot } from './VersionDialog'
import { clearVariantPngCache } from '@/hooks/useVariantPng'
import type { FigureDocument, PanelChartState, PanelObject } from '@/types/document'
import vectors from '../../../tests/golden/chart_model_vectors.json'

globalThis.IS_REACT_ACT_ENVIRONMENT = true
let host: HTMLDivElement
let root: Root
let requests: Record<string, unknown>[]
const approximate = vi.fn()
const model = structuredClone(vectors.vectors[0])
const panel: PanelObject = {
  id: 'chart', type: 'panel', fileId: 'saved.svg', fileKind: 'svg',
  x: 0, y: 0, w: 100, h: 100, nativeW: 100, nativeH: 100, overrides: [],
  chart: { model: model.model, binding: model.binding } as unknown as PanelChartState,
  chartSource: { schema: 1, sourceId: 'saved.svg', bytesSha256: 'a'.repeat(64), sizeBytes: 10, sceneDigest: model.model.scene_digest },
}
const doc = (p: PanelObject): FigureDocument => ({ schema: 2, name: 'saved', page: { w: 100, h: 100 }, objects: [p], guides: [] })

beforeEach(() => {
  clearVariantPngCache()
  approximate.mockClear()
  requests = []
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
  vi.stubGlobal('fetch', vi.fn(async (_url: string, init?: RequestInit) => {
    requests.push(JSON.parse(init!.body as string))
    return new Response(JSON.stringify({ png: btoa(`saved-${requests.length}`), manifest: {}, chart_revision: 'verified' }), { status: 200 })
  }))
})
afterEach(() => { act(() => root.unmount()); host.remove(); vi.unstubAllGlobals(); clearVariantPngCache() })

it('没有 overrides 的图表仍按节点模型和附件请求预览，模型/附件/profile 分别换缓存身份', async () => {
  const show = async (p: PanelObject) => act(async () => root.render(<LayoutSnapshot doc={doc(p)} renderOverrides onApproximate={approximate} />))
  await show(panel)
  expect(requests).toHaveLength(1)
  expect(requests[0]).toMatchObject({ chart: panel.chart, chart_source: panel.chartSource, render_profile: 'native-manifest/1' })
  expect(host.querySelector('img')!.src).toContain(btoa('saved-1'))
  expect(approximate).toHaveBeenLastCalledWith(false)
  const changed = structuredClone(panel)
  changed.chart!.model.rev += 1
  await show(changed)
  expect(requests).toHaveLength(2)
  changed.chartSource!.bytesSha256 = 'b'.repeat(64)
  await show(structuredClone(changed))
  expect(requests).toHaveLength(3)
  changed.chartFrame = { version: 1, renderProfile: 'native-manifest/2', originPt: [-1, -1] }
  await show(structuredClone(changed))
  expect(requests).toHaveLength(4)
  expect(requests[3].render_profile).toBe('native-manifest/2')
})

it('检查点图表渲染失败时明确报告近似预览', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ code: 'chart_source_mismatch' }), { status: 422 })))
  await act(async () => root.render(<LayoutSnapshot doc={doc(panel)} renderOverrides onApproximate={approximate} />))
  expect(approximate).toHaveBeenLastCalledWith(true)
})

it('同页普通面板不会掩盖图表的近似标记', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response('{}', { status: 422 })))
  const snapshot = doc(panel)
  snapshot.objects.push({ ...panel, id: 'ordinary', chart: undefined, chartSource: undefined })
  await act(async () => root.render(<LayoutSnapshot doc={snapshot} renderOverrides onApproximate={approximate} />))
  expect(approximate).toHaveBeenLastCalledWith(true)
})
