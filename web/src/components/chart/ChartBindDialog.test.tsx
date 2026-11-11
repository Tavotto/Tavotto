import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ChartBindDialog } from './ChartBindDialog'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { chartBind, chartCompute, chartInspect } from '@/lib/chartApi'
import { literal } from '@/i18n'
import { useChartBindStore } from '@/store/chartBindStore'
import { useDocumentStore } from '@/store/documentStore'
import { emptyProject, type PanelChartState, type PanelObject, type PanelOverride } from '@/types/document'

vi.mock('@/lib/chartApi', () => ({ chartBind: vi.fn(), chartCompute: vi.fn(), chartInspect: vi.fn() }))
globalThis.IS_REACT_ACT_ENVIRONMENT = true
let host: HTMLDivElement, root: Root
const panel: PanelObject = { id: 'chart-panel', type: 'panel', fileId: 'chart.svg', fileKind: 'svg', nativeW: 100, nativeH: 100, x: 0, y: 0, w: 100, h: 100, overrides: [] }
const chart = {
  model: { schema: 'tavotto.chart-model/1', rev: 1, axes: [], series: [], dataset: { columns: {} }, scene_digest: 'a'.repeat(64), data_digest: 'b'.repeat(64), engine: { matplotlib: '3', numpy: '2', tavotto: '0' } },
  binding: { model_rev: 1, scene_digest: 'a'.repeat(64), entries: [] },
}
const preview = { svg: '<svg xmlns="http://www.w3.org/2000/svg"/>', facts: {}, scene_digest: chart.model.scene_digest, render_revision: 'revision', model_digest: 'model', binding_digest: 'binding', engine: chart.model.engine }
const restoredChart: PanelChartState = {
  model: { ...chart.model, axes: [{ id: 'ax1', rect: [10, 20, 70, 60], xlim: [1, 3], ylim: [2, 8],
    xscale: 'linear', yscale: 'linear', invert_x: false, invert_y: false }],
  series: [{ id: 's1', axes: 'ax1', node: 'curve-a', x: 'x', y: 'y', kind: 'line', style: { color: '#248acf' } }],
  dataset: { columns: { x: [1, 3], y: [2, 8] } } },
  binding: { ...chart.binding, entries: [{ object: 'series:s1', node: 'curve-a' }] },
}
const button = (value: string) => document.querySelector<HTMLButtonElement>(`[data-chart-bind-action="${value}"]`)!
const currentPanel = () => useDocumentStore.getState().doc.objects.find(o => o.id === panel.id) as PanelObject
const fullEdits: PanelOverride[] = [
  { gid: 'axes_0.lines_0', prop: 'color', value: '#ff0000', identity: 'line-owner' },
  { gid: 'axes_0', prop: 'xlim', value: [0, 10] },
  { gid: 'axes_0.lines_0', prop: 'color', value: '#0000ff', identity: 'line-owner' },
]
const recoveredScene = () => ({ scene_digest: restoredChart.model.scene_digest,
  w_pt: 100, h_pt: 100, view_box: [0, 0, 100, 100], nodes: [{ id: 'curve-a', tag: 'path' }],
  truncated: false, restored_chart: restoredChart,
  restored_render: { render_profile: 'native-manifest/1' as const, overrides: structuredClone(fullEdits) } })

beforeEach(async () => {
  vi.resetAllMocks()
  vi.mocked(chartInspect).mockResolvedValue({ scene_digest: chart.model.scene_digest, w_pt: 100, h_pt: 100, view_box: [0, 0, 100, 100], nodes: [{ id: 'curve-a', tag: 'path' }, { id: 'curve-b', tag: 'path' }], truncated: false })
  vi.mocked(chartBind).mockResolvedValue(chart)
  vi.mocked(chartCompute).mockResolvedValue(preview)
  useChartBindStore.getState().close()
  await useDocumentStore.getState().switchDocument(emptyProject(), 'chart-dialog-test')
  useDocumentStore.getState().commit(literal('fixture'), d => { d.objects = [structuredClone(panel)] })
  host = document.createElement('div'); document.body.appendChild(host); root = createRoot(host)
  await act(async () => { root.render(<TooltipProvider><ChartBindDialog /></TooltipProvider>); useChartBindStore.getState().openFor(panel) })
})
afterEach(() => { act(() => root.unmount()); host.remove() })

