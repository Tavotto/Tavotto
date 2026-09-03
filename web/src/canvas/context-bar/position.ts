import type { Rect } from '@/lib/geometry'
import { RAIL_W, type WorkspaceLayout } from '@/store/uiStore'
import { mmToPx, mmToViewX, mmToViewY, type ViewTransform } from '@/store/viewportStore'

/**
 * 浮动工具条的落位：纯函数，单选 / 图内元素 / 多选三种工具条共用一份。
 *
 * 抽成纯函数是为了能在没有真实布局的 jsdom 里把「上方放不下就放下方」「左右不越界」
 * 「避让停靠的侧栏」逐条量到；组件那边只负责把锚点、尺寸、视口喂进来。
 */

export const MARGIN = 8
/** 顶栏 + 标签条的高度：工具条不该盖到它们上面 */
export const TOP_SAFE = 76
/**
 * 完整多选栏（计数 + 参照 + 六向对齐 + 分布 / 尺寸 + 成组 + 更多）需要的最小可用宽度；
 * 侧栏之间比它窄时压缩成「对齐 / 分布 / 尺寸」三个弹层入口，不让它越界或压住侧栏
 */
export const FULL_BAR_MIN_WIDTH = 600

export interface ScreenRect {
  left: number
  top: number
  width: number
  height: number
}

export interface Insets {
  left: number
  right: number
}

export interface Placement {
  x: number
  y: number
  placement: 'above' | 'below'
}

export type BarVariant = 'full' | 'compact'

/** 停靠布局下侧栏占掉的窗口宽度；narrow 断点的侧栏是覆盖层，工具条那时整个让位 */
export function sidebarInsets(ui: {
  layout: WorkspaceLayout
  leftOpen: boolean
  leftWidth: number
  rightOpen: boolean
  rightWidth: number
}): Insets {
  const docked = ui.layout !== 'narrow'
  return {
    left: docked && ui.leftOpen ? RAIL_W + ui.leftWidth : 0,
    right: docked && ui.rightOpen ? ui.rightWidth : 0,
  }
}

/**
 * 锚点（窗口坐标）→ 工具条左上角。
 * 水平居中于锚点、夹在两侧侧栏之间（放不下时贴左）；默认贴在锚点上方，
 * 顶部安全区放不下就翻到下方（再不够就贴窗口底边）。
 */
export function placeToolbar(
  anchor: ScreenRect,
  size: { w: number; h: number },
  viewport: { width: number; height: number },
  insets: Insets,
): Placement {
  const minX = insets.left + MARGIN
  const maxX = viewport.width - insets.right - size.w - MARGIN
  const x = Math.max(minX, Math.min(anchor.left + anchor.width / 2 - size.w / 2, maxX))
  let y = anchor.top - size.h - MARGIN
  let placement: Placement['placement'] = 'above'
  if (y < TOP_SAFE) {
    y = Math.min(anchor.top + anchor.height + MARGIN, viewport.height - size.h - MARGIN)
    placement = 'below'
  }
  return { x, y, placement }
}

export const freeWidthOf = (viewportWidth: number, insets: Insets): number =>
  viewportWidth - insets.left - insets.right

export const barVariant = (freeWidth: number): BarVariant =>
  freeWidth >= FULL_BAR_MIN_WIDTH ? 'full' : 'compact'

/**
 * 联合选区（mm）→ 窗口坐标。**与 `OverlaySvg` 画联合框用的是同一份换算**
 * （`mmToViewX/Y` + `mmToPx`，再加视口在窗口里的原点）——不是量 DOM 再套另一套
 * 公式，缩放 / 平移 / 侧栏变化后两者永远重合。
 */
export function selectionScreenRect(bounds: Rect, t: ViewTransform): ScreenRect {
  return {
    left: t.originX + mmToViewX(bounds.x, t),
    top: t.originY + mmToViewY(bounds.y, t),
    width: mmToPx(bounds.w, t),
    height: mmToPx(bounds.h, t),
  }
}
