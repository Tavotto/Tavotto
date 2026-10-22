import type { PageEdge } from '@/lib/pageEdgeResize'

/** 文字两侧垫底（标尺同色）的留白（px） */
export const DIM_TEXT_PAD = 5
/** 线段要把文字放在里面，文字两侧各至少还要剩这么长的线（否则文字挪到线段外侧） */
export const DIM_MIN_LINE = 10
/** 文字放在线段外侧时，离线段端点的距离 */
export const DIM_OUTSIDE_GAP = 6

export interface DimSpan {
  /** 线段在标尺内的可见起止（标尺局部坐标，沿标尺长度方向；已夹进 [0, viewLen]） */
  a: number
  b: number
  /** 两端的短竖线只在对应页面边真的落在可见范围内时才画 */
  startCap: boolean
  endCap: boolean
  /** 文字中心（沿标尺长度方向）；线段放得下就是可见段中点，否则在被拖边一侧的线段外侧 */
  center: number
  outside: boolean
}

/**
 * 拖页面边时标尺里那条尺寸线的几何（纯函数，不看文字内容，只看文字长度的估计值，所以数字位数变不抖）。
 * `start` / `end` 是页面起止边在**这条标尺局部坐标**里的位置（px，start <= end，标尺局部坐标 = 舞台坐标）；
 * `viewLen` 是标尺可见长度；`textLen` 是文字（含垫底留白之外）沿标尺方向的长度估计。
 * - 页面边超出可视区：线段夹在 [0, viewLen]，文字居中于可见段；整段都看不见返回 null。
 * - 可见段放不下文字（textLen + 两侧留白 + 两侧最短线）：文字放到线段外侧、靠近被拖边的一侧；
 *   那一侧也放不下就换另一侧；都放不下才夹在标尺内。
 */
export function dimSpan(edge: PageEdge, start: number, end: number, viewLen: number, textLen: number): DimSpan | null {
  const a = Math.max(0, start)
  const b = Math.min(viewLen, end)
  if (!(b > a)) return null
  const startCap = start >= 0 && start <= viewLen
  const endCap = end >= 0 && end <= viewLen
  const half = textLen / 2 + DIM_TEXT_PAD
  if (b - a >= 2 * half + 2 * DIM_MIN_LINE) {
    return { a, b, startCap, endCap, center: (a + b) / 2, outside: false }
  }
  const before = a - DIM_OUTSIDE_GAP - half // 起点一侧（左 / 上）
  const after = b + DIM_OUTSIDE_GAP + half // 终点一侧（右 / 下）
  const fitsBefore = before - half >= 0
  const fitsAfter = after + half <= viewLen
  const preferBefore = edge === 'l' || edge === 't'
  let center: number
  if (preferBefore) center = fitsBefore ? before : fitsAfter ? after : before
  else center = fitsAfter ? after : fitsBefore ? before : after
  center = Math.max(half, Math.min(viewLen - half, center))
  return { a, b, startCap, endCap, center, outside: true }
}
