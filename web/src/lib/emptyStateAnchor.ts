/**
 * 空画布起步提示落在哪（纯函数，便于测试）。
 *
 * 锚在**纸面可见部分**的中心：纸面整个在视野里时就是纸面中心（提示跟着纸面走，
 * 不飘在灰色工作区中央）；纸面被侧栏挤到一边、或用户放大到只剩一角时，取
 * 「纸面 ∩ 视口」那一块的中心——提示永远落在用户看得到的纸面上，而不是按整张
 * 纸的坐标算出一个视野之外的点（审计 B01：194% 下提示被挤到可视区右缘，
 * 主按钮半截出屏）。纸面完全不在视野里时退到视口中心。
 *
 * 最后再钳进视口：提示盒有固定的大致尺寸，中心离视口边缘至少留半个盒子，
 * 这样纸面只露出一条窄边时提示也整个可见。
 */
export interface EmptyAnchorInput {
  /** 视口尺寸（px） */
  viewW: number
  viewH: number
  /** 纸面在视口里的矩形（px，已含 pan / zoom） */
  paper: { x: number; y: number; w: number; h: number }
  /** 提示盒的半宽 / 半高（px）；用来钳进视口 */
  halfW?: number
  halfH?: number
}

/** 提示盒的大致外形：EmptyState 的 max-w 加左右留白 / 图标 + 三行字 + 一颗按钮 */
export const EMPTY_HINT_HALF_W = 150
export const EMPTY_HINT_HALF_H = 90

export function emptyStateAnchor({
  viewW,
  viewH,
  paper,
  halfW = EMPTY_HINT_HALF_W,
  halfH = EMPTY_HINT_HALF_H,
}: EmptyAnchorInput): { x: number; y: number } {
  const x0 = Math.max(paper.x, 0)
  const y0 = Math.max(paper.y, 0)
  const x1 = Math.min(paper.x + paper.w, viewW)
  const y1 = Math.min(paper.y + paper.h, viewH)
  const visible = x1 > x0 && y1 > y0
  let cx = visible ? (x0 + x1) / 2 : viewW / 2
  let cy = visible ? (y0 + y1) / 2 : viewH / 2
  // 视口比盒子还窄 / 矮时钳不了，就居中
  cx = viewW > halfW * 2 ? Math.min(Math.max(cx, halfW), viewW - halfW) : viewW / 2
  cy = viewH > halfH * 2 ? Math.min(Math.max(cy, halfH), viewH - halfH) : viewH / 2
  return { x: cx, y: cy }
}
