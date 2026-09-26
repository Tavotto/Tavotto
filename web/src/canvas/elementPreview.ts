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
  patchId,
  previewSession,
  type HistoryMode,
} from '@/store/svgPreviewStore'
import { activeRenderKey, panelRender, renderKeyOf, useRenderStore } from '@/store/renderStore'
import { useDocumentStore } from '@/store/documentStore'
import type { PanelObject } from '@/types/document'

/** 开一个预览会话。渲染键取「画布上此刻挂的那一版」——预览贴在哪份 DOM 上，
 *  账本就必须认哪个键，否则还原会写到一批野引用上。 */
/**
 * 手势开始那一刻面板上已有的 override（按会话记）。提交时只把**这次手势写出来的**
 * patch 交给预览会话：`settleUnbackedCommit` 拿它判「预览的依据还在不在文档里」，
 * 混进手势之前就有的无关 override 的话，等图期间改掉 / 删掉那条无关的，就会把
 * 仍有依据的拖动预览撤掉、元素先弹回原位（#591 评审）。
 */
let baseline: { session: number; ids: Set<string> } | null = null

export function beginElementPreview(panel: PanelObject, historyMode?: HistoryMode): void {
  const rs = useRenderStore.getState()
  const render = panelRender(rs, panel)
  const session = beginPreview({
    panelId: panel.id,
    renderKey: activeRenderKey(rs, panel),
    rev: render?.rev ?? 0,
    historyMode: historyMode ?? getHistoryMode(),
    sizeMm: render?.manifest?.size_mm,
  })
  baseline = { session, ids: new Set(panel.overrides.map(patchId)) }
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
  const all = panel.overrides.map((o) => ({ gid: o.gid, prop: o.prop, value: o.value }))
  // 只留这次手势新写 / 改写的；认不出基线（会话已换）时退回全部——宁可多还原，不漏还原
  const own = previewSession()?.id
  const mine =
    baseline && own != null && baseline.session === own
      ? all.filter((p) => !baseline!.ids.has(patchId(p)))
      : all
  baseline = null
  commitPreview(mine, renderKeyOf(panel))
}

export { cancelPreview as cancelElementPreview }
