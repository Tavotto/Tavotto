// 绑定草稿与文档分离；只有当前请求的成功预览才允许一次文档事务确认。
import { create } from 'zustand'
import { chartBind, chartCompute, chartInspect } from '@/lib/chartApi'
import type { SceneInventory } from '@/lib/chartApi'
import { currentProjectId, onCurrentProjectChange } from '@/lib/session'
import type { PanelChartState, PanelObject } from '@/types/document'

export type ChartBindStep = 'scene' | 'data' | 'confirm' | 'error'
export interface ChartAxisForm {
  p1_user: number
  p1_value: number
  p2_user: number
  p2_value: number
  scale: 'linear' | 'log'
}
export interface ChartSeriesForm {
  node: string
  xCol: string
  yCol: string
  yerrCol?: string
  kind: 'line' | 'scatter' | 'bar' | 'errorbar' | 'image'
  cbarNode?: string
  style?: Record<string, string | number>
}
export interface ChartBindForm {
  rect: [number, number, number, number]
  axesNodes: string[]
  x: ChartAxisForm
  y: ChartAxisForm
  xKind: 'numeric' | 'categorical'
  categoriesCol: string
  series: ChartSeriesForm[]
  datasetText: string
  gridText: string
}
export const DEFAULT_AXIS: ChartAxisForm = {
  p1_user: 0, p1_value: 0, p2_user: 1, p2_value: 10, scale: 'linear',
}
export const DEFAULT_SERIES: ChartSeriesForm = { node: '', xCol: '', yCol: '', kind: 'line' }
export const DEFAULT_FORM: ChartBindForm = {
  rect: [0, 0, 0, 0], axesNodes: [],
  x: { ...DEFAULT_AXIS }, y: { ...DEFAULT_AXIS, p2_value: 100 },
  xKind: 'numeric', categoriesCol: '', series: [{ ...DEFAULT_SERIES }],
  datasetText: '', gridText: '',
}
function formFor(panel: PanelObject): ChartBindForm {
  const form = structuredClone(DEFAULT_FORM)
  const chart = panel.chart
  const ax = chart?.model.axes[0]
  if (!chart || !ax) return form
  const columns = chart.model.dataset.columns
  const names = Object.keys(columns)
  const quote = (v: string | number) => `"${String(v).replaceAll('"', '""')}"`
  form.datasetText = [names, ...Array.from(
    { length: Math.max(0, ...Object.values(columns).map(c => c.length)) },
    (_, i) => names.map(name => columns[name][i] ?? ''),
  )].map(row => row.map(quote).join(',')).join('\n')
  form.rect = [...ax.rect]
  form.axesNodes = chart.binding.entries.filter(e => e.object.startsWith('axes:')).map(e => e.node)
  form.x = { ...DEFAULT_AXIS, scale: ax.xscale, p1_value: ax.xlim[ax.invert_x ? 1 : 0], p2_value: ax.xlim[ax.invert_x ? 0 : 1] }
  form.y = { ...DEFAULT_AXIS, scale: ax.yscale, p1_value: ax.ylim[ax.invert_y ? 1 : 0], p2_value: ax.ylim[ax.invert_y ? 0 : 1] }
  form.xKind = ax.kind === 'categorical' ? 'categorical' : 'numeric'
  form.series = chart.model.series.map(sr => ({
    node: sr.node, kind: sr.kind, xCol: sr.x, yCol: sr.y,
    yerrCol: sr.yerr, cbarNode: sr.cbar_node, style: { ...sr.style },
  }))
  form.categoriesCol = form.xKind === 'categorical' ? form.series[0]?.xCol ?? '' : ''
  const image = chart.model.series.find(sr => sr.kind === 'image')
  const grid = image?.grid ? chart.model.dataset.grids?.[image.grid] : undefined
  if (grid) form.gridText = grid.values.map(row => row.join('\t')).join('\n')
  return form
}
interface ChartBindState {
  open: boolean
  panelId: string | null
  file: string | null
  projectId: string | null
  openSeq: number
  requestSeq: number
  step: ChartBindStep
  scene: SceneInventory | null
  form: ChartBindForm
  formRev: number
  result: (PanelChartState & { formRev: number }) | null
  previewSvg: string | null
  previewOk: boolean
  error: { code?: string, message: string } | null
  busy: boolean
}
interface ChartBindActions {
  openFor: (panel: PanelObject) => void
  close: () => void
  setStep: (step: ChartBindStep) => void
  patchForm: (patch: Partial<ChartBindForm>) => void
  patchSeries: (index: number, patch: Partial<ChartSeriesForm>) => void
  addSeries: () => void
  removeSeries: (index: number) => void
  loadScene: () => Promise<void>
  confirmBinding: () => Promise<void>
  runPreview: () => Promise<void>
}
const invalidPreview = { result: null, previewSvg: null, previewOk: false, busy: false, error: null }
export const useChartBindStore = create<ChartBindState & ChartBindActions>()((set, get) => {
  const startRequest = () => {
    const requestSeq = get().requestSeq + 1
    set({ requestSeq, busy: true, error: null })
    return { ...get(), requestSeq }
  }
  const ownsRequest = (s: ChartBindState) => {
    const cur = get()
    return cur.requestSeq === s.requestSeq && cur.openSeq === s.openSeq
      && cur.file === s.file && cur.formRev === s.formRev
      && currentProjectId() === s.projectId
  }
  const fail = (s: ChartBindState, err: unknown) => {
    if (!ownsRequest(s)) return
    set({ ...invalidPreview, error: { code: (err as { code?: string }).code, message: String(err) } })
  }
  return {
    open: false, panelId: null, file: null, projectId: currentProjectId(),
    openSeq: 0, requestSeq: 0, step: 'scene', scene: null,
    form: structuredClone(DEFAULT_FORM), formRev: 0, ...invalidPreview,
    openFor: (panel) => {
      if (panel.fileKind !== 'svg' || !panel.fileId) return
      set(s => ({
        open: true, panelId: panel.id, file: panel.fileId, projectId: currentProjectId(),
        openSeq: s.openSeq + 1, requestSeq: s.requestSeq + 1, step: 'scene', scene: null,
        form: formFor(panel), formRev: 0, ...invalidPreview,
      }))
      void get().loadScene()
    },
    close: () => set(s => ({
      open: false, panelId: null, file: null, scene: null,
      projectId: currentProjectId(), openSeq: s.openSeq + 1, requestSeq: s.requestSeq + 1,
      ...invalidPreview,
    })),
    setStep: step => set({ step, error: null }),
    patchForm: patch => set(s => ({
      form: { ...s.form, ...patch }, formRev: s.formRev + 1, requestSeq: s.requestSeq + 1,
      ...invalidPreview,
    })),
    patchSeries: (index, patch) => get().patchForm({
      series: get().form.series.map((sr, i) => i === index ? { ...sr, ...patch } : sr),
    }),
    addSeries: () => get().patchForm({ series: [...get().form.series, { ...DEFAULT_SERIES }] }),
    removeSeries: index => {
      if (get().form.series.length > 1) get().patchForm({ series: get().form.series.filter((_, i) => i !== index) })
    },
    loadScene: async () => {
      if (!get().file) return
      const s = startRequest()
      try {
        const scene = await chartInspect(s.file!)
        if (!ownsRequest(s)) return
        const graphic = scene.nodes.find(n =>
          ['path', 'rect', 'circle', 'ellipse', 'line', 'polygon', 'polyline', 'image', 'use', 'g'].includes(n.tag)
          && !['figure_1', 'patch_1'].includes(n.id))
        set({ scene, busy: false, step: 'data', form: {
          ...s.form,
          series: s.form.series.map(sr => ({ ...sr, node: sr.node || graphic?.id || '' })),
          rect: s.form.rect[2] > 0 ? s.form.rect : scene.view_box ? [...scene.view_box] as ChartBindForm['rect'] : s.form.rect,
        } })
      } catch (err) { fail(s, err) }
    },
    confirmBinding: async () => {
      if (!get().file) return
      const s = startRequest()
      set({ result: null, previewSvg: null, previewOk: false })
      try {
        const f = s.form
        const result = await chartBind({
          file: s.file!, axes_id: 'ax1', rect: f.rect, axes_nodes: f.axesNodes,
          x: { ...f.x }, y: { ...f.y },
          ...(f.xKind === 'categorical' ? { x_kind: 'categorical', categories_col: f.categoriesCol } : {}),
          series: f.series.map(sr => ({ node: sr.node, kind: sr.kind,
            x_col: sr.kind === 'image' ? undefined : sr.xCol,
            y_col: sr.kind === 'image' ? undefined : sr.yCol,
            yerr_col: sr.yerrCol || undefined, cbar_node: sr.cbarNode || undefined, style: sr.style,
          })),
          dataset_text: f.datasetText || undefined,
          grid_text: f.series.some(sr => sr.kind === 'image') ? f.gridText : undefined,
          completeness: 'self-contained',
        })
        if (!ownsRequest(s)) return
        set({ result: { ...result, formRev: s.formRev }, busy: false, step: 'confirm' })
        await get().runPreview()
      } catch (err) { fail(s, err) }
    },
    runPreview: async () => {
      const { file, result, formRev } = get()
      if (!file || !result || result.formRev !== formRev) return
      const s = startRequest()
      set({ previewSvg: null, previewOk: false })
      try {
        const preview = await chartCompute(file, result)
        if (!ownsRequest(s)) return
        set({ previewSvg: preview.svg, previewOk: true, busy: false })
      } catch (err) { fail(s, err) }
    },
  }
})
onCurrentProjectChange(() => useChartBindStore.getState().close())
export function chartBindConfirmReady(s: ChartBindState): boolean {
  return Boolean(s.file && !s.busy && !s.error && s.projectId === currentProjectId()
    && s.result && s.result.formRev === s.formRev && s.previewOk && s.previewSvg !== null)
}
