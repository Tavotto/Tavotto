import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { EngineError, enginePreviewPng, enginePreviewPngSnapshot, fetchFigureSnapshotPreview } from './api'
import { setCurrentProjectId } from './session'
import type { FigureSnapshot } from '@/types/document'

const ref = (): FigureSnapshot => ({ version: 1, sourceId: 'History.pdf', patchHash: 'patch', receiptId: 'receipt', snapshotId: 'snapshot',
  executionOwner: { script: 'plot.py', run_config: 'config-A' } })
const patches = [{ gid: 'line', prop: 'linewidth', value: 9 }, { gid: 'line', prop: 'linewidth', value: 3 }]
const requests: { url: URL; init: RequestInit; body: Record<string, unknown> }[] = []
let response: () => Promise<Response>
const png = (reference: unknown = ref(), bytes = 'exact-archive') => new Response(bytes, { headers: {
  'Content-Type': 'image/png', 'X-Tavotto-Figure-Snapshot': JSON.stringify(reference),
} })
const svg = () => ({ preview_source: 'figure_snapshot', figure_snapshot: ref(), svg: '<svg xmlns="http://www.w3.org/2000/svg"/>', size_mm: [80, 60] })

beforeEach(() => {
  requests.length = 0; setCurrentProjectId('current-B'); response = async () => png()
  vi.stubGlobal('fetch', vi.fn(async (url: string, init: RequestInit) => {
    requests.push({ url: new URL(url, 'http://localhost'), init, body: JSON.parse(String(init.body)) })
    return response()
  }))
})
afterEach(() => { setCurrentProjectId(null); vi.unstubAllGlobals() })

describe('归档PNG原端点的完整引用回执', () => {
  it.each(['owner-A', null])('显式pj=%s同行，原序patch和完整ref不降到当前项目', async pj => {
    const value = await enginePreviewPng('History.pdf', patches, 360, undefined, { figure_snapshot: ref() }, pj)
    expect(await value.text()).toBe('exact-archive')
    expect(requests).toHaveLength(1)
    expect(requests[0].url.pathname).toBe('/api/engine/preview_png')
    expect(requests[0].url.searchParams.get('pj')).toBe(pj)
    expect(new Headers(requests[0].init.headers).get('X-Tavotto-Project')).toBe(pj)
    expect(requests[0].body).toEqual({ id: 'History.pdf', patches, w: 360, figure_snapshot: ref() })
  })

  it('服务器JSON字段顺序改变仍认同一完整ref', async () => {
    response = async () => png({ executionOwner: { run_config: 'config-A', script: 'plot.py' },
      snapshotId: 'snapshot', receiptId: 'receipt', patchHash: 'patch', sourceId: 'History.pdf', version: 1 })
    expect(await (await enginePreviewPng('History.pdf', [], 200, undefined, { figure_snapshot: ref() })).text()).toBe('exact-archive')
  })

  it.each([
    ['snapshotId', { snapshotId: 'other-snapshot' }], ['receiptId', { receiptId: 'other-receipt' }],
    ['patchHash', { patchHash: 'other-patch' }], ['sourceId', { sourceId: 'other.pdf' }],
    ['owner', { executionOwner: { script: 'plot.py', run_config: null } }], ['unknown member', { unexpected: true }],
  ])('拒绝回显不同%s的图片，不消费同名当前图', async (_label, delta) => {
    response = async () => png({ ...ref(), ...delta })
    await expect(enginePreviewPng('History.pdf', patches, 200, undefined, { figure_snapshot: ref() }))
      .rejects.toMatchObject({ code: 'figure_snapshot_preview_invalid' })
    expect(requests).toHaveLength(1)
  })

  it.each(['missing-header', 'bad-json', 'wrong-content', 'empty'])('拒绝%s成功响应', async mode => {
    response = async () => mode === 'empty' ? png(ref(), '') : new Response('unproved', { headers: {
      'Content-Type': mode === 'wrong-content' ? 'text/html' : 'image/png',
      ...(mode === 'missing-header' ? {} : { 'X-Tavotto-Figure-Snapshot': mode === 'bad-json' ? '{' : JSON.stringify(ref()) }),
    } })
    await expect(enginePreviewPng('History.pdf', [], 200, undefined, { figure_snapshot: ref() })).rejects.toBeInstanceOf(EngineError)
  })

  it('在途调用者换ref不改变已发请求的回显核对目标', async () => {
    const selected = ref()
    let finish!: (value: Response) => void
    response = () => new Promise(resolve => { finish = resolve })
    const pending = enginePreviewPng('History.pdf', patches, 200, undefined, { figure_snapshot: selected }, 'A')
    selected.snapshotId = 'mutated-later'
    finish(png())
    expect(await (await pending).text()).toBe('exact-archive')
    expect(requests[0].body.figure_snapshot).toEqual(ref())
  })

  it.each([null, { ...ref(), sourceId: 'other.pdf' }])('显式坏ref %j不发普通worker请求', async value => {
    await expect(enginePreviewPng('History.pdf', [], 200, undefined, { figure_snapshot: value as never })).rejects.toBeInstanceOf(EngineError)
    expect(requests).toHaveLength(0)
  })

  it('归档PNG不给可编辑manifest，原paired PNG API不能把它收为权威', async () => {
    await expect(enginePreviewPngSnapshot('History.pdf', [], 200, undefined, { figure_snapshot: ref() })).rejects.toBeInstanceOf(EngineError)
    expect(requests).toHaveLength(0)
  })

  it('无ref原调用保持旧wire和旧响应要求，不强加归档header', async () => {
    response = async () => new Response('legacy-worker-png')
    expect(await (await enginePreviewPng('Current.pdf', patches, 200)).text()).toBe('legacy-worker-png')
    expect(requests[0].body).toEqual({ id: 'Current.pdf', patches, w: 200 })
    expect(requests[0].url.searchParams.get('pj')).toBe('current-B')
  })
})

