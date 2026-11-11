import { afterEach, describe, expect, it, vi } from 'vitest'
import { chartPreviewArgs, chartRequestIdentity, chartSourceFields, clearChangedChartSources } from './chartSource'
import { enginePreviewPngSnapshot, engineRender } from './api'
import { chartBind, chartCompute, chartInspect, type ChartBindRequest } from './chartApi'
import { emptyDocument, type ChartSourceRef, type PanelChartState, type PanelObject } from '@/types/document'
import chartVectors from '../../../tests/golden/chart_model_vectors.json'

const chart = (): PanelChartState => {
  const v = chartVectors.vectors.find(v => v.expect === 'ok')!
  return structuredClone({ model: v.model, binding: v.binding }) as unknown as PanelChartState
}
const panel = (): PanelObject => {
  const state = chart()
  return { id: 'p', type: 'panel', fileId: 'original.svg', fileKind: 'svg', nativeW: 100, nativeH: 80,
    x: 0, y: 0, w: 100, h: 80, overrides: [], chart: state,
    chartSource: { schema: 1, sourceId: 'original.svg', bytesSha256: 'a'.repeat(64), sizeBytes: 100,
      sceneDigest: state.model.scene_digest } }
}
afterEach(() => vi.unstubAllGlobals())

describe('chart 请求身份携带坐标合同（N5 第 2 步）', () => {
  it('/1 形状逐字不变；/2 才出现 render_profile，且同 chart 不同 profile 是不同身份', () => {
    const c = chart()
    expect(chartRequestIdentity(c)).toBe(JSON.stringify(c))
    const one = chartRequestIdentity(c, undefined, 'native-manifest/1')
    const two = chartRequestIdentity(c, undefined, 'native-manifest/2')
    expect(JSON.parse(one!)).toEqual({ chart: c, render_profile: 'native-manifest/1' })
    expect(JSON.parse(two!)).toEqual({ chart: c, render_profile: 'native-manifest/2' })
    expect(one).not.toBe(two)
    const ref = panel().chartSource!
    expect(JSON.parse(chartRequestIdentity(c, ref, 'native-manifest/2')!))
      .toEqual({ chart: c, chart_source: ref, render_profile: 'native-manifest/2' })
    // 无 chart 时不因 profile 出身份（profile 只随计算图表有意义）
    expect(chartRequestIdentity(undefined, undefined, 'native-manifest/2')).toBeUndefined()
  })
})

