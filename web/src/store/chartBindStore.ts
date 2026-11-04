// SVG 计算图表绑定向导的状态机（ADR 0166；NEXT_PHASE R04）。
// 只管流程状态与调用编排；判据全部在后端（chartbind/chartmodel），本 store
// 不做第二份校验。确认落盘走 documentStore 的既有事务（一次 commit、可撤销）。
//
// 防串合同（F1–F3）：
// - 每次打开面板递增 openSeq；inspect/bind/compute 只在 openSeq 与 file 仍是
//   当前值时写入状态——迟到的旧响应一律丢弃（F1）。
// - 表单（数据/系列/轴）变化即失效已确认结果与预览：result/previewSvg 清空，
//   确认资格随之消失；草稿与「本次已验证模型」严格分开（F2）。
// - 只有「当前表单对应的成功预览」才给最终提交资格：预览失败清空预览并
//   使 ready=false（F3）。
import { create } from 'zustand'

import { chartBind, chartCompute, chartInspect } from '@/lib/chartApi'
import type { SceneInventory } from '@/lib/chartApi'
import type { PanelChartState } from '@/types/document'
import type { PanelObject } from '@/types/document'

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
  kind: 'line' | 'scatter' | 'bar' | 'image'
  cbarNode?: string
}

export interface ChartBindForm {
  /** 绘图区（SVG 用户单位）：v1 用数值输入，后续批次升级为画布框选 */
  rect: [number, number, number, number]
  x: ChartAxisForm
  y: ChartAxisForm
  series: ChartSeriesForm[]
  datasetText: string
  gridText: string
}

export const DEFAULT_AXIS: ChartAxisForm = {
  p1_user: 0,
  p1_value: 0,
  p2_user: 1,
  p2_value: 10,
  scale: 'linear',
}

export const DEFAULT_SERIES: ChartSeriesForm = {
  node: '',
  xCol: '',
  yCol: '',
  kind: 'line',
}

export const DEFAULT_FORM: ChartBindForm = {
  rect: [0, 0, 0, 0],
  x: { ...DEFAULT_AXIS },
  y: { ...DEFAULT_AXIS, p2_value: 100 },
  series: [{ ...DEFAULT_SERIES }],
  datasetText: '',
  gridText: '',
}

