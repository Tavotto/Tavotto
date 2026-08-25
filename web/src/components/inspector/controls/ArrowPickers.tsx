import { t as translate } from '@/i18n'
import type { ArrowHeadType } from '@/types/document'
import { optionLabel } from '../roles/registry'
import { OptionGrid, type GridOption } from './OptionGrid'

/**
 * 箭头选择器（两个数据域，同一种视觉语言）：
 *
 *  - `ArrowStylePicker`：matplotlib 的 arrowstyle enum（"-" / "->" / "-|>" /
 *    "simple" …），整条样式一次选定；"custom" = 脚本自定义写法，选它 = 不动。
 *  - `ArrowHeadPicker`：画布箭头的端型（none / triangle / open / bar），
 *    起点与终点各一份。
 *
 * 写入值都是各自的原始 enum；预览只是视觉近似，不承诺像素等价。
 */

const S = { stroke: 'currentColor', strokeWidth: 1.4, fill: 'none' } as const
const F = { fill: 'currentColor' } as const

/** 端型图形：终点画在右侧（x≈30），起点镜像 */
function headShape(kind: string, atEnd: boolean): React.ReactNode {
  const x = atEnd ? 30 : 4
  const dir = atEnd ? -1 : 1
  switch (kind) {
    case 'triangle': // 实心三角
      return <path d={`M${x} 5 L${x + dir * 6} 1.8 L${x + dir * 6} 8.2 Z`} {...F} />
    case 'open': // 开放式（两撇）
      return <path d={`M${x + dir * 6} 1.5 L${x} 5 L${x + dir * 6} 8.5`} {...S} />
    case 'bar': // 竖线
      return <path d={`M${x} 1.5 L${x} 8.5`} {...S} />
    default:
      return null
  }
}

function ArrowPreview({ start, end, line = 'solid' }: { start: string; end: string; line?: 'solid' | 'none' }) {
  return (
    <svg width="34" height="10" viewBox="0 0 34 10" aria-hidden className="shrink-0">
      {line !== 'none' && <line x1="4" y1="5" x2="30" y2="5" {...S} />}
      {headShape(start, false)}
      {headShape(end, true)}
    </svg>
  )
}

/** matplotlib arrowstyle → 预览用的两端形态（视觉近似） */
const ARROWSTYLE_SHAPE: Record<string, { start: string; end: string }> = {
  '-': { start: 'none', end: 'none' },
  '->': { start: 'none', end: 'open' },
  '-|>': { start: 'none', end: 'triangle' },
  '<-': { start: 'open', end: 'none' },
  '<|-': { start: 'triangle', end: 'none' },
  '<->': { start: 'open', end: 'open' },
  '<|-|>': { start: 'triangle', end: 'triangle' },
  '|-|': { start: 'bar', end: 'bar' },
  ']-[': { start: 'bar', end: 'bar' },
  simple: { start: 'none', end: 'triangle' },
  fancy: { start: 'none', end: 'triangle' },
  wedge: { start: 'none', end: 'triangle' },
}

export function ArrowStylePicker({
  value,
  options,
  onChange,
  ariaLabel,
}: {
  value: string
  options: string[]
  onChange: (v: string) => void
  ariaLabel: string
}) {
  const all = options.includes(value) ? options : [value, ...options]
  const grid: GridOption[] = all.map((o) => {
    const shape = ARROWSTYLE_SHAPE[o]
    return {
      value: o,
      label: optionLabel('arrowstyle', o),
      code: o,
      preview: shape ? (
        <ArrowPreview start={shape.start} end={shape.end} />
      ) : (
        <span aria-hidden className="max-w-12 truncate font-mono text-xs">{o}</span>
      ),
    }
  })
  return (
    <OptionGrid value={value} options={grid} onChange={onChange} columns={3} ariaLabel={ariaLabel} />
  )
}

const HEAD_KINDS: ArrowHeadType[] = ['none', 'triangle', 'open', 'bar']

/**
 * 画布箭头端型。`at` 决定预览画在起点侧还是终点侧——
 * 「───▶」和「◀───」不该长一个样。
 */
export function ArrowHeadPicker({
  value,
  at,
  onChange,
  ariaLabel,
}: {
  value: ArrowHeadType | null
  at: 'start' | 'end'
  onChange: (v: ArrowHeadType) => void
  ariaLabel: string
}) {
  const grid: GridOption<ArrowHeadType>[] = HEAD_KINDS.map((kind) => ({
    value: kind,
    label: translate(`stroke.head.${kind}`, { ns: 'inspector' }),
    preview: (
      <ArrowPreview
        start={at === 'start' ? kind : 'none'}
        end={at === 'end' ? kind : 'none'}
      />
    ),
  }))
  return (
    <OptionGrid value={value} options={grid} onChange={onChange} columns={4} ariaLabel={ariaLabel} />
  )
}
