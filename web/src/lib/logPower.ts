/**
 * 对数轴范围值的写法：10 的整数次幂写成上标形式（0.01 → 10⁻²、1 → 10⁰、1000 → 10³），别的数照旧。
 * 只给对数轴的范围值用（线性轴上写 10⁻² 反而难读）；纯函数，不碰文案与文档。
 */

const SUPERSCRIPT: Record<string, string> = { '-': '⁻', '0': '⁰', '1': '¹', '2': '²', '3': '³', '4': '⁴', '5': '⁵', '6': '⁶', '7': '⁷', '8': '⁸', '9': '⁹' }

/** 指数范围之外（1e-300 之类）不改写，免得写出一长串上标 */
const MAX_EXP = 99

/** 是 10 的整数次幂 → `10⁻²` 这样的写法；不是（含 ≤ 0、非有限数）→ `null` */
export function pow10Label(v: unknown): string | null {
  if (typeof v !== 'number' || !Number.isFinite(v) || v <= 0) return null
  const exp = Math.round(Math.log10(v))
  if (Math.abs(exp) > MAX_EXP || Math.abs(v / 10 ** exp - 1) > 1e-9) return null
  return `10${[...String(exp)].map((c) => SUPERSCRIPT[c]).join('')}`
}

/** 一根轴的范围值写成界面上的样子：对数轴上的 10 的整数次幂用上标写法，其余（线性轴、别的数）照旧 `String(v)` */
export function formatAxisLimit(v: number, log: boolean): string {
  return (log ? pow10Label(v) : null) ?? String(v)
}
