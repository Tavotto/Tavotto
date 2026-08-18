/**
 * 图内元素预览的**接线层**：把「画布上挂的是哪一版 SVG」「提交后该等哪一版」
 * 这两件只有 store 才知道的事，收敛成三个动作，画布交互与属性页共用同一份。
 *
 * 分工再强调一遍（数据流见 store/svgPreviewStore.ts 顶部）：
 *   begin  → 只记账，不改任何东西
 *   预览   → 只改 SVG DOM（rAF 合并），**不 commit、不进历史、不发后端**
 *   commit → 调用方已经把正式 override 写进 documentStore（一条历史），
 *            这里只负责登记「等哪一版权威渲染」并让预览继续挂着
 */
import {
  beginPreview,
  cancelPreview,
  commitPreview,
  getHistoryMode,
  type HistoryMode,
} from '@/store/svgPreviewStore'
import { activeRenderKey, panelRender, renderKeyOf, useRenderStore } from '@/store/renderStore'
import { useDocumentStore } from '@/store/documentStore'
import type { PanelObject } from '@/types/document'

/** 开一个预览会话。渲染键取「画布上此刻挂的那一版」——预览贴在哪份 DOM 上，
 *  账本就必须认哪个键，否则还原会写到一批野引用上。 */
export function beginElementPreview(panel: PanelObject, historyMode?: HistoryMode): void {
  const rs = useRenderStore.getState()
  const render = panelRender(rs, panel)
  beginPreview({
    panelId: panel.id,
    renderKey: activeRenderKey(rs, panel),
    rev: render?.rev ?? 0,
    historyMode: historyMode ?? getHistoryMode(),
    sizeMm: render?.manifest?.size_mm,
  })
}

/**
 * 收尾：正式 override 已经写进文档了，从**文档里现取**面板算出等待键。
 * 不接受调用方传进来的 patch 列表——闭包里那份 panel 可能是上一帧的，
 * 而等待键必须与 renderKeyOf 逐字节一致，否则权威渲染回来时认不出来，
 * 预览就永远挂着不走。
 */
export function commitElementPreview(panelId: string): void {
  const panel = useDocumentStore.getState().doc.objects.find((o) => o.id === panelId)
  if (panel?.type !== 'panel') {
    cancelPreview()
    return
  }
  commitPreview(
    panel.overrides.map((o) => ({ gid: o.gid, prop: o.prop, value: o.value })),
    renderKeyOf(panel),
  )
}

export { cancelPreview as cancelElementPreview }
