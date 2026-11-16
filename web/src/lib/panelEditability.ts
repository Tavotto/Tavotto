import { figureOriginFor, validFigureOrigin } from './figureSnapshot'
import type { PanelObject } from '@/types/document'
import { panelRender, useRenderStore, type PanelRender } from '@/store/renderStore'

type RenderFacts = { byKey: Record<string, PanelRender>; latest: Record<string, string> }

/** 图内编辑入口的资格。几何写入仍必须另经 exact/mounted manifest。 */
export function canEditPanel(
  panel: PanelObject | null | undefined,
  state: RenderFacts = useRenderStore.getState(),
): boolean {
  if (!panel) return false
  const origin = figureOriginFor(panel)
  if (origin !== undefined) return panel.chart === undefined && panel.chartSource === undefined && validFigureOrigin(origin, panel.fileId)
  if (!panel.chart) return !!panel.script
  // 同一 chart 的旧 patch 变体只证明已有可编辑对象，不授予当前几何权威。
  // renderStore 已拒绝跨 chart/source/binding 的文件级显示退路。
  const render = panelRender(state, panel)
  return !!render?.manifest && !!render.chartRevision && !render.stale
}

export function useCanEditPanel(panel: PanelObject | null | undefined): boolean {
  return useRenderStore(state => canEditPanel(panel, state))
}