interface ChartBindState {
  open: boolean
  panelId: string | null
  file: string | null
  /** 防串代际：openFor/close 递增；迟到响应按 openSeq 丢弃（F1） */
  openSeq: number
  step: ChartBindStep
  scene: SceneInventory | null
  form: ChartBindForm
  /** 表单代际：patchForm 递增；bind/compute 的产物只对当时的代际有资格（F2） */
  formRev: number
  /** 绑定产物（携带它生成时的 formRev） */
  result: (PanelChartState & { formRev: number }) | null
  previewSvg: string | null
  /** 当前 formRev 是否已有成功预览（F3：失败即 false） */
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

export const useChartBindStore = create<ChartBindState & ChartBindActions>()((set, get) => ({
  open: false,
  panelId: null,
  file: null,
  openSeq: 0,
  step: 'scene',
  scene: null,
  form: DEFAULT_FORM,
  formRev: 0,
  result: null,
  previewSvg: null,
  previewOk: false,
  error: null,
  busy: false,

  openFor: (panel) => {
    if (panel.fileKind !== 'svg' || !panel.fileId) return
    const seq = get().openSeq + 1
    set({
      open: true,
      panelId: panel.id,
      file: panel.fileId,
      openSeq: seq,
      step: 'scene',
      scene: null,
      form: DEFAULT_FORM,
      formRev: 0,
      result: null,
      previewSvg: null,
      previewOk: false,
      error: null,
      busy: false,
    })
    void get().loadScene()
  },

  close: () => {
    // 关闭作废一切在途/已确认状态（F1：close 后迟到响应不写状态）
    set((s) => ({
      open: false,
      openSeq: s.openSeq + 1,
      scene: null,
      result: null,
      previewSvg: null,
      previewOk: false,
      error: null,
      busy: false,
    }))
  },

  setStep: (step) => set({ step, error: null }),

  patchForm: (patch) => {
    // 表单变化 = 草稿变化：旧确认产物与预览立即失去资格（F2）
    set((s) => ({
      form: { ...s.form, ...patch },
      formRev: s.formRev + 1,
      result: null,
      previewSvg: null,
      previewOk: false,
    }))
  },

  patchSeries: (index, patch) => {
    set((s) => {
      const series = s.form.series.map((sr, i) => (i === index ? { ...sr, ...patch } : sr))
      return {
        form: { ...s.form, series },
        formRev: s.formRev + 1,
        result: null,
        previewSvg: null,
        previewOk: false,
      }
    })
  },

  addSeries: () => {
    set((s) => ({
      form: { ...s.form, series: [...s.form.series, { ...DEFAULT_SERIES }] },
      formRev: s.formRev + 1,
      result: null,
      previewSvg: null,
      previewOk: false,
    }))
  },

  removeSeries: (index) => {
    set((s) => {
      if (s.form.series.length <= 1) return {}
      return {
        form: { ...s.form, series: s.form.series.filter((_, i) => i !== index) },
        formRev: s.formRev + 1,
        result: null,
        previewSvg: null,
        previewOk: false,
      }
    })
  },

  loadScene: async () => {
    const { file, openSeq } = get()
    if (!file) return
    set({ busy: true, error: null })
    try {
      const scene = await chartInspect(file)
      const cur = get()
      if (cur.openSeq !== openSeq || cur.file !== file) return // F1：迟到/串台
      const graphic = scene.nodes.find(
        (n) =>
          ['path', 'rect', 'circle', 'ellipse', 'line', 'polygon', 'polyline', 'image', 'use', 'g']
            .includes(n.tag) && !['figure_1', 'patch_1'].includes(n.id),
      )
      set({
        scene,
        busy: false,
        step: 'data',
        form: {
          ...cur.form,
          series: cur.form.series.map((sr) => ({ ...sr, node: graphic?.id ?? sr.node })),
          // 视图框作为绘图区的初始候选（确认预览里对照）
          rect: scene.view_box
            ? [scene.view_box[0], scene.view_box[1], scene.view_box[2], scene.view_box[3]]
            : cur.form.rect,
        },
      })
    } catch (err) {
      const cur = get()
      if (cur.openSeq !== openSeq || cur.file !== file) return // F1
      set({ busy: false, error: { code: (err as { code?: string }).code, message: String(err) } })
    }
  },

  confirmBinding: async () => {
    const { file, form, openSeq, formRev } = get()
    if (!file) return
    set({ busy: true, error: null })
    try {
      const result = await chartBind({
        file,
        rect: form.rect,
        x: { ...form.x },
        y: { ...form.y },
        series: form.series.map((sr) => ({
          node: sr.node,
          x_col: sr.kind === 'image' ? undefined : sr.xCol,
          y_col: sr.kind === 'image' ? undefined : sr.yCol,
          yerr_col: sr.yerrCol || undefined,
          cbar_node: sr.cbarNode || undefined,
          kind: sr.kind,
        })),
        dataset_text: form.datasetText || undefined,
        grid_text: form.series.some((sr) => sr.kind === 'image') ? form.gridText : undefined,
        completeness: 'self-contained',
      })
      const cur = get()
      if (cur.openSeq !== openSeq || cur.file !== file) return // F1
      // 绑定产物携带生成时的表单代际；表单再动就失去资格（F2）
      set({ result: { ...result, formRev }, busy: false, step: 'confirm' })
      await get().runPreview()
    } catch (err) {
      const cur = get()
      if (cur.openSeq !== openSeq || cur.file !== file) return // F1
      set({
        busy: false,
        error: { code: (err as { code?: string }).code, message: String(err) },
      })
    }
  },

  runPreview: async () => {
    const { file, result, openSeq, formRev } = get()
    if (!file || !result || result.formRev !== formRev) {
      // 没有当前表单代际的绑定产物：无预览资格（F2/F3）
      set({ previewSvg: null, previewOk: false })
      return
    }
    set({ busy: true })
    try {
      const preview = await chartCompute(file, result)
      const cur = get()
      if (cur.openSeq !== openSeq || cur.file !== file || cur.formRev !== formRev) return // F1/F2
      set({ previewSvg: preview.svg, previewOk: true, busy: false })
    } catch (err) {
      const cur = get()
      if (cur.openSeq !== openSeq || cur.file !== file || cur.formRev !== formRev) return // F1/F2
      // 预览失败：旧预览清除、提交资格消失（F3）
      set({
        busy: false,
        previewSvg: null,
        previewOk: false,
        error: { code: (err as { code?: string }).code, message: String(err) },
      })
    }
  },
}))

/**
 * 最终提交资格：有当前表单代际的绑定产物 **且** 该代际已有成功预览。
 * busy/错误由调用方按 UI 需要另行展示——这里只回答「能不能确认」。
 */
export function chartBindConfirmReady(s: ChartBindState): boolean {
  return Boolean(
    s.file && s.result && s.result.formRev === s.formRev && s.previewOk && s.previewSvg !== null,
  )
}
