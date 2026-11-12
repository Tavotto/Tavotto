/**
 * 点阵进度条的几何（ADR 0168）：`ui/ProgressBar` 的唯一画法出处，纯函数，不碰 DOM。
 *
 * - 未完成 = 每 4×4 一个点，已完成 = 每 2×2 一个点：**点一样大、同一个颜色，只用疏密表现填充**（不用变黑）。
 * - 一切按**物理像素**算：点 = `dot` 个物理像素（最接近缩放倍数的整数），间距 = 点 × 4 / 点 × 2——与 Mac 2× 上
 *   「点 2、间距 8 / 4」同一个比例。这样 Windows 125% / 175% 也不会出现半个像素的点（CSS 遮罩在那两档会被抹灰）。
 * - 两段共用一张网格（列周期 / 行周期取两种间距的最小公倍数）：点阵只铺「点 + 整数个公共周期」那一块并居中，
 *   四条边都正好落在一行 / 一列点上，两段的顶行、底行同高；已完成 / 未完成的分界也吸附到公共列周期。
 */

/** 一种点阵：每 `step` 个点径放一个点（横竖同距、对齐排） */
export interface DotPattern {
  step: number
}

export const EMPTY_PATTERN: DotPattern = { step: 4 }
export const FILLED_PATTERN: DotPattern = { step: 2 }

/** 纵向固定两格（稀点 3 行、密点 5 行）：各屏幕行数一致，不随条高的取整多出一行 */
export const ROWS = 2

/** 不确定态那段密点占整条的比例（至少一个公共周期） */
export const INDETERMINATE_SPAN = 1 / 8

export interface DotLayout {
  /** 点的边长（物理像素） */
  dot: number
  /** 未完成 / 已完成的点间距（物理像素） */
  emptyStep: number
  filledStep: number
  /** 公共周期（物理像素）：一「格」 */
  period: number
  /** 点阵铺开的那块（物理像素）与它在位图里的左上角 */
  gridW: number
  gridH: number
  ox: number
  oy: number
  /** 一共几格（分界只落在格边上） */
  cells: number
}

const gcd = (a: number, b: number): number => (b ? gcd(b, a % b) : a)
const lcm = (a: number, b: number) => (a / gcd(a, b)) * b

/**
 * 位图 `devW × devH`（物理像素）、CSS 宽 `cssW` 时的布局。
 * `dotOverride` 留给按屏幕 ppi 选点径的那一层（ADR 0168 第二节）；不给就按实际倍率取整。
 */
export function dotLayout(devW: number, devH: number, cssW: number, dotOverride?: number): DotLayout {
  const scale = cssW > 0 ? devW / cssW : 1
  const dot = Math.max(1, Math.round(dotOverride ?? scale))
  const emptyStep = EMPTY_PATTERN.step * dot
  const filledStep = FILLED_PATTERN.step * dot
  const period = lcm(emptyStep, filledStep)
  // 横向至少一格；纵向至少一行点
  const cells = Math.max(1, Math.floor((devW - dot) / period))
  const rows = Math.max(0, Math.min(ROWS, Math.floor((devH - dot) / period)))
  const gridW = dot + cells * period
  const gridH = dot + rows * period
  return {
    dot,
    emptyStep,
    filledStep,
    period,
    gridW,
    gridH,
    ox: Math.max(0, Math.floor((devW - gridW) / 2)),
    oy: Math.max(0, Math.floor((devH - gridH) / 2)),
    cells,
  }
}

/** 已完成几格（0…cells）：pct 0–100，非有限数按 0 */
export function filledCells(layout: DotLayout, pct: number): number {
  const p = Number.isFinite(pct) ? Math.min(100, Math.max(0, pct)) : 0
  return Math.round((p / 100) * layout.cells)
}

/** 不确定态那段密点有几格 */
export function sweepCells(layout: DotLayout): number {
  return Math.min(layout.cells, Math.max(1, Math.round(layout.cells * INDETERMINATE_SPAN)))
}

/** 一个点（物理像素，左上角） */
export type Dot = readonly [number, number]

/**
 * 要画的全部点：格 [denseFrom, denseTo) 画密点，其余画稀点。
 * 一个点归属哪一段看它的左边落在哪一格；网格右边界那一列点（x = gridW − dot）跟着最后一格走。
 */
export function dotsFor(layout: DotLayout, denseFrom: number, denseTo: number): Dot[] {
  const { dot, period, gridW, gridH, ox, oy, emptyStep, filledStep, cells } = layout
  const out: Dot[] = []
  const cellOf = (x: number) => Math.min(cells - 1, Math.floor(x / period))
  const isDense = (x: number) => {
    const c = cellOf(x)
    return c >= denseFrom && c < denseTo
  }
  // 两种间距都整除公共周期，所以稀点是密点的子集：先按密点间距走，再决定这个位置画不画
  for (let y = 0; y + dot <= gridH; y += filledStep) {
    for (let x = 0; x + dot <= gridW; x += filledStep) {
      const dense = isDense(x)
      if (dense || (x % emptyStep === 0 && y % emptyStep === 0)) out.push([ox + x, oy + y])
    }
  }
  return out
}