it('first-time preview builds the binding and only explicit confirmation writes one undoable edit', async () => {
  const history = useDocumentStore.getState().past.length
  expect(button('preview').disabled).toBe(false)
  await act(async () => document.querySelector<HTMLButtonElement>('[data-chart-series-add]')!.click())
  expect(document.querySelectorAll('[data-chart-series]')).toHaveLength(2)
  await act(async () => useChartBindStore.getState().patchForm({ datasetText: 'x,a,b\n0,1,2', axesNodes: ['axis-labels'], series: [
    { node: 'curve-a', kind: 'line', xCol: 'x', yCol: 'a' },
    { node: 'curve-b', kind: 'errorbar', xCol: 'x', yCol: 'b', yerrCol: 'err' },
  ] }))
  await act(async () => button('preview').click())
  expect(chartBind).toHaveBeenCalledWith(expect.objectContaining({ axes_id: 'ax1', axes_nodes: ['axis-labels'], series: [expect.objectContaining({ node: 'curve-a' }), expect.objectContaining({ node: 'curve-b', kind: 'errorbar', yerr_col: 'err' })] }))
  expect(chartCompute).toHaveBeenCalledTimes(1)
  expect(document.querySelector('[data-chart-comparison]')).not.toBeNull()
  expect(currentPanel().chart).toBeUndefined()
  await act(async () => button('confirm').click())
  expect(currentPanel().chart).toEqual(chart)
  expect(useDocumentStore.getState().past.length).toBe(history + 1)
  await act(async () => { useDocumentStore.getState().undo() })
  expect(currentPanel().chart).toBeUndefined()
  await act(async () => { useDocumentStore.getState().redo() })
  expect(currentPanel().chart).toEqual(chart)
})

it('failed preview and cancelling never change the document or history', async () => {
  const history = useDocumentStore.getState().past.length
  vi.mocked(chartCompute).mockRejectedValue(new Error('source changed'))
  await act(async () => button('preview').click())
  expect(document.querySelector('[data-chart-bind-action="confirm"]')).toBeNull()
  expect(currentPanel().chart).toBeUndefined()
  await act(async () => useChartBindStore.getState().close())
  expect(useDocumentStore.getState().past.length).toBe(history)
})

it('recovered SVG data remains a visible draft until preview and one explicit undoable confirmation', async () => {
  const history = useDocumentStore.getState().past.length
  vi.mocked(chartInspect).mockResolvedValue({ scene_digest: restoredChart.model.scene_digest,
    w_pt: 100, h_pt: 100, view_box: [0, 0, 100, 100], nodes: [{ id: 'curve-a', tag: 'path' }],
    truncated: false, restored_chart: restoredChart })
  vi.mocked(chartBind).mockResolvedValue(restoredChart)
  await act(async () => useChartBindStore.getState().openFor(panel))
  const notice = document.querySelector('[data-chart-restored]')
  expect(notice).not.toBeNull()
  expect(notice?.textContent).toContain('SVG')
  expect(document.querySelector<HTMLTextAreaElement>('[data-chart-data]')?.value).toBe('x\ty\n1\t2\n3\t8')
  expect(currentPanel().chart).toBeUndefined()
  expect(useDocumentStore.getState().past.length).toBe(history)
  expect(chartCompute).not.toHaveBeenCalled()
  expect(document.querySelector('[data-chart-bind-action="confirm"]')).toBeNull()

  await act(async () => button('preview').click())
  expect(currentPanel().chart).toBeUndefined()
  expect(useDocumentStore.getState().past.length).toBe(history)
  await act(async () => button('confirm').click())
  expect(currentPanel().chart).toEqual(restoredChart)
  expect(useDocumentStore.getState().past.length).toBe(history + 1)
  await act(async () => useDocumentStore.getState().undo())
  expect(currentPanel().chart).toBeUndefined()
  await act(async () => useDocumentStore.getState().redo())
  expect(currentPanel().chart).toEqual(restoredChart)
})

it('cancelling a recovered chart leaves its SVG panel unchanged', async () => {
  const history = useDocumentStore.getState().past.length
  vi.mocked(chartInspect).mockResolvedValue({ scene_digest: restoredChart.model.scene_digest,
    w_pt: 100, h_pt: 100, view_box: [0, 0, 100, 100], nodes: [{ id: 'curve-a', tag: 'path' }],
    truncated: false, restored_chart: restoredChart })
  await act(async () => useChartBindStore.getState().openFor(panel))
  expect(document.querySelector('[data-chart-restored]')).not.toBeNull()
  await act(async () => useChartBindStore.getState().close())
  expect(currentPanel()).toEqual(panel)
  expect(useDocumentStore.getState().past.length).toBe(history)
})

it('categorical charts offer category evidence and omit the numeric x request', async () => {
  await act(async () => useChartBindStore.getState().patchForm({ xKind: 'categorical', categoriesCol: 'category',
    datasetText: 'category\ty\nA\t1\nB\t2', series: [{ kind: 'bar', node: 'curve-a', xCol: 'category', yCol: 'y' }] }))
  expect(document.querySelector('[data-chart-axis="x"]')).toBeNull()
  expect(document.querySelector('[data-chart-axis="y"]')).not.toBeNull()
  await act(async () => button('preview').click())
  const request = vi.mocked(chartBind).mock.calls[0]?.[0]
  expect(request).toMatchObject({ x_kind: 'categorical', categories_col: 'category' })
  expect(request).not.toHaveProperty('x')
})