describe('来源引用只是独立INPUT合同，不成为第二份图表模型', () => {
  it('深拷贝完整引用；无引用省略字段，显式坏引用不静默当成legacy', () => {
    const p = panel(), result = chartSourceFields(p.chartSource)
    expect(result.chart_source).toEqual(p.chartSource)
    expect(result.chart_source).not.toBe(p.chartSource)
    p.chartSource!.bytesSha256 = 'b'.repeat(64)
    expect(result.chart_source!.bytesSha256).toBe('a'.repeat(64))
    expect(chartSourceFields(undefined)).toEqual({})
    expect(chartSourceFields(null as unknown as ChartSourceRef)).toEqual({ chart_source: null })
    expect(chartRequestIdentity(p.chart)).toBe(JSON.stringify(p.chart))
    expect(chartRequestIdentity(p.chart, result.chart_source)).not.toBe(chartRequestIdentity(p.chart, p.chartSource))
    expect(chartPreviewArgs()).toEqual([])
    expect(chartPreviewArgs({ chart: p.chart })).toEqual([p.chart])
    expect(chartPreviewArgs(p)).toEqual([p.chart, p.chartSource])
  })

  it.each([
    { name: '数据', change: (p: PanelObject) => { p.chart!.model.dataset.columns.extra = [3] } },
    { name: '轴范围', change: (p: PanelObject) => { p.chart!.model.axes[0].xlim[1] += 5 } },
    { name: '完整edits', change: (p: PanelObject) => { p.overrides = [{ gid: 's', prop: 'color', value: '#f00' }] } },
    { name: '页面位置', change: (p: PanelObject) => { p.x += 5 } },
  ])('$name变化保留来源附件', ({ change }) => {
    const before = emptyDocument(); before.objects = [panel()]
    const next = structuredClone(before); change(next.objects[0] as PanelObject)
    clearChangedChartSources(next, before)
    expect((next.objects[0] as PanelObject).chartSource).toEqual((before.objects[0] as PanelObject).chartSource)
  })

  it.each([
    { name: '文件', change: (p: PanelObject) => { p.fileId = 'other.svg' } },
    { name: '模型scene', change: (p: PanelObject) => { p.chart!.model.scene_digest = 'b'.repeat(64) } },
    { name: '绑定scene', change: (p: PanelObject) => { p.chart!.binding.scene_digest = 'b'.repeat(64) } },
    { name: '删除chart', change: (p: PanelObject) => { delete p.chart } },
  ])('$name真实变化撤销旧引用', ({ change }) => {
    const before = emptyDocument(); before.objects = [panel()]
    const next = structuredClone(before); change(next.objects[0] as PanelObject)
    clearChangedChartSources(next, before)
    expect(next.objects[0]).not.toHaveProperty('chartSource')
  })

  it('旧坏引用在无关提交仍在场；从存储恢复的新引用不被清掉', () => {
    const before = emptyDocument(); before.objects = [{ ...panel(), chartSource: null } as unknown as PanelObject]
    const next = structuredClone(before); next.objects[0].x += 1
    clearChangedChartSources(next, before)
    expect(next.objects[0]).toHaveProperty('chartSource', null)
    const restored = structuredClone(before), p = panel()
    p.fileId = 'other.svg'; p.chartSource!.sourceId = p.fileId
    restored.objects = [p]
    clearChangedChartSources(restored, before)
    expect(restored.objects[0]).toHaveProperty('chartSource', p.chartSource)
  })
})

it('render/PNG/inspect/bind/compute均携完整chart_source，普通URL与payload不增加字段', async () => {
  const p = panel()
  const fetcher = vi.fn<typeof fetch>(async () => new Response(JSON.stringify({
    rev: 1, chart_revision: 'paired', manifest: { stem: 'chart', size_mm: [100, 80], elements: [] }, png: 'eA==', svg: '<svg/>',
  }), { status: 200 }))
  vi.stubGlobal('fetch', fetcher)
  await engineRender(p.fileId, p.overrides, { chart: p.chart, ...chartSourceFields(p.chartSource) })
  await enginePreviewPngSnapshot(p.fileId, p.overrides, 400, undefined, { chart: p.chart, ...chartSourceFields(p.chartSource) })
  await chartInspect(p.fileId, p.chartSource)
  await chartBind({ file: p.fileId, ...chartSourceFields(p.chartSource), axes_id: 'a', rect: [0, 0, 100, 80],
    x_kind: 'categorical', categories_col: 'category', series: [] } as ChartBindRequest)
  await chartCompute(p.fileId, p.chart!, { render_profile: 'native-manifest/1', overrides: p.overrides,
    ...chartSourceFields(p.chartSource) })
  for (const index of [0, 1, 3, 4]) {
    const init = fetcher.mock.calls[index][1] as RequestInit
    expect(JSON.parse(init.body as string).chart_source).toEqual(p.chartSource)
  }
  const url = new URL(String(fetcher.mock.calls[2][0]), 'http://localhost')
  expect(JSON.parse(url.searchParams.get('chart_source')!)).toEqual(p.chartSource)
  await chartInspect(p.fileId)
  expect(String(fetcher.mock.calls[5][0])).not.toContain('chart_source')
  await chartCompute(p.fileId, p.chart!)
  expect(JSON.parse((fetcher.mock.calls[6][1] as RequestInit).body as string)).toEqual({ file: p.fileId, ...p.chart })
})
