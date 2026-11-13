// 计算图表的渲染缓存（ADR 0182；NEXT_PHASE R06）。
// 键 = render_revision（来源+模型完整身份，后端 compute 返回）；同一身份只算一次。
// 只管取图与状态；判据在后端。项目代际：clear() 由项目切换时的既有代际机制调用。
import { create } from 'zustand'

import { chartCompute } from '@/lib/chartApi'
import { digestOf } from '@/types/chartModel'
import { onCurrentProjectChange } from '@/lib/session'
import type { PanelChartState } from '@/types/document'

export interface ChartRenderEntry {
  status: 'idle' | 'computing' | 'ready' | 'error'
  svg: string | null
  /** 稳定错误码（chart_source_mismatch / chart_engine_mismatch / chart_source_changed …） */
  code?: string
  message?: string
  requestId?: number
}

interface ChartRenderState {
  byRevision: Record<string, ChartRenderEntry>
  /** 缓存代际：clear/drop 递增；在途响应按代际丢弃（F6） */
  generation: number
  ensure: (file: string, chart: PanelChartState) => void
  clear: () => void
  drop: (revision: string) => void
}

let nextRequestId = 0
export const useChartRenderStore = create<ChartRenderState>()((set, get) => ({
  byRevision: {},
  generation: 0,

  ensure: (file, chart) => {
    const revision = revisionOf(chart)
    if (!revision) return
    const existing = get().byRevision[revision]
    if (existing && existing.status !== 'error') return
    const generation = get().generation
    const requestId = ++nextRequestId
    set((s) => ({
      byRevision: {
        ...s.byRevision,
        [revision]: { status: 'computing', svg: null, requestId },
      },
    }))
    chartCompute(file, chart)
      .then((res) => {
        if (get().generation !== generation || get().byRevision[revision]?.requestId !== requestId) return
        set((s) => ({
          byRevision: {
            ...s.byRevision,
            [revision]: { status: 'ready', svg: res.svg },
            // 后端完整身份也挂一份，供保存/导出消费
            [res.render_revision]: { status: 'ready', svg: res.svg },
          },
        }))
      })
      .catch((err: { code?: string, message?: string }) => {
        if (get().generation !== generation || get().byRevision[revision]?.requestId !== requestId) return
        set((s) => ({
          byRevision: {
            ...s.byRevision,
            [revision]: {
              status: 'error',
              svg: null,
              code: err.code,
              message: err.message ?? String(err),
            },
          },
        }))
      })
  },

  clear: () =>
    set((s) => ({ byRevision: {}, generation: s.generation + 1 })),

  drop: (revision) =>
    set((s) => ({
      byRevision: (() => {
        const next = { ...s.byRevision }
        delete next[revision]
        return next
      })(),
    })),
}))

onCurrentProjectChange(() => useChartRenderStore.getState().clear())

/**
 * 完整渲染身份：直接消费后端 compute 返回的 render_revision；同一来源/模型/绑定
 * 是同一版本，模型或来源任一变化即新版本（与后端 panel_identity 同一判据的镜像）。
 * 这里只从 chart 状态提取——真正的判据在后端。
 */
export function revisionOf(chart: PanelChartState): string | null {
  // 完整变体身份（F4/F5）：场景摘要 + **模型与绑定的全部内容**——
  // 同来源/数据下改轴范围、样式或绑定节点都必须是不同版本。
  const model = chart.model as { scene_digest?: string } | undefined
  if (!model?.scene_digest) return null
  return `chart:${model.scene_digest}:${digestOf({ model: chart.model, binding: chart.binding })}`
}
