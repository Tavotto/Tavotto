// 绑定草稿与文档分离；只有当前请求的成功预览才允许一次文档事务确认。
import { create } from 'zustand'
import { chartSourceFields } from '@/lib/chartSource'
import { chartBind, chartCompute, chartInspect } from '@/lib/chartApi'
import type { ChartComputeOptions, SceneInventory } from '@/lib/chartApi'
import { currentProjectId, onCurrentProjectChange } from '@/lib/session'
import type { ChartSourceRef, PanelChartState, PanelObject } from '@/types/document'
import { useDocumentStore } from '@/store/documentStore'

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
export interface ChartLegendEntryForm {
  /** 用户确认的原始序（区域内第几项，≥0）。 */
  index: number
  /** 确认预览里旧图例该条目的显示文字；初始取场景文本节点的文字、用户可改。 */
  label: string
  /** 跟随的系列（以其场景节点指认，确认请求里仍发 series_node）；空 = custom。 */
  seriesNode: string
}
export interface ChartLegendForm {
  id: string
  /** 旧 SVG 图例区域节点 id。 */
  node: string
  rect: [number, number, number, number]
  entries: ChartLegendEntryForm[]
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
  /** N4 图例合同草稿：空列表 = 本请求不带 legend 键（请求身份不变）。 */
  legend: ChartLegendForm[]
}
export const DEFAULT_AXIS: ChartAxisForm = {
  p1_user: 0, p1_value: 0, p2_user: 1, p2_value: 10, scale: 'linear',
}
export const DEFAULT_SERIES: ChartSeriesForm = { node: '', xCol: '', yCol: '', kind: 'line' }
export const DEFAULT_LEGEND_ENTRY: ChartLegendEntryForm = { index: 0, label: '', seriesNode: '' }
export const DEFAULT_LEGEND: ChartLegendForm = {
  id: 'leg1', node: '', rect: [0, 0, 0, 0], entries: [{ ...DEFAULT_LEGEND_ENTRY }],
}
export const DEFAULT_FORM: ChartBindForm = {
  rect: [0, 0, 0, 0], axesNodes: [],
  x: { ...DEFAULT_AXIS }, y: { ...DEFAULT_AXIS, p2_value: 100 },
  xKind: 'numeric', categoriesCol: '', series: [{ ...DEFAULT_SERIES }],
  datasetText: '', gridText: '', legend: [],
}
/** 既有 chart → 表单草稿（纯函数；导出给测试与恢复路径复用）。 */
export function formFor(chart?: PanelChartState): ChartBindForm {
  const form = structuredClone(DEFAULT_FORM)
  const ax = chart?.model.axes[0]
  if (!chart || !ax) return form
  const columns = chart.model.dataset.columns
  const names = Object.keys(columns)
  const quote = (v: string | number) => /[\t\r\n"]/.test(String(v))
    ? `"${String(v).replaceAll('"', '""')}"` : String(v)
  form.datasetText = [names, ...Array.from(
    { length: Math.max(0, ...Object.values(columns).map(c => c.length)) },
    (_, i) => names.map(name => columns[name][i] ?? ''),
  )].map(row => row.map(quote).join('\t')).join('\n')
  form.rect = [...ax.rect]
  const colorbarNodes = new Set(chart.model.series.map(sr => sr.cbar_node).filter(Boolean))
  // 图例区域节点也按「已被合同接管」排除在轴区域之外（对齐 colorbar 先例）
  const legendNodes = new Set((chart.binding.legend ?? []).map(lg => lg.node))
  form.axesNodes = chart.binding.entries
    .filter(e => e.object.startsWith('axes:') && !colorbarNodes.has(e.node)
      && !legendNodes.has(e.node)).map(e => e.node)
  // N4 图例合同还原：series id → 其场景节点（表单以节点指认，确认请求发 series_node）
  const seriesNodeOf = (id: string) => chart.model.series.find(sr => sr.id === id)?.node ?? ''
  form.legend = (chart.binding.legend ?? []).map(lg => ({
    id: lg.id,
    node: lg.node,
    rect: [...lg.rect],
    entries: lg.entries.map(e => ({
      index: e.index,
      label: e.label,
      seriesNode: e.series ? seriesNodeOf(e.series) : '',
    })),
  }))
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
  chartSource: ChartSourceRef | undefined
  projectId: string | null
  openSeq: number
  requestSeq: number
  step: ChartBindStep
  scene: SceneInventory | null
  draftSource: 'new' | 'panel' | 'svg'
  draftChart: PanelChartState | null
  /** 只属于当前草稿的原序编辑；不改变 PanelChartState 的语义模型。 */
  draftRender: ChartComputeOptions | null
  resultRender: ChartComputeOptions | null
  documentContext: { documentId: string; loadSeq: number } | null
  panelInputKey: string | null
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
  addLegend: () => void
  removeLegend: (index: number) => void
  patchLegend: (index: number, patch: Partial<ChartLegendForm>) => void
  patchLegendEntry: (legendIndex: number, entryIndex: number, patch: Partial<ChartLegendEntryForm>) => void
  addLegendEntry: (legendIndex: number) => void
  removeLegendEntry: (legendIndex: number, entryIndex: number) => void
  loadScene: () => Promise<void>
  confirmBinding: () => Promise<void>
  runPreview: () => Promise<void>
}
const invalidPreview = { result: null, resultRender: null, previewSvg: null, previewOk: false, busy: false, error: null }
const panelInputKey = (panel: PanelObject) => JSON.stringify(panel.chartSource === undefined
  ? [panel.fileId, panel.chart, panel.overrides] : [panel.fileId, panel.chart, panel.overrides, panel.chartSource])
const ownsDocument = (s: ChartBindState) => {
  const doc = useDocumentStore.getState()
  return !s.documentContext || (s.documentContext.documentId === doc.documentId && s.documentContext.loadSeq === doc.loadSeq)
}
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
      && currentProjectId() === s.projectId && ownsDocument(s)
  }
  const fail = (s: ChartBindState, err: unknown) => {
    if (!ownsRequest(s)) return
    set({ ...invalidPreview, error: { code: (err as { code?: string }).code, message: String(err) } })
  }
  return {
    open: false, panelId: null, file: null, chartSource: undefined, projectId: currentProjectId(),
    openSeq: 0, requestSeq: 0, step: 'scene', scene: null, draftSource: 'new', draftChart: null,
    draftRender: null, documentContext: null, panelInputKey: null,
    form: structuredClone(DEFAULT_FORM), formRev: 0, ...invalidPreview,
    openFor: (panel) => {
      if (panel.fileKind !== 'svg' || !panel.fileId) return
      const doc = useDocumentStore.getState()
      set(s => ({
        open: true, panelId: panel.id, file: panel.fileId,
        chartSource: panel.chartSource === undefined ? undefined : structuredClone(panel.chartSource), projectId: currentProjectId(),
        openSeq: s.openSeq + 1, requestSeq: s.requestSeq + 1, step: 'scene', scene: null,
        form: formFor(panel.chart), draftSource: panel.chart ? 'panel' : 'new',
        draftChart: panel.chart ? structuredClone(panel.chart) : null,
        draftRender: panel.chart || panel.overrides.length
          ? { render_profile: 'native-manifest/1', overrides: structuredClone(panel.overrides) } : null,
        documentContext: { documentId: doc.documentId, loadSeq: doc.loadSeq },
        panelInputKey: panelInputKey(panel),
        formRev: 0, ...invalidPreview,
      }))
      void get().loadScene()
    },
    close: () => set(s => ({
      open: false, panelId: null, file: null, chartSource: undefined, scene: null, draftSource: 'new', draftChart: null,
      draftRender: null, documentContext: null, panelInputKey: null,
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
    // N4 图例合同：首批 UI 只建一个区域（后端合同允许多个）；条目至少一条，
    // 「只绑区域不列条目」在合同层就是半接，UI 不放行。
    addLegend: () => {
      if (get().form.legend.length === 0) {
        get().patchForm({ legend: [{ ...structuredClone(DEFAULT_LEGEND) }] })
      }
    },
    removeLegend: index => get().patchForm({ legend: get().form.legend.filter((_, i) => i !== index) }),
    patchLegend: (index, patch) => get().patchForm({
      legend: get().form.legend.map((lg, i) => i === index ? { ...lg, ...patch } : lg),
    }),
    patchLegendEntry: (legendIndex, entryIndex, patch) => get().patchForm({
      legend: get().form.legend.map((lg, i) => i === legendIndex ? {
        ...lg,
        entries: lg.entries.map((e, k) => k === entryIndex ? { ...e, ...patch } : e),
      } : lg),
    }),
    addLegendEntry: legendIndex => get().patchForm({
      legend: get().form.legend.map((lg, i) => i === legendIndex ? {
        ...lg,
        entries: [...lg.entries, { ...DEFAULT_LEGEND_ENTRY, index: lg.entries.length }],
      } : lg),
    }),
    removeLegendEntry: (legendIndex, entryIndex) => {
      const lg = get().form.legend[legendIndex]
      if (!lg || lg.entries.length <= 1) return
      get().patchLegend(legendIndex, { entries: lg.entries.filter((_, k) => k !== entryIndex) })
    },
    loadScene: async () => {
      if (!get().file) return
      const s = startRequest()
      try {
        const scene = await (s.chartSource !== undefined ? chartInspect(s.file!, structuredClone(s.chartSource)) : chartInspect(s.file!))
        if (!ownsRequest(s)) return
        const graphic = scene.nodes.find(n =>
          ['path', 'rect', 'circle', 'ellipse', 'line', 'polygon', 'polyline', 'image', 'use', 'g'].includes(n.tag)
          && !['figure_1', 'patch_1'].includes(n.id))
        const restored = s.draftSource === 'new' && s.formRev === 0 ? scene.restored_chart : undefined
        const render = restored && !s.draftRender ? scene.restored_render : s.draftRender
        const form = restored ? formFor(restored) : s.form
        set({ scene, busy: false, step: 'data', draftSource: restored ? 'svg' : s.draftSource,
          draftRender: render ? structuredClone(render) : null,
          draftChart: restored ? structuredClone(restored) : s.draftChart, form: {
          ...form,
          series: form.series.map(sr => ({ ...sr, node: sr.node || graphic?.id || '' })),
          rect: form.rect[2] > 0 ? form.rect : scene.view_box ? [...scene.view_box] as ChartBindForm['rect'] : form.rect,
        } })
      } catch (err) { fail(s, err) }
    },
    confirmBinding: async () => {
      if (!get().file) return
      const s = startRequest()
      set({ result: null, previewSvg: null, previewOk: false })
      try {
        if (s.draftChart && s.formRev === 0) {
          if (!ownsRequest(s)) return
          set({ result: { ...structuredClone(s.draftChart), formRev: s.formRev },
            resultRender: s.draftRender ? structuredClone(s.draftRender) : null, busy: false, step: 'confirm' })
          await get().runPreview()
          return
        }
        const f = s.form
        const result = await chartBind({
          file: s.file!, ...chartSourceFields(s.chartSource), axes_id: 'ax1', rect: f.rect, axes_nodes: f.axesNodes,
          y: { ...f.y },
          ...(f.xKind === 'categorical'
            ? { x_kind: 'categorical' as const, categories_col: f.categoriesCol }
            : { x: { ...f.x } }),
          series: f.series.map(sr => ({ node: sr.node, kind: sr.kind,
            x_col: sr.kind === 'image' ? undefined : sr.xCol,
            y_col: sr.kind === 'image' ? undefined : sr.yCol,
            yerr_col: sr.yerrCol || undefined, cbar_node: sr.cbarNode || undefined, style: sr.style,
          })),
          dataset_text: f.datasetText || undefined,
          grid_text: f.series.some(sr => sr.kind === 'image') ? f.gridText : undefined,
          // N4 图例合同：有才发——缺省请求与既有身份逐字节相同
          ...(f.legend.length
            ? { legend: f.legend.map(lg => ({
                id: lg.id,
                node: lg.node,
                rect: [...lg.rect] as [number, number, number, number],
                entries: lg.entries.map(e => ({
                  index: e.index,
                  label: e.label,
                  ...(e.seriesNode ? { series_node: e.seriesNode } : {}),
                })),
              })) }
            : {}),
          completeness: 'self-contained',
        })
        if (!ownsRequest(s)) return
        const render = s.draftRender ? structuredClone(s.draftRender) : null
        if (render?.overrides.length) render.previous_chart = s.draftChart ? structuredClone(s.draftChart) : null
        set({ result: { ...result, formRev: s.formRev }, resultRender: render, busy: false, step: 'confirm' })
        await get().runPreview()
      } catch (err) { fail(s, err) }
    },
    runPreview: async () => {
      const { file, result, formRev } = get()
      if (!file || !result || result.formRev !== formRev) return
      const s = startRequest()
      set({ previewSvg: null, previewOk: false })
      try {
        const opts = s.resultRender ? structuredClone(s.resultRender)
          : s.chartSource !== undefined ? { render_profile: 'native-manifest/1' as const, overrides: [] } : null
        if (opts && s.chartSource !== undefined) Object.assign(opts, chartSourceFields(s.chartSource))
        const preview = opts ? await chartCompute(file, result, opts) : await chartCompute(file, result)
        if (!ownsRequest(s)) return
        set({ previewSvg: preview.svg, previewOk: true, busy: false })
      } catch (err) { fail(s, err) }
    },
  }
})
onCurrentProjectChange(() => useChartBindStore.getState().close())
export function chartBindConfirmReady(s: ChartBindState): boolean {
  return Boolean(s.file && !s.busy && !s.error && s.projectId === currentProjectId()
    && ownsDocument(s) && s.result && s.result.formRev === s.formRev && s.previewOk && s.previewSvg !== null)
}

/** 确认只作用于打开时那份面板输入，不能覆盖预览期间的新编辑。 */
export function chartBindPanelUnchanged(s: ChartBindState, panel: PanelObject): boolean {
  return ownsDocument(s) && s.panelId === panel.id && s.panelInputKey === panelInputKey(panel)
}