it('v2 preview uses every ordered edit and confirmation restores model plus edits in one undoable transaction', async () => {
  vi.mocked(chartInspect).mockResolvedValue(recoveredScene())
  const before = structuredClone(currentPanel())
  const history = useDocumentStore.getState().past.length
  await act(async () => useChartBindStore.getState().openFor(currentPanel()))
  expect(currentPanel()).toEqual(before)
  await act(async () => button('preview').click())
  expect(chartCompute).toHaveBeenCalledWith('chart.svg', expect.objectContaining(restoredChart), {
    render_profile: 'native-manifest/1', overrides: fullEdits,
  })
  expect(currentPanel()).toEqual(before)
  expect(useDocumentStore.getState().past.length).toBe(history)
  await act(async () => button('confirm').click())
  expect(currentPanel().chart).toEqual(restoredChart)
  expect(currentPanel().overrides).toEqual(fullEdits)
  expect(useDocumentStore.getState().past.length).toBe(history + 1)
  await act(async () => useDocumentStore.getState().undo())
  expect(currentPanel()).toEqual(before)
  await act(async () => useDocumentStore.getState().redo())
  expect(currentPanel().chart).toEqual(restoredChart)
  expect(currentPanel().overrides).toEqual(fullEdits)
})

it('existing panel edits override embedded state and survive confirming the unchanged preview', async () => {
  const currentEdits: PanelOverride[] = [{ gid: 'axes_0.lines_0', prop: 'linewidth', value: 7 }]
  const currentChart = structuredClone(restoredChart)
  currentChart.model.dataset.columns.y = [20, 80]
  await act(async () => useDocumentStore.getState().commit(literal('existing edit'), d => {
    const p = d.objects[0] as PanelObject
    p.chart = currentChart
    p.overrides = currentEdits
  }))
  vi.mocked(chartInspect).mockResolvedValue(recoveredScene())
  await act(async () => useChartBindStore.getState().openFor(currentPanel()))
  await act(async () => button('preview').click())
  expect(chartCompute).toHaveBeenCalledWith('chart.svg', expect.objectContaining(currentChart), {
    render_profile: 'native-manifest/1', overrides: currentEdits,
  })
  await act(async () => button('confirm').click())
  expect(currentPanel().chart).toEqual(currentChart)
  expect(currentPanel().overrides).toEqual(currentEdits)
})

it('cancelling while v2 preview is pending leaves the panel and history unchanged after its late response', async () => {
  let finish!: (value: typeof preview) => void
  vi.mocked(chartInspect).mockResolvedValue(recoveredScene())
  vi.mocked(chartCompute).mockReturnValue(new Promise(resolve => { finish = resolve }))
  const before = structuredClone(currentPanel()), history = useDocumentStore.getState().past.length
  await act(async () => useChartBindStore.getState().openFor(currentPanel()))
  await act(async () => { button('preview').click() })
  await act(async () => useChartBindStore.getState().close())
  await act(async () => finish(preview))
  expect(currentPanel()).toEqual(before)
  expect(useDocumentStore.getState().past.length).toBe(history)
  expect(useChartBindStore.getState().resultRender).toBeNull()
})

it('confirmation cannot overwrite edits changed after the preview started', async () => {
  vi.mocked(chartInspect).mockResolvedValue(recoveredScene())
  await act(async () => useChartBindStore.getState().openFor(currentPanel()))
  await act(async () => button('preview').click())
  await act(async () => useDocumentStore.getState().commit(literal('newer edit'), d => {
    (d.objects[0] as PanelObject).overrides = [{ gid: 'axes_0', prop: 'ylim', value: [0, 500] }]
  }))
  const afterNewEdit = structuredClone(currentPanel()), history = useDocumentStore.getState().past.length
  await act(async () => button('confirm').click())
  expect(currentPanel()).toEqual(afterNewEdit)
  expect(useDocumentStore.getState().past.length).toBe(history)
})

