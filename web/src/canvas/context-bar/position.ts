import type { ManifestElement } from '@/lib/api'
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

/* ------------------------------ 避让 -------------------------------------- */

/**
 * 落位时不该被盖住的东西。
 *
 * `obstacles` 是别的文字元素在窗口里的矩形；`zone` 是锚点所在的整块区域
 * （图内元素 = 那张图的 SVG 容器）：贴着锚点的上下两档都盖到东西时，退到这块
 * 区域外面去——图的正上方 / 正下方通常是空白纸面，而且用户的视线不用离开
 * 这张图。
 */
export interface AvoidOptions {
  obstacles: readonly ScreenRect[]
  zone?: ScreenRect | null
}

const overlapArea = (a: ScreenRect, b: ScreenRect): number => {
  const w = Math.min(a.left + a.width, b.left + b.width) - Math.max(a.left, b.left)
  const h = Math.min(a.top + a.height, b.top + b.height) - Math.max(a.top, b.top)
  return w > 0 && h > 0 ? w * h : 0
}

/**
 * 与 `placeToolbar` 同一套边界规则，多一条：**不盖住别的文字**。
 *
 * 候选位按「离锚点近 → 远」试：锚点上方、锚点下方、区域上方、区域下方。
 * 第一个放得下（不进顶部安全区、不出窗口底边）且与任何障碍物零重叠的胜出；
 * 都有重叠就取重叠面积最小的；一个都放不下就退回 `placeToolbar` 的夹边逻辑
 * （宁可盖一点，不许出界）。
 *
 * 审计 T14 记的就是这条缺的那一半：选中图例项时工具条贴到它上方，正好压在
 * 图标题上（`21-legend-entry`）——旧规则只看顶部安全区，不看那里有什么。
 */
export function placeToolbarAvoiding(
  anchor: ScreenRect,
  size: { w: number; h: number },
  viewport: { width: number; height: number },
  insets: Insets,
  avoid: AvoidOptions,
): Placement {
  const minX = insets.left + MARGIN
  const maxX = viewport.width - insets.right - size.w - MARGIN
  const x = Math.max(minX, Math.min(anchor.left + anchor.width / 2 - size.w / 2, maxX))
  const maxY = viewport.height - size.h - MARGIN
  const zone = avoid.zone ?? null
  const candidates: { y: number; placement: Placement['placement'] }[] = [
    { y: anchor.top - size.h - MARGIN, placement: 'above' },
    { y: anchor.top + anchor.height + MARGIN, placement: 'below' },
    ...(zone
      ? [
          { y: zone.top - size.h - MARGIN, placement: 'above' as const },
          { y: zone.top + zone.height + MARGIN, placement: 'below' as const },
        ]
      : []),
  ]
  let best: (typeof candidates)[number] | null = null
  let bestArea = Number.POSITIVE_INFINITY
  for (const c of candidates) {
    if (c.y < TOP_SAFE || c.y > maxY) continue
    const rect: ScreenRect = { left: x, top: c.y, width: size.w, height: size.h }
    let area = 0
    for (const o of avoid.obstacles) area += overlapArea(rect, o)
    if (area === 0) return { x, y: c.y, placement: c.placement }
    if (area < bestArea) {
      bestArea = area
      best = c
    }
  }
  if (best) return { x, y: best.y, placement: best.placement }
  return placeToolbar(anchor, size, viewport, insets)
}

/**
 * 被盖住会妨碍阅读的角色：文字、图例、刻度、色条。曲线 / 填充 / 子图本身的
 * bbox 是一大块矩形，把它们也算作障碍物的话工具条在图里就无处可放。
 */
export const COVER_SENSITIVE_ROLES: ReadonlySet<string> = new Set([
  'title',
  'text',
  'axis_label',
  'legend_text',
  'legend',
  'ticks',
  'ticklabel',
  'colorbar',
])

/** manifest 的 bbox（占整图的分数，y 向下）→ 窗口矩形；`host` 是那张图的 SVG 容器 */
export function bboxToScreen(
  bbox: readonly [number, number, number, number] | readonly number[],
  host: ScreenRect,
): ScreenRect {
  const [bx, by, bw, bh] = bbox
  return {
    left: host.left + bx * host.width,
    top: host.top + by * host.height,
    width: bw * host.width,
    height: bh * host.height,
  }
}

/**
 * 图内元素工具条的障碍物：同一张图里**别的**文字类元素。锚点自己不算
 * （工具条贴着它放，本来就在它旁边），隐藏的不算（画布上看不见它）。
 */
export function elementObstacles<T extends Pick<ManifestElement, 'gid' | 'role' | 'bbox'>>(
  elements: readonly T[],
  host: ScreenRect,
  exceptGid: string | null,
  hidden: (el: T) => boolean = () => false,
): ScreenRect[] {
  return elements
    .filter((e) => e.gid !== exceptGid && COVER_SENSITIVE_ROLES.has(e.role) && !hidden(e))
    .map((e) => bboxToScreen(e.bbox, host))
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
