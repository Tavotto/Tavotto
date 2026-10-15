/**
 * 吸附反馈的构造（ADR 0128 §四）：吸上的线 + 自身所属框；目标是**别的图**里的子图 / 色条 / 图例等
 * 元素时再带上对方的框。纯函数，store 只存结果。
 */
import type { Rect, SnapLine } from '@/lib/geometry'
import type { SnapFeedback } from '@/store/interactionStore'

/** 这些出身的线不是「某个图的元素」，对方没有可框的元素 */
const NOT_AN_ELEMENT = new Set(['frame', 'figure', 'page', 'guide', 'grid'])

export function feedbackFor(
  axis: 'x' | 'y',
  line: SnapLine,
  self: Rect,
  ownPanelId: string,
): SnapFeedback {
  const foreign =
    !!line.panelId && line.panelId !== ownPanelId && !!line.rect && !NOT_AN_ELEMENT.has(line.kind)
  return foreign ? { axis, v: line.v, self, target: line.rect } : { axis, v: line.v, self }
}
