// SVG 计算图表绑定向导的状态机（ADR 0166，SG10）。
// 只管流程状态与调用编排；判据全部在后端（chartbind/chartmodel），本 store
// 不做第二份校验。确认落盘走 documentStore 的既有事务（一次 commit、可撤销）。
import { create } from 'zustand'

import { chartBind, chartCompute, chartInspect } from '@/lib/chartApi'
import type { SceneInventory } from '@/lib/chartApi'
import type { PanelChartState } from '@/types/document'
import type { PanelObject } from '@/types/document'

export type ChartBindStep = 'scene' | 'data' | 'series' | 'confirm' | 'error'

export interface ChartBindForm {
  /** 绘图区（SVG 用户单位）：v1 用数值输入，后续批次升级为画布框选 */
  rect: [number, number, number, number]
  x: { p1_user: number, p1_value: number, p2_user: number, p2_value: number }
  y: { p1_user: number, p1_value: number, p2_user: number, p2_value: number }
  scale: 'linear' | 'log'
  datasetText: string
  gridText: string
  seriesNode: string
  xCol: string
  yCol: string
  kind: 'line' | 'scatter' | 'bar' | 'image'
}

interface ChartBindState {
  open: boolean
  panelId: string | null
  file: string | null
  step: ChartBindStep
  scene: SceneInventory | null
  form: ChartBindForm
  result: PanelChartState | null
  previewSvg: string | null
  error: { code?: string, message: string } | null
  busy: boolean
}

export const DEFAULT_FORM: ChartBindForm = {
  rect: [0, 0, 0, 0],
  x: { p1_user: 0, p1_value: 0, p2_user: 1, p2_value: 10 },
  y: { p1_user: 0, p1_value: 0, p2_user: 1, p2_value: 100 },
  scale: 'linear',
  datasetText: '',
  gridText: '',
  seriesNode: '',
  xCol: '',
  yCol: '',
  kind: 'line',
}

interface ChartBindActions {
  openFor: (panel: PanelObject) => void
  close: () => void
  setStep: (step: ChartBindStep) => void
  patchForm: (patch: Partial<ChartBindForm>) => void
  loadScene: () => Promise<void>
  confirmBinding: () => Promise<void>
  runPreview: () => Promise<void>
}

export const useChartBindStore = create<ChartBindState & ChartBindActions>()((set, get) => ({
  open: false,
  panelId: null,
  file: null,
  step: 'scene',
  scene: null,
  form: DEFAULT_FORM,
  result: null,
  previewSvg: null,
  error: null,
  busy: false,

  openFor: (panel) => {
    if (panel.fileKind !== 'svg' || !panel.fileId) return
    set({
      open: true,
      panelId: panel.id,
      file: panel.fileId,
      step: 'scene',
      scene: null,
      form: DEFAULT_FORM,
      result: null,
      previewSvg: null,
      error: null,
      busy: false,
    })
    void get().loadScene()
  },

  close: () => set({ open: false }),

  setStep: (step) => set({ step, error: null }),

  patchForm: (patch) => set((s) => ({ form: { ...s.form, ...patch } })),

  loadScene: async () => {
    const file = get().file
    if (!file) return
    set({ busy: true, error: null })
    try {
      const scene = await chartInspect(file)
      const graphic = scene.nodes.find(
        n => ['path', 'rect', 'circle', 'ellipse', 'line', 'polygon', 'polyline', 'image', 'use', 'g']
          .includes(n.tag) && !['figure_1', 'patch_1'].includes(n.id),
      )
      set({
        scene,
        busy: false,
        step: 'data',
        form: {
          ...get().form,
          seriesNode: graphic?.id ?? '',
          // 视图框作为绘图区的初始候选（确认预览里对照）
          rect: scene.view_box
            ? [scene.view_box[0], scene.view_box[1], scene.view_box[2], scene.view_box[3]]
            : get().form.rect,
        },
      })
    } catch (err) {
      set({ busy: false, error: { code: (err as { code?: string }).code, message: String(err) } })
    }
  },

  confirmBinding: async () => {
    const { file, form } = get()
    if (!file) return
    set({ busy: true, error: null })
    try {
      const evidence = (axis: 'x' | 'y') => ({
        p1_user: form[axis].p1_user,
        p1_value: form[axis].p1_value,
        p2_user: form[axis].p2_user,
        p2_value: form[axis].p2_value,
        scale: axis === 'x' ? form.scale : form.scale,
      })
      const result = await chartBind({
        file,
        rect: form.rect,
        x: evidence('x'),
        y: evidence('y'),
        series: [
          {
            node: form.seriesNode,
            x_col: form.kind === 'image' ? undefined : form.xCol,
            y_col: form.kind === 'image' ? undefined : form.yCol,
            kind: form.kind,
          },
        ],
        dataset_text: form.datasetText || undefined,
        grid_text: form.kind === 'image' ? form.gridText : undefined,
        completeness: 'self-contained',
      })
      set({ result, busy: false, step: 'confirm' })
      await get().runPreview()
    } catch (err) {
      set({
        busy: false,
        error: { code: (err as { code?: string }).code, message: String(err) },
      })
    }
  },

  runPreview: async () => {
    const { file, result } = get()
    if (!file || !result) return
    set({ busy: true })
    try {
      const preview = await chartCompute(file, result)
      set({ previewSvg: preview.svg, busy: false })
    } catch (err) {
      set({
        busy: false,
        error: { code: (err as { code?: string }).code, message: String(err) },
      })
    }
  },
}))

/** 选择器：确认动作是否可用（调 UI 的 disable，不做第二份判据——最终以後端为准）。 */
export function chartBindConfirmReady(s: ChartBindState): boolean {
  if (!s.file || s.busy) return false
  if (s.form.kind === 'image') return s.form.gridText.trim().length > 0
  return s.form.seriesNode !== '' && s.form.xCol !== '' && s.form.yCol !== ''
}
