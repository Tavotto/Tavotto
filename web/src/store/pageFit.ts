import { useDocumentStore } from './documentStore'
import { useViewportStore } from './viewportStore'
import { useWorkspaceStore } from './workspace'

/**
 * 同一张画布的**页面尺寸**变了（换预设「单栏 / 双栏 / …」、手填宽高、横竖对调、
 * 样式预设带的页面、以及它们的撤销 / 重做）→ 视口按新页面补间适配一次。
 *
 * 改造前换了画布类型视口原地不动：从单栏换到海报，页面大半截跑出屏幕；反过来
 * 换小了，页面缩成角落里一小块（2026-09-28 用户反馈）。页面尺寸是用户对「这张
 * 画布是什么」的显式决定，按新页面重新取景就是这个决定的一部分，所以这里**不看**
 * 是否处在适应模式——平移 / 缩放过的视口在换了页面之后已经对不上新页面了。
 *
 * 只管「同一份文档、同一张画布上的页面变化」。另外三种页面变化各有自己的适配点，
 * 这里一律跳过，免得同一帧里抢着写视口：
 * - 切画布标签：`canvasSession.restore`（适应模式按画布各自记，不在这里覆盖）；
 * - 整体换文档 / 载入 / 切项目：`loadSeq` 或 `documentId` 变了，`afterSwitch` /
 *   `adoptOpenedProject` / 会话恢复各自 `fit`；
 * - 快速编辑：那一屏取景的是那张图的包围盒，不是页面——此刻不动视口；停放的排版视口
 *   记着停放时的页面尺寸，回排版时 `returnToLayout` 比一比就知道要不要按新页面取景。
 */
export function startPageSizeFit(): () => void {
  return useDocumentStore.subscribe((state, prev) => {
    const a = state.doc.page
    const b = prev.doc.page
    if (a.w === b.w && a.h === b.h) return
    if (state.activeCanvasId !== prev.activeCanvasId) return
    if (state.documentId !== prev.documentId || state.loadSeq !== prev.loadSeq) return
    if (useWorkspaceStore.getState().mode === 'fast_edit') return
    useViewportStore.getState().fitAnimated(a.w, a.h)
  })
}