it('a late v2 preview after reloading the same document identity never grants confirmation or writes edits', async () => {
  let finish!: (value: typeof preview) => void
  vi.mocked(chartInspect).mockResolvedValue(recoveredScene())
  vi.mocked(chartCompute).mockReturnValue(new Promise(resolve => { finish = resolve }))
  await act(async () => useChartBindStore.getState().openFor(currentPanel()))
  await act(async () => { button('preview').click() })
  const next = useDocumentStore.getState().buildProject()
  await act(async () => { await useDocumentStore.getState().switchDocument(next, 'chart-dialog-test') })
  const before = structuredClone(currentPanel()), history = useDocumentStore.getState().past.length
  await act(async () => finish(preview))
  expect(document.querySelector('[data-chart-bind-action="confirm"]')).toBeNull()
  expect(currentPanel()).toEqual(before)
  expect(useDocumentStore.getState().past.length).toBe(history)
})

describe('N4 legend region', () => {
  const input = (selector: string) => document.querySelector<HTMLInputElement>(selector)!
  const change = (el: HTMLInputElement, value: string) => {
    // React 受控输入：走原型 value setter 再发 input 事件，onChange 才会触发
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
    setter.call(el, value)
    el.dispatchEvent(new Event('input', { bubbles: true }))
  }

  it('has no legend editor until a region is explicitly added (no fake switch)', async () => {
    expect(document.querySelector('[data-chart-legend]')).toBeNull()
    expect(document.querySelector('[data-chart-legend-add]')).not.toBeNull()
    await act(async () => document.querySelector<HTMLButtonElement>('[data-chart-legend-add]')!.click())
    expect(document.querySelector('[data-chart-legend="0"]')).not.toBeNull()
    expect(document.querySelector('[data-chart-legend-add]')).toBeNull()
    await act(async () => document.querySelector<HTMLButtonElement>('[data-chart-legend-remove="0"]')!.click())
    expect(document.querySelector('[data-chart-legend]')).toBeNull()
  })

  it('adds a region with follow/custom entries and the bind request carries the legend contract', async () => {
    await act(async () => useChartBindStore.getState().patchForm({ datasetText: 'x,a,b\n0,1,2\n1,3,4', series: [
      { node: 'curve-a', kind: 'line', xCol: 'x', yCol: 'a' },
      { node: 'curve-b', kind: 'line', xCol: 'x', yCol: 'b' },
    ] }))
    await act(async () => document.querySelector<HTMLButtonElement>('[data-chart-legend-add]')!.click())
    await act(async () => {
      change(input('[data-chart-legend-node]'), 'legendbox')
      change(input('[data-chart-legend-entry-index]'), '0')
      change(input('[data-chart-legend-entry-label]'), 'curve one')
    })
    // 条目 0 跟随 curve-a（Select 由 store 断言覆盖；这里直接写表单走完整请求）
    await act(async () => useChartBindStore.getState().patchLegendEntry(0, 0, { seriesNode: 'curve-a' }))
    await act(async () => useChartBindStore.getState().addLegendEntry(0))
    await act(async () => {
      const entries = document.querySelectorAll('[data-chart-legend-entry]')
      expect(entries).toHaveLength(2)
      change(input('[data-chart-legend-entry="1"] [data-chart-legend-entry-index]'), '1')
      change(input('[data-chart-legend-entry="1"] [data-chart-legend-entry-label]'), 'note only')
    })
    await act(async () => button('preview').click())
    const request = vi.mocked(chartBind).mock.calls[0]?.[0]
    expect(request).toMatchObject({ legend: [{
      id: 'leg1', node: 'legendbox',
      entries: [
        { index: 0, label: 'curve one', series_node: 'curve-a' },
        { index: 1, label: 'note only' },
      ],
    }] })
    expect(request.legend[0].rect).toEqual([0, 0, 0, 0])
  })

  it('restores the legend editor from a chart that already carries the contract', async () => {
    const restored = structuredClone(restoredChart)
    restored.binding.legend = [{
      id: 'leg1', node: 'legendbox', rect: [12, 8, 40, 20],
      entries: [{ index: 0, label: 'series A', series: 's1' }],
    }]
    vi.mocked(chartInspect).mockResolvedValue({ scene_digest: restored.model.scene_digest,
      w_pt: 100, h_pt: 100, view_box: [0, 0, 100, 100], nodes: [{ id: 'curve-a', tag: 'path' }, { id: 'legendbox', tag: 'g', text: undefined }],
      truncated: false, restored_chart: restored })
    vi.mocked(chartBind).mockResolvedValue(restored)
    await act(async () => useChartBindStore.getState().openFor(panel))
    await act(async () => button('preview').click())
    expect(document.querySelector('[data-chart-legend="0"]')).not.toBeNull()
    expect(input('[data-chart-legend-node]').value).toBe('legendbox')
    expect(input('[data-chart-legend-entry-label]').value).toBe('series A')
    await act(async () => button('confirm').click())
    expect(currentPanel().chart).toEqual(restored)
    expect(currentPanel().chart?.binding.legend?.[0]?.entries[0]).toEqual({ index: 0, label: 'series A', series: 's1' })
  })
})
