import type { CSSProperties } from 'react'
import type { ColormapFacts } from '@/lib/api'
import { optionLabel } from '../roles/registry'
import { colormapGradient, gradientOfStops } from './colormapStops'
import { OptionGrid, type GridOption } from './OptionGrid'

/**
 * 色条的方向与两端延伸：**用当前色图画的小色条**做选项预览（审计 T23），
 * 不再是两个文字下拉。写入值仍是 matplotlib 的 enum（vertical / horizontal，
 * neither / min / max / both）；manifest 没给的档不渲染。
 *
 * 预览是 `div` + CSS 渐变 + `clip-path`（延伸三角），不是 SVG：色图的渐变
 * 停靠点本来就是给 CSS 用的（`colormapStops`）；白名单之外的色图（脚本自定义、
 * `Blues` 这类没进离线表的）由引擎随 manifest 发来色标（`cmap_current`），两处
 * 都没有才退回一条灰阶——预览的形状仍然对，只是颜色不认识。
 */

export type Orientation = 'vertical' | 'horizontal'
export type Extend = 'neither' | 'min' | 'max' | 'both'

/** 延伸三角的裁剪形状；矩形本体占 60%–80%，三角各占一端 20% */
const CLIP: Record<Orientation, Record<Extend, string | undefined>> = {
  vertical: {
    neither: undefined,
    // vmin 在下端：下端出三角
    min: 'polygon(0 0, 100% 0, 100% 80%, 50% 100%, 0 80%)',
    max: 'polygon(50% 0, 100% 20%, 100% 100%, 0 100%, 0 20%)',
    both: 'polygon(50% 0, 100% 20%, 100% 80%, 50% 100%, 0 80%, 0 20%)',
  },
  horizontal: {
    neither: undefined,
    // vmin 在左端
    min: 'polygon(0 50%, 20% 0, 100% 0, 100% 100%, 20% 100%)',
    max: 'polygon(0 0, 80% 0, 100% 50%, 80% 100%, 0 100%)',
    both: 'polygon(0 50%, 20% 0, 80% 0, 100% 50%, 80% 100%, 20% 100%)',
  },
}

const FALLBACK_STOPS = ['#d9d9d9', '#404040']
const REVERSED_EXTEND: Record<Extend, Extend> = { min: 'max', max: 'min', neither: 'neither', both: 'both' }

/** 事实里的色标从 vmin → vmax，预览按绘制方向和实际端点排列。 */
export function colorbarPreviewStyle(
  cmap: string,
  orientation: Orientation,
  extend: Extend,
  facts?: ColormapFacts | null,
  reversed = false,
): CSSProperties {
  const directions = orientation === 'vertical' ? ['to top', 'to bottom'] : ['to right', 'to left']
  const dir = directions[Number(reversed)]
  const tip = reversed ? REVERSED_EXTEND[extend] : extend
  return {
    background: colormapGradient(cmap, facts, dir) ?? gradientOfStops(FALLBACK_STOPS, false, dir)!,
    clipPath: CLIP[orientation][tip],
  }
}

function BarPreview({
  cmap,
  facts,
  orientation,
  extend,
  reversed,
}: {
  cmap: string
  facts?: ColormapFacts | null
  orientation: Orientation
  extend: Extend
  reversed?: boolean
}) {
  const vertical = orientation === 'vertical'
  return (
    <span
      aria-hidden
      data-cb-preview={`${orientation}:${extend}`}
      className={vertical ? 'block h-5 w-2.5' : 'block h-2.5 w-5'}
      style={colorbarPreviewStyle(cmap, orientation, extend, facts, reversed)}
    />
  )
}

const asOrientation = (v: string): Orientation => (v === 'horizontal' ? 'horizontal' : 'vertical')
const asExtend = (v: string): Extend => (v === 'min' || v === 'max' || v === 'both' ? v : 'neither')

export function ColorbarOrientationPicker({
  value,
  options,
  onChange,
  ariaLabel,
  cmap,
  cmapFacts,
  reversed,
}: {
  value: string | null
  options: string[]
  onChange: (v: string) => void
  ariaLabel: string
  /** 当前色图名：预览用它的渐变 */
  cmap: string
  /** 当前色图不在离线表里时它长什么样（同一元素 `cmap` 字段的 `cmap_current`） */
  cmapFacts?: ColormapFacts | null
  reversed?: boolean
}) {
  const items: GridOption[] = options.map((v) => ({
    value: v,
    label: optionLabel('orientation', v),
    preview: (
      <span className="flex items-center gap-1.5 px-1">
        <BarPreview cmap={cmap} facts={cmapFacts} orientation={asOrientation(v)} extend="neither" reversed={reversed} />
        <span className="text-xs">{optionLabel('orientation', v)}</span>
      </span>
    ),
  }))
  return (
    <OptionGrid
      value={value}
      options={items}
      onChange={onChange}
      columns={Math.max(1, Math.min(2, items.length))}
      ariaLabel={ariaLabel}
    />
  )
}

export function ColorbarExtendPicker({
  value,
  options,
  onChange,
  ariaLabel,
  cmap,
  cmapFacts,
  orientation,
  reversed,
}: {
  value: string | null
  options: string[]
  onChange: (v: string) => void
  ariaLabel: string
  cmap: string
  cmapFacts?: ColormapFacts | null
  /**
   * 色条此刻的方向：预览要跟它画成一个样子。收 string 而不是收窄类型——
   * **多宿主色条不宣称 `orientation`**（引擎的 guard，issue #69），那时调用方
   * 给的是从 bbox 反推的值，认不出来就按竖直画（matplotlib 的默认）。
   */
  orientation: string
  reversed?: boolean
}) {
  const dir = asOrientation(orientation)
  const items: GridOption[] = options.map((v) => ({
    value: v,
    label: optionLabel('extend', v),
    preview: (
      <span className="flex flex-col items-center gap-0.5 px-0.5">
        <BarPreview cmap={cmap} facts={cmapFacts} orientation={dir} extend={asExtend(v)} reversed={reversed} />
        <span className="text-xs leading-3">{optionLabel('extend', v)}</span>
      </span>
    ),
  }))
  return (
    <OptionGrid
      value={value}
      options={items}
      onChange={onChange}
      columns={Math.max(1, Math.min(4, items.length))}
      ariaLabel={ariaLabel}
      cellClassName="h-11"
    />
  )
}
