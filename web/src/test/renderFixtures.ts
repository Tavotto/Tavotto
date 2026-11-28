/**
 * 「这个面板变体已经**精确渲染过**」的唯一一份测试装置。
 *
 * 手写 `patch(renderKeyOf(p), { manifest, status: 'ready' })` 造出来的是一个
 * 真实渲染永远不会出现的形状：有 manifest、却没有 `lastPatches`。
 * `exactPanelRender` 正是拿 `lastPatches` 判「这一版确实画出来过」的
 * （issue #131 的权威判据），所以那种半成品在用例里会被当成「权威未就位」。
 *
 * 用例要的是「已经画好了」，就用这个；要的是「还没画好」，直接
 * `patch(key, { status: 'rendering', wantPatches })`，别给 manifest。
 */
import { renderKeyOf, renderSourceKey, renderRequestIdentity, useRenderStore } from '@/store/renderStore'
import { figureOriginFor } from '@/lib/figureSnapshot'
import type { Manifest } from '@/lib/api'
import type { PanelObject } from '@/types/document'

export function seedExactRender(
  panel: PanelObject,
  manifest: Manifest,
  extra: {
    svg?: string
    rev?: number
    chartRevision?: string
    /** /2 配对帧（真实链路由 renderStore 从 render 响应落账）；F→S 写入适配的权威取它 */
    renderFrame?: { version: 1; source: 'computed-svg'; source_viewport_pt: [number, number]; rect_pt: [number, number, number, number]; edit_basis: 'source-viewport-pt' }
  } = {},
): void {
  const key = renderKeyOf(panel)
  const origin = figureOriginFor(panel)
  const source = renderSourceKey(panel)
  // 与真实 writer 同一形状：/2 面板的请求身份带 renderProfile（renderStore 写 lastPatches 的那把尺）
  const variant = renderRequestIdentity(panel.overrides, panel.chart, panel.chartSource, origin, panel.chartFrame?.renderProfile)
  useRenderStore.getState().patch(key, {
    fileId: panel.fileId,
    ...(origin === undefined ? {} : { figureOrigin: structuredClone(origin) }),
    rev: extra.rev ?? 1,
    manifest,
    svg: extra.svg ?? '<svg/>',
    status: 'ready',
    stale: false,
    ...(extra.chartRevision === undefined ? {} : { chartRevision: extra.chartRevision }),
    ...(extra.renderFrame === undefined ? {} : { renderFrame: extra.renderFrame }),
    // 权威判据就是这一条：渲染成功时由 renderStore 写下
    lastPatches: variant,
    wantPatches: variant,
  })
  useRenderStore.setState((s) => ({
    latest: { ...s.latest, [source]: key },
    recent: { ...s.recent, [source]: [key, ...(s.recent[source] ?? [])] },
  }))
}
