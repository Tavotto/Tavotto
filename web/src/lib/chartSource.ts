import type { ChartSourceRef, FigureDocument, PanelChartState, PanelObject } from '@/types/document'

/** 计算图表的坐标合同：/1 面板不携带（身份与请求形状逐字不变）。 */
export type ChartRenderProfile = 'native-manifest/1' | 'native-manifest/2'
import { sameRules } from './specBinding'

/** 只镜像请求，不校验/授予来源资格；在场但畸形的值也须交后端拒绝，不能降成原文件。 */
export function chartSourceFields(source: ChartSourceRef | undefined): { chart_source?: ChartSourceRef } {
  return source === undefined ? {} : { chart_source: structuredClone(source) }
}

/** 没有附件时保持既有 chart 请求身份逐字不变。`renderProfile` 只随计算图表
 *  （native-manifest/2，`panel.chartFrame.renderProfile`）出现；/1 面板不传，
 *  身份串与旧形状逐字相同——同 chart 不同坐标语义不能共用缓存（N5 第 2 步）。 */
export function chartRequestIdentity(chart?: PanelChartState, source?: ChartSourceRef, renderProfile?: ChartRenderProfile): string | undefined {
  if (!chart && source === undefined) return undefined
  if (renderProfile === undefined) {
    return source === undefined ? JSON.stringify(chart) : JSON.stringify({ chart, chart_source: source })
  }
  return JSON.stringify({ chart, chart_source: source, render_profile: renderProfile })
}

/** 可选末尾参数：无 chart/ref 时返回空 tuple，保留既有调用的实参数量。 */
export function chartPreviewArgs(panel?: Pick<PanelObject, 'chart' | 'chartSource'> | null): [PanelChartState?, ChartSourceRef?] {
  if (panel?.chartSource !== undefined) return [panel.chart, panel.chartSource]
  return panel?.chart ? [panel.chart] : []
}

const sceneOf = (p: PanelObject) => [p.fileId, p.fileKind, p.chart?.model.scene_digest, p.chart?.binding.scene_digest]

/** 复用文档现有提交/撤销/派生清理入口；无关编辑不把坏引用静默变成 legacy。 */
export function clearChangedChartSources(next: FigureDocument, before: FigureDocument): void {
  for (let index = 0; index < next.objects.length; index++) {
    const panel = next.objects[index], previous = before.objects[index]
    if (panel.type !== 'panel' || panel.chartSource === undefined || previous?.type !== 'panel'
      || panel.id !== previous.id || !sameRules(panel.chartSource, previous.chartSource)) continue
    if (!sameRules(sceneOf(panel), sceneOf(previous))) delete panel.chartSource
  }
}
