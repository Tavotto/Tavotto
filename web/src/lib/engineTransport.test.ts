import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { setEngineTransport, type EngineTransport } from './engineTransport'
import type { Manifest } from './api'
import { exactPanelManifest, renderKeyOf, useRenderStore } from '@/store/renderStore'
import type { PanelChartState, PanelObject } from '@/types/document'
import chartVectors from '../../../tests/golden/chart_model_vectors.json'

const manifest: Manifest = { stem: 'chart', size_mm: [80, 60], elements: [] }
const panel = (): PanelObject => {
  const v = chartVectors.vectors.find(v => v.expect === 'ok')!
  const chart = structuredClone({ model: v.model, binding: v.binding }) as unknown as PanelChartState
  return { id: 'p', type: 'panel', fileId: 'chart.svg', fileKind: 'svg', x: 0, y: 0, w: 80, h: 60,
    nativeW: 80, nativeH: 60, overrides: [], chart }
}
const transport = (supportsChartRender = false): EngineTransport => ({
  ...(supportsChartRender ? { supportsChartRender: true as const } : {}),
  render: vi.fn(async () => ({ rev: 1, manifest, svg: '<svg/>', chart_revision: 'transport-paired' })),
  previewPngUrl: vi.fn(async () => 'data:image/png;base64,eA=='),
  panelSrc: () => null,
})
const send = (p: PanelObject) => useRenderStore.getState().render(p.fileId, p.overrides,
  undefined, undefined, undefined, undefined, 'foreground', p.chart)
beforeEach(() => useRenderStore.getState().clear())
afterEach(() => setEngineTransport(null))

describe('显式chart传输能力', () => {
  it('旧chart宿主仍须明确支持INPUT附件，否则不得忽略ref发native请求', async () => {
    const p = panel(), host = transport(true)
    p.chartSource = { schema: 1, sourceId: p.fileId, bytesSha256: 'a'.repeat(64), sizeBytes: 100,
      sceneDigest: p.chart!.model.scene_digest }
    setEngineTransport(host)
    await useRenderStore.getState().render(p.fileId, p.overrides,
      undefined, undefined, undefined, undefined, 'foreground', p.chart, p.chartSource)
    expect(host.render).not.toHaveBeenCalled()
    expect(useRenderStore.getState().get(renderKeyOf(p)).code).toBe('chart_render_unsupported')
    host.supportsChartSourceRender = true
    await useRenderStore.getState().render(p.fileId, p.overrides,
      undefined, undefined, undefined, undefined, 'foreground', p.chart, p.chartSource)
    expect(host.render).toHaveBeenCalledWith(p.fileId, p.overrides,
      expect.objectContaining({ chart_source: p.chartSource }))
    expect(exactPanelManifest(useRenderStore.getState(), p)).toBe(manifest)
  })
  it('旧宿主不能忽略chart后把native Figure响应冒充成功', async () => {
    const p = panel(), host = transport()
    setEngineTransport(host)
    await send(p)
    expect(host.render).not.toHaveBeenCalled()
    expect(useRenderStore.getState().get(renderKeyOf(p))).toMatchObject({ status: 'error', code: 'chart_render_unsupported' })
    expect(exactPanelManifest(useRenderStore.getState(), p)).toBeNull()
  })

  it('明确声明支持的宿主收到完整chart与patches，响应仍经同一个配对状态机', async () => {
    const p = panel(), host = transport(true)
    p.overrides = [{ gid: 'axes_0', prop: 'xlim', value: [0, 20] }]
    setEngineTransport(host)
    await send(p)
    expect(host.render).toHaveBeenCalledWith(p.fileId, p.overrides, expect.objectContaining({ chart: p.chart }))
    expect(exactPanelManifest(useRenderStore.getState(), p)).toBe(manifest)
  })

  it('普通Python请求不需要chart声明且不增加chart选项', async () => {
    const host = transport()
    setEngineTransport(host)
    await useRenderStore.getState().render('plot.pdf', [])
    expect(host.render).toHaveBeenCalledOnce()
    expect(vi.mocked(host.render).mock.calls[0][2]).not.toHaveProperty('chart')
  })
})
