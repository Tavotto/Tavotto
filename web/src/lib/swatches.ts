import type { Manifest } from './api'

/**
 * 取色面板里的两组推荐色（ADR 0185 M3 第三批）：
 *
 * - **推荐配色**：期刊目录（`profiles/journals.json`、远程目录）里**没有任何期刊的配色规定**——只有 `info.color_mode`
 *   （RGB / CMYK）这一类字段，所以这里不按期刊给颜色、也不编一份「期刊推荐色」。一律给 Okabe-Ito 的八色
 *   （Okabe & Ito 2008，色盲友好、科研绘图里最常用的定性配色），面板的说明里写明所选期刊没有规定配色。
 *   将来目录里真有了配色字段，在 `recommendedSwatches` 这一处接上。
 * - **图内配色**：当前画布上各张图里**引擎报出来的**颜色属性（manifest 里 `type: 'color'` 的字段值、渐变色带两端），
 *   按出现次数排，去掉灰阶（黑白灰多半是坐标轴与文字，已在「排版里的颜色」里）。
 */

/** Okabe-Ito 八色（黑色放最后：它在科研图里多半是轴与文字，排在前面没用） */
export const OKABE_ITO: readonly string[] = [
  '#e69f00',
  '#56b4e9',
  '#009e73',
  '#f0e442',
  '#0072b2',
  '#d55e00',
  '#cc79a7',
  '#000000',
]

export interface RecommendedSwatches {
  /** 颜色从哪来：目前只有 `general`（期刊目录里没有配色规定） */
  source: 'general'
  colors: readonly string[]
  /** 当前排版绑定的期刊名（给说明用）；没有选期刊 = null */
  journal: string | null
}

export function recommendedSwatches(journalLabel: string | null | undefined): RecommendedSwatches {
  return { source: 'general', colors: OKABE_ITO, journal: journalLabel || null }
}

const HEX = /^#?([0-9a-fA-F]{6}|[0-9a-fA-F]{3})$/
function hex(v: unknown): string | null {
  if (typeof v !== 'string') return null
  const m = HEX.exec(v.trim())
  if (!m) return null
  const h = m[1].length === 3 ? m[1].replace(/./g, (c) => c + c) : m[1]
  return `#${h.toLowerCase()}`
}
const isGray = (c: string) => c.slice(1, 3) === c.slice(3, 5) && c.slice(3, 5) === c.slice(5, 7)

/** 图里用到的颜色（出现多的在前，去灰阶），最多 `max` 个 */
export function figureColorsOf(manifests: readonly (Manifest | null | undefined)[], max = 12): string[] {
  const counts = new Map<string, number>()
  const add = (v: unknown) => {
    const c = hex(v)
    if (c && !isGray(c)) counts.set(c, (counts.get(c) ?? 0) + 1)
  }
  for (const m of manifests) {
    for (const el of m?.elements ?? []) {
      for (const f of el.editable) {
        if (f.type === 'color') add(f.value)
        else if (f.type === 'gradient') {
          const stops = (f.value as { stops?: unknown[] } | null)?.stops
          if (Array.isArray(stops)) stops.forEach(add)
        }
      }
    }
  }
  return [...counts.entries()]
    .sort((a, b) => b[1] - a[1])
    .slice(0, max)
    .map(([c]) => c)
}