describe('归档SVG显示响应不冒充编辑manifest', () => {
  it.each(['owner-A', null])('SVG同ref同pj=%s，并返回已核尺寸', async pj => {
    const value = svg(); response = async () => Response.json(value)
    expect(await fetchFigureSnapshotPreview('History.pdf', patches, ref(), undefined, pj)).toEqual(value)
    expect(requests[0].url.pathname).toBe('/api/documents/v4/figure-preview')
    expect(requests[0].url.searchParams.get('pj')).toBe(pj)
    expect(new Headers(requests[0].init.headers).get('X-Tavotto-Project')).toBe(pj)
    expect(requests[0].body).toEqual({ id: 'History.pdf', patches, figure_snapshot: ref() })
  })

  it.each([
    null, {}, { preview_source: 'current' }, { svg: '' }, { svg: '  ' }, { svg: null },
    { size_mm: [0, 60] }, { size_mm: [80, null] }, { size_mm: [80, 60, 70] }, { size_mm: ['80', 60] },
    { figure_snapshot: { ...ref(), receiptId: 'other' } }, { figure_snapshot: null },
  ])('拒绝缺乏真实配对/尺寸的SVG响应 %j', async delta => {
    response = async () => Response.json(delta === null ? null : Object.keys(delta).length ? { ...svg(), ...delta } : {})
    await expect(fetchFigureSnapshotPreview('History.pdf', [], ref())).rejects.toMatchObject({ code: 'figure_snapshot_preview_invalid' })
  })

  it.each(['png', 'svg'])('后端422在%s保持EngineError/code，不改成普通素材重试', async kind => {
    response = async () => Response.json({ code: 'figure_snapshot_unavailable', error: 'archive unavailable', params: { id: 'History.pdf' } }, { status: 422 })
    const result = kind === 'png' ? enginePreviewPng('History.pdf', [], 200, undefined, { figure_snapshot: ref() })
      : fetchFigureSnapshotPreview('History.pdf', [], ref())
    await expect(result).rejects.toMatchObject({ code: 'figure_snapshot_unavailable' })
    expect(requests).toHaveLength(1)
  })
})
