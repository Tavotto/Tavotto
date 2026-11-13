import { afterEach, describe, expect, it, vi } from 'vitest'
import { enginePreviewPng, enginePreviewPngSnapshot, engineRender } from './api'
import { setCurrentProjectId } from './session'
import chartVectors from '../../../tests/golden/chart_model_vectors.json'
import type { PanelChartState } from '@/types/document'

const chart = () => {
  const v = chartVectors.vectors.find(v => v.expect === 'ok')!
  return structuredClone({ model: v.model, binding: v.binding }) as unknown as PanelChartState
}
const manifest = { stem: 'chart', size_mm: [80, 60], elements: [] }

afterEach(() => { vi.unstubAllGlobals(); setCurrentProjectId(null) })

describe('计算图表走标准 engine HTTP 合同', () => {
  it('普通请求的 JSON 逐字段保持原样', async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response('{}'))
    vi.stubGlobal('fetch', fetcher)
    await engineRender('plot.pdf', [])
    expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual({ id: 'plot.pdf', patches: [], inline_svg: true })
  })

  it('chart 与完整 patches 同次提交，保留项目头及配对渲染身份', async () => {
    setCurrentProjectId('chart-project')
    const response = { rev: 2, chart_revision: 'complete-render', svg: '<svg/>', manifest }
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify(response)))
    vi.stubGlobal('fetch', fetcher)
    const state = chart()
    const patches = [{ gid: 'axes_0', prop: 'xlim', value: [0, 20] }]
    expect(await engineRender('chart.svg', patches, { chart: state })).toEqual(response)
    const [url, init] = fetcher.mock.calls[0] as [string, RequestInit]
    expect(url).toContain('/api/engine/render')
    expect(new Headers(init.headers).get('X-Tavotto-Project')).toBe('chart-project')
    expect(JSON.parse(init.body as string)).toEqual({ id: 'chart.svg', patches, chart: state, inline_svg: true })
  })

  it('PNG 第五参数保留来源合同并显式传 chart，读同响应的 manifest/revision', async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ png: 'eA==', manifest, chart_revision: 'png-draw' })))
    vi.stubGlobal('fetch', fetcher)
    const state = chart()
    const patches = [{ gid: 'axes_0.lines_0', prop: 'color', value: '#f00' }]
    const result = await enginePreviewPngSnapshot('chart.svg', patches, 1000, undefined, { chart: state })
    expect(result).toMatchObject({ url: 'data:image/png;base64,eA==', manifest, chartRevision: 'png-draw' })
    expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual({ id: 'chart.svg', patches, w: 1000, chart: state, with_manifest: true })
  })

  it('chart PNG 缺少配对身份时拒绝，而普通旧端点仍兼容', async () => {
    vi.stubGlobal('fetch', vi.fn().mockImplementation(() => Promise.resolve(new Response(JSON.stringify({ png: 'eA==', manifest })))))
    await expect(enginePreviewPngSnapshot('chart.svg', [], 512, undefined, { chart: chart() })).rejects.toMatchObject({ code: 'chart_render_pair_missing' })
    await expect(enginePreviewPngSnapshot('plot.pdf', [], 512)).resolves.toMatchObject({ manifest })
  })

  it('chart 的 Blob 显示接口同样经配对 PNG 请求，普通 Blob 保持原始载荷', async () => {
    const fetcher = vi.fn().mockImplementation(() => Promise.resolve(new Response(JSON.stringify({ png: 'eA==', manifest, chart_revision: 'paired' }))))
    vi.stubGlobal('fetch', fetcher)
    const blob = await enginePreviewPng('chart.svg', [], 512, undefined, { chart: chart() })
    expect(blob.type).toBe('image/png')
    expect(blob.size).toBe(1)
    expect(JSON.parse(fetcher.mock.calls[0][1].body).with_manifest).toBe(true)
    fetcher.mockResolvedValueOnce(new Response('original-png'))
    await enginePreviewPng('plot.pdf', [], 512)
    expect(JSON.parse(fetcher.mock.calls[1][1].body)).toEqual({ id: 'plot.pdf', patches: [], w: 512 })
  })
})
