/**
 * coachmark 的落位——**纯函数**，与 `canvas/context-bar/position.ts` 同一种
 * 写法：输入锚点矩形、卡片尺寸、视口，输出左上角与箭头方向。jsdom 量不出
 * 布局，所以这里的每条规则都能在单测里用数字直接验。
 *
 * 规则：
 *   1. 首选放在锚点**下方**，水平与锚点左边对齐；
 *   2. 下方放不下 → 上方；上下都放不下 → 右侧；再不行 → 左侧；
 *   3. 四个方向都不够就取下方并**夹进视口**（宁可挡一点，不许出界）；
 *   4. 与视口边至少留 `margin`，与锚点之间留 `gap`。
 */
export interface Box {
  x: number
  y: number
  w: number
  h: number
}

export type CoachmarkSide = 'bottom' | 'top' | 'right' | 'left'

export interface CoachmarkPlacement {
  x: number
  y: number
  side: CoachmarkSide
}

export const COACHMARK_GAP = 10
export const COACHMARK_MARGIN = 8

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v))

export function placeCoachmark(
  anchor: Box,
  size: { w: number; h: number },
  viewport: { w: number; h: number },
  opts: { gap?: number; margin?: number } = {},
): CoachmarkPlacement {
  const gap = opts.gap ?? COACHMARK_GAP
  const margin = opts.margin ?? COACHMARK_MARGIN
  const maxX = Math.max(margin, viewport.w - size.w - margin)
  const maxY = Math.max(margin, viewport.h - size.h - margin)

  const fitsBelow = anchor.y + anchor.h + gap + size.h + margin <= viewport.h
  const fitsAbove = anchor.y - gap - size.h - margin >= 0
  const fitsRight = anchor.x + anchor.w + gap + size.w + margin <= viewport.w
  const fitsLeft = anchor.x - gap - size.w - margin >= 0

  if (fitsBelow) {
    return { x: clamp(anchor.x, margin, maxX), y: anchor.y + anchor.h + gap, side: 'bottom' }
  }
  if (fitsAbove) {
    return { x: clamp(anchor.x, margin, maxX), y: anchor.y - gap - size.h, side: 'top' }
  }
  if (fitsRight) {
    return { x: anchor.x + anchor.w + gap, y: clamp(anchor.y, margin, maxY), side: 'right' }
  }
  if (fitsLeft) {
    return { x: anchor.x - gap - size.w, y: clamp(anchor.y, margin, maxY), side: 'left' }
  }
  return {
    x: clamp(anchor.x, margin, maxX),
    y: clamp(anchor.y + anchor.h + gap, margin, maxY),
    side: 'bottom',
  }
}

/** 没有锚点的步骤（欢迎 / 完成）：卡片居中 */
export function placeCentered(
  size: { w: number; h: number },
  viewport: { w: number; h: number },
): { x: number; y: number } {
  return {
    x: Math.max(COACHMARK_MARGIN, (viewport.w - size.w) / 2),
    y: Math.max(COACHMARK_MARGIN, (viewport.h - size.h) / 2),
  }
}

/** 锚点是否（部分）在视口外——决定要不要先把它滚进来 */
export function offscreen(anchor: Box, viewport: { w: number; h: number }): boolean {
  return (
    anchor.x + anchor.w <= 0 ||
    anchor.y + anchor.h <= 0 ||
    anchor.x >= viewport.w ||
    anchor.y >= viewport.h
  )
}

/** `display: contents` 的锚点自身没有盒子：取子元素矩形的并集 */
export function unionBoxes(boxes: Box[]): Box | null {
  const real = boxes.filter((b) => b.w > 0 || b.h > 0)
  if (!real.length) return null
  const x0 = Math.min(...real.map((b) => b.x))
  const y0 = Math.min(...real.map((b) => b.y))
  const x1 = Math.max(...real.map((b) => b.x + b.w))
  const y1 = Math.max(...real.map((b) => b.y + b.h))
  return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 }
}

/**
 * 卡片从 `from` 挪到 `to` 该不该**滑过去**（left/top 过渡），还是直接出现在新位置。
 *
 * 滑行途中卡片是可点的：它扫过的地方，用户点下去点到的是卡片——锚点正是用户
 * 此刻要点的东西。第 1 步的卡片曾从屏幕外斜着飞进来、半路改道扫过素材卡中心，
 * 慢机器上双击的第二下落在飞过来的「跳过此步」上，教程被推到下一步、图却没打开
 * （issue #581）。所以：
 *   - 没有旧位置（刚挂载、还没在屏幕上出现过）→ 不滑，直接出现；
 *   - 扫过的区域（两框的外接矩形，比真实扫过的平行四边形只大不小）碰到锚点 → 不滑；
 *   - 其余照常滑。
 */
export function shouldGlide(from: Box | null, to: Box, anchor: Box | null): boolean {
  if (!from) return false
  if (!anchor) return true
  const x0 = Math.min(from.x, to.x)
  const y0 = Math.min(from.y, to.y)
  const x1 = Math.max(from.x + from.w, to.x + to.w)
  const y1 = Math.max(from.y + from.h, to.y + to.h)
  return !(x0 < anchor.x + anchor.w && anchor.x < x1 && y0 < anchor.y + anchor.h && anchor.y < y1)
}
