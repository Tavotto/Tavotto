import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { ChartBindDialog } from './ChartBindDialog'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { chartBind, chartCompute, chartInspect } from '@/lib/chartApi'
import { literal } from '@/i18n'
import { useChartBindStore } from '@/store/chartBindStore'
import { useDocumentStore } from '@/store/documentStore'
import { emptyProject, type PanelObject } from '@/types/document'

vi.mock('@/lib/chartApi', () => ({ chartBind: vi.fn(), chartCompute: vi.fn(), chartInspect: vi.fn() }))
globalThis.IS_REACT_ACT_ENVIRONMENT = true
let host: HTMLDivElement, root: Root
const panel: PanelObject = { id: 'chart-panel', type: 'panel', fileId: 'chart.svg', fileKind: 'svg', nativeW: 100, nativeH: 100, x: 0, y: 0, w: 100, h: 100, overrides: [] }
const chart = {
  model: { schema: 'tavotto.chart-model/1', rev: 1, axes: [], series: [], dataset: { columns: {} }, scene_digest: 'a'.repeat(64), data_digest: 'b'.repeat(64), engine: { matplotlib: '3', numpy: '2', tavotto: '0' } },
  binding: { model_rev: 1, scene_digest: 'a'.repeat(64), entries: [] },
}
const preview = { svg: '<svg xmlns="http://www.w3.org/2000/svg"/>', facts: {}, scene_digest: chart.model.scene_digest, render_revision: 'revision', model_digest: 'model', binding_digest: 'binding', engine: chart.model.engine }
const button = (value: string) => document.querySelector<HTMLButtonElement>(`[data-chart-bind-action="${value}"]`)!
const currentPanel = () => useDocumentStore.getState().doc.objects.find(o => o.id === panel.id) as PanelObject

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
