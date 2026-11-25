/**
 * 推荐排序（ADR 0210 §五）：哪套方案配得上这张画布此刻的这几组。
 *
 * 硬约束：色数够（每个数字槽都有颜色）；四种视觉（正常 + 红弱 / 绿弱 / 蓝黄弱）下任意两组的 CAM02-UCS
 * 色差 ≥ `MIN_DELTA_E`。过了的排前，组内按四种视觉下的最小色差从大到小；没过的排后，同样按最小色差。
 * 只有一组或没有组时谈不上撞色，全都算过，保持目录顺序（默认方案在前）。
 */
import { DEFAULT_SCHEME, PALETTES, neutralOf, type CatalogPalette } from './catalog'
import { closestPair, contrastRatio, type Vision } from './cvd'
import type { ColorSlot } from './colorKeys'

/** 「分得开」的门槛：CAM02-UCS 里 10 左右是并排的两条细线一眼能分开的距离 */
export const MIN_DELTA_E = 10
/** 细线、小点对白底的最低对比度（WCAG 的非文字图形 3:1） */
export const MIN_CONTRAST = 3

/** 某个槽在某套方案下的颜色；数字槽超出方案的色数就是 null（色数不够，不循环复用） */
export function colorForSlot(slot: ColorSlot, palette: CatalogPalette): string | null {
  if (slot === 'neutral') return neutralOf(palette)
  return palette.colors[slot] ?? null
}

export interface RankedPalette {
  palette: CatalogPalette
  /** 与传入的 slots 一一对应 */
  colors: (string | null)[]
  /** 色数不够：有槽拿不到颜色 */
  tooFew: boolean
  /** 四种视觉下最近的那两组（下标指向 slots）；少于两组时是 null */
  closest: { i: number; j: number; vision: Vision; deltaE: number } | null
  /** 过了硬约束 */
  fits: boolean
  /** 对白底不到 3:1 的颜色有几个（细线、小点要提示） */
  lowContrast: number
}

export function evaluate(palette: CatalogPalette, slots: readonly ColorSlot[]): RankedPalette {
  const colors = slots.map((s) => colorForSlot(s, palette))
  const tooFew = colors.some((c) => c === null)
  const present = colors.map((c) => c ?? '')
  const closest = tooFew ? null : closestPair(present)
  const fits = !tooFew && (closest === null || closest.deltaE >= MIN_DELTA_E)
  const lowContrast = colors.filter((c) => c !== null && contrastRatio(c) < MIN_CONTRAST).length
  return { palette, colors, tooFew, closest, fits, lowContrast }
}

const score = (r: RankedPalette): number =>
  r.tooFew ? -1 : r.closest === null ? Number.POSITIVE_INFINITY : r.closest.deltaE

/** 全目录排一遍（`Array.prototype.sort` 是稳定的：分数相同保持目录顺序） */
export function rankPalettes(
  slots: readonly ColorSlot[],
  palettes: readonly CatalogPalette[] = PALETTES,
): RankedPalette[] {
  return palettes
    .map((p) => evaluate(p, slots))
    .sort((a, b) => Number(b.fits) - Number(a.fits) || score(b) - score(a))
}

/**
 * 一张画布第一次建色键表时用哪套：默认方案过得了硬约束就用它（ADR 0210 §八，可改），
 * 过不了就用排第一的。
 */
export function initialScheme(
  slots: readonly ColorSlot[],
  palettes: readonly CatalogPalette[] = PALETTES,
): string {
  const def = palettes.find((p) => p.id === DEFAULT_SCHEME)
  if (def && evaluate(def, slots).fits) return def.id
  return rankPalettes(slots, palettes)[0]?.palette.id ?? DEFAULT_SCHEME
}
