import { afterEach, expect, it, vi } from 'vitest'

import { chartBind, chartCompute, chartInspect, type ChartBindRequest } from './chartApi'
import type { PanelChartState, PanelOverride } from '@/types/document'
import { setCurrentProjectId } from './session'

afterEach(() => { vi.unstubAllGlobals(); setCurrentProjectId(null) })

it('serializes categorical binding without a numeric x field and preserves project ownership', async () => {
  setCurrentProjectId('category-project')
  const fetcher = vi.fn().mockResolvedValue(new Response('{}', { status: 200 }))
  vi.stubGlobal('fetch', fetcher)
  const request: ChartBindRequest = {
    file: 'category.svg', axes_id: 'ax1', rect: [0, 0, 100, 100],
    x_kind: 'categorical', categories_col: 'category',
    y: { p1_user: 0, p1_value: 0, p2_user: 1, p2_value: 10, scale: 'linear' },
    series: [{ node: 'bars', x_col: 'category', y_col: 'value', kind: 'bar' }],
    dataset_text: 'category\tvalue\nA\t1\nB\t2', completeness: 'self-contained',
  }
  await chartBind(request)
  const [url, init] = fetcher.mock.calls[0] as [string, RequestInit]
  expect(url).toContain('/api/svg/chart/bind')
  expect(new Headers(init.headers).get('X-Tavotto-Project')).toBe('category-project')
  const sent = JSON.parse(init.body as string)
  expect(sent).toEqual(request)
  expect(sent).not.toHaveProperty('x')
})

it('returns restored chart data from inspect without transforming or adopting it', async () => {
  const payload = { scene_digest: 'original-scene', w_pt: 100, h_pt: 100, view_box: [0, 0, 100, 100],
    nodes: [{ id: 'original-node', tag: 'path' }], truncated: false,
    restored_chart: { model: { rev: 2 }, binding: { model_rev: 2 } } }
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify(payload), { status: 200 })))
  expect(await chartInspect('computed.svg')).toEqual(payload)
})

it('leaves the existing compute payload unchanged when no render options are supplied', async () => {
  const chart = { model: { rev: 2 }, binding: { model_rev: 2 } } as PanelChartState
  const fetcher = vi.fn().mockResolvedValue(new Response('{}', { status: 200 }))
  vi.stubGlobal('fetch', fetcher)
  await chartCompute('legacy.svg', chart)
  expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual({ file: 'legacy.svg', ...chart })
})

it('sends v2 render profile and complete original ordered edits alongside the chart', async () => {
  const chart = { model: { rev: 2 }, binding: { model_rev: 2 } } as PanelChartState
  const overrides: PanelOverride[] = [
    { gid: 'axes_0.lines_0', prop: 'color', value: '#f00' },
    { gid: 'axes_0', prop: 'xlim', value: [1, 10] },
    { gid: 'axes_0.lines_0', prop: 'color', value: '#00f' },
  ]
  const fetcher = vi.fn().mockResolvedValue(new Response('{}', { status: 200 }))
  vi.stubGlobal('fetch', fetcher)
  await chartCompute('v2.svg', chart, { render_profile: 'native-manifest/1', overrides })
  expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual({
    file: 'v2.svg', ...chart, render_profile: 'native-manifest/1', overrides,
  })
  expect(overrides.map(o => o.value)).toEqual(['#f00', [1, 10], '#00f'])
})

it('passes through only actual inspect restored_render fields without inventing v2 for v1', async () => {
  const payload = { scene_digest: 'original-scene', w_pt: 100, h_pt: 100, view_box: [0, 0, 100, 100],
    nodes: [], truncated: false, restored_chart: { model: { rev: 2 }, binding: { model_rev: 2 } },
    restored_render: { render_profile: 'native-manifest/1', overrides: [{ gid: 'axes_0', prop: 'xlim', value: [1, 10] }] } }
  const fetcher = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify(payload), { status: 200 }))
    .mockResolvedValueOnce(new Response(JSON.stringify({ ...payload, restored_render: undefined }), { status: 200 }))
  vi.stubGlobal('fetch', fetcher)
  expect(await chartInspect('v2.svg')).toEqual(payload)
  expect(await chartInspect('v1.svg')).not.toHaveProperty('restored_render')
})

it.each([null, { model: { rev: 1 }, binding: { model_rev: 1 } }])('preserves explicit prior-chart ownership evidence including %j', async previous => {
  const chart = { model: { rev: 2 }, binding: { model_rev: 2 } } as PanelChartState
  const previous_chart = previous as PanelChartState | null
  const fetcher = vi.fn().mockResolvedValue(new Response('{}', { status: 200 }))
  vi.stubGlobal('fetch', fetcher)
  await chartCompute('rebound.svg', chart, { render_profile: 'native-manifest/1', overrides: [], previous_chart })
  expect(JSON.parse(fetcher.mock.calls[0][1].body)).toMatchObject({ previous_chart })
})

it('serializes the legend contract verbatim and omits the key when absent (N4)', async () => {
  const fetcher = vi.fn().mockImplementation(async () => new Response('{}', { status: 200 }))
  vi.stubGlobal('fetch', fetcher)
  const withoutLegend: ChartBindRequest = {
    file: 'plain.svg', axes_id: 'ax1', rect: [0, 0, 100, 100],
    x: { p1_user: 0, p1_value: 0, p2_user: 1, p2_value: 10, scale: 'linear' },
    y: { p1_user: 0, p1_value: 0, p2_user: 1, p2_value: 10, scale: 'linear' },
    series: [{ node: 'curve', x_col: 'x', y_col: 'y' }],
    dataset_text: 'x,y\n1,2', completeness: 'self-contained',
  }
  await chartBind(withoutLegend)
  expect(JSON.parse(fetcher.mock.calls[0][1].body as string)).not.toHaveProperty('legend')
  const legend = [{
    id: 'leg1', node: 'legendbox', rect: [10, 10, 40, 20] as [number, number, number, number],
    entries: [{ index: 0, label: 'one', series_node: 'curve' }, { index: 1, label: 'note' }],
  }]
  await chartBind({ ...withoutLegend, file: 'legend.svg', legend })
  const sent = JSON.parse(fetcher.mock.calls[1][1].body as string)
  expect(sent.legend).toEqual(legend)
})
