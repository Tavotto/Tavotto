import { afterEach, expect, it, vi } from 'vitest'

import { chartBind, chartInspect, type ChartBindRequest } from './chartApi'
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
