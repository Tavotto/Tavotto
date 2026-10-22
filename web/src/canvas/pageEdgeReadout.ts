import type { PageEdge } from '@/lib/pageEdgeResize'

/** 读数盒的固定宽 / 高（px）：宽按「高 999.9 mm」英文最长一档留足，数字用 tabular-nums，盒子本身不随数值变宽 */
export const READOUT_W = 112
export const READOUT_H = 20
/** 读数离拖动边（画布内侧）与舞台边缘的距离 */
export const READOUT_INSET = 8

export interface ReadoutBox {
  /** 盒子左上角（舞台坐标，已按舞台尺寸夹取） */
  left: number
  top: number
  /** 盒内文字对齐：贴着边的那一侧 */
  align: 'left' | 'right' | 'center'
}

/**
 * 拖页面边时的读数位置：**锚在被拖那条边的画布内侧**（页面里面，不在页面外的标尺一侧）——
 * 左 / 右边贴着边、垂直居中；上 / 下边贴着边、水平居中。盒子宽高固定，位置只由边的屏幕位置决定，
 * 数值位数怎么变都不动；页面比舞台大时夹在舞台内（`READOUT_INSET`），因此永远不会压到舞台外的标尺。
 * `page` 是页面在舞台里的矩形（左上角 + 宽高，px）。
 */
export function readoutBox(
  edge: PageEdge,
  page: { left: number; top: number; w: number; h: number },
  view: { w: number; h: number },
): ReadoutBox {
  const maxLeft = view.w - READOUT_W - READOUT_INSET
  const maxTop = view.h - READOUT_H - READOUT_INSET
  const cl = (v: number, hi: number) => Math.max(READOUT_INSET, Math.min(hi, v))
  const midLeft = page.left + page.w / 2 - READOUT_W / 2
  const midTop = page.top + page.h / 2 - READOUT_H / 2
  switch (edge) {
    case 'l':
      return { left: cl(page.left + READOUT_INSET, maxLeft), top: cl(midTop, maxTop), align: 'left' }
    case 'r':
      return { left: cl(page.left + page.w - READOUT_INSET - READOUT_W, maxLeft), top: cl(midTop, maxTop), align: 'right' }
    case 't':
      return { left: cl(midLeft, maxLeft), top: cl(page.top + READOUT_INSET, maxTop), align: 'center' }
    case 'b':
      return { left: cl(midLeft, maxLeft), top: cl(page.top + page.h - READOUT_INSET - READOUT_H, maxTop), align: 'center' }
  }
}
