import type { ColormapFacts } from '@/lib/api'

import catalog from '../../../../../src/tavotto/resources/color_palettes.json'

/** Inspector thumbnails sample the same palette catalog as native adapters. */
export const COLORMAP_STOPS: Record<string, string[]> = Object.fromEntries(
  Object.entries(catalog.palettes).map(([name, packed]) => [
    name,
    Array.from({ length: 9 }, (_, index) => {
      const offset = 6 * Math.min(Math.floor(index * catalog.size / 8), catalog.size - 1)
      return `#${packed.slice(offset, offset + 6)}`
    }),
  ]),
)

/**
 * 一串色标 → CSS 渐变。离散色图一格一色、格与格之间是硬边（`ListedColormap`
 * 手写的三五个色块本来就没有过渡）；连续的按均匀停靠点平滑过渡。
 */
export function gradientOfStops(
  stops: readonly string[],
  discrete = false,
  dir = 'to right',
  boundaries?: readonly number[],
): string | null {
  if (!stops.length) return null
  if (stops.length === 1) return `linear-gradient(${dir}, ${stops[0]}, ${stops[0]})`
  if (!discrete) return `linear-gradient(${dir}, ${stops.join(', ')})`
  const n = stops.length
  const edges = boundaries?.length === n + 1 && boundaries.every(
    (value, i) => Number.isFinite(value) && value >= 0 && value <= 1 &&
      (i === 0 || value > boundaries[i - 1]),
  ) ? boundaries : undefined
  const pct = (i: number) => `${(100 * (edges?.[i] ?? i / n)).toFixed(2)}%`
  const bands = stops.map((c, i) => `${c} ${pct(i)}, ${c} ${pct(i + 1)}`)
  return `linear-gradient(${dir}, ${bands.join(', ')})`
}

/**
 * CSS 渐变。**引擎发来的事实优先**（`cmap_current` / `cmap_original`：白名单
 * 之外的色图只有它说得出长什么样），其次查离线表；两处都没有（老引擎 + 脚本
 * 自定义）返回 null → 调用方回落到名称显示。
 *
 * 事实只对得上名字才作数：override 刚写下、渲染还没回来的那一拍，`name` 已经是
 * 新的而 manifest 里的事实还是上一张的，照用就是把上一张的色标画到新名字头上。
 */
export function colormapGradient(
  name: string,
  facts?: ColormapFacts | null,
  dir = 'to right',
): string | null {
  if (facts?.name === name && facts.stops.length) {
    return gradientOfStops(facts.stops, facts.discrete, dir, facts.boundaries)
  }
  const stops = COLORMAP_STOPS[name]
  if (!stops) return null
  return gradientOfStops(stops, false, dir)
}
