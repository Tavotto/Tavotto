import type { SVGProps } from 'react'

import { cn } from '@/lib/utils'
import { ICON_SIZE, useIconDefaults } from './Icon'

/**
 * 全产品唯一的「加载中」图形（`docs/rules/frontend/ui-visual-discipline.md` 图标节）：
 * 淡底环 + 一段约 100° 的短弧，整枚匀速转。旧的 `LoaderCircle` 是 3/4 弧、没有底环，
 * 描边只有 1–1.33px，转起来重心偏、看着又细又飘。
 *
 * 描边在 24 网格里取 2.5（16px 上 1.67px），小档位给下限 1.4px（12px 上 2.8 / 14px 上 2.4），
 * 这条下限只属于转圈，图标集其余图标的比例缩放不动。
 * 用 `currentColor`，颜色由调用方的 `text-*` 给；`size` 只写 `ICON_SIZE.*`。
 * 放进「图标 + 文字」行时包 `IconSlot`，不要用 margin 去凑对齐。
 * 减少动态效果时不转，静止的「环 + 弧」仍读得出是加载。
 */
const R = 9
const CIRC = 2 * Math.PI * R
const ARC = (100 / 360) * CIRC
const MIN_STROKE_PX = 1.4

export function Spinner({
  size,
  className,
  ...rest
}: Omit<SVGProps<SVGSVGElement>, 'ref' | 'strokeWidth'> & { size?: number }) {
  const defaults = useIconDefaults()
  const px = size ?? defaults.size ?? ICON_SIZE.sm
  const sw = Math.max(2.5, (MIN_STROKE_PX * 24) / px)
  const hasA11y = 'aria-label' in rest || 'aria-labelledby' in rest
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 24 24"
      width={px}
      height={px}
      fill="none"
      stroke="currentColor"
      strokeWidth={sw}
      strokeLinecap="round"
      className={cn('icon icon-spinner shrink-0 animate-spin motion-reduce:animate-none', className)}
      {...(hasA11y ? { role: 'img' } : { 'aria-hidden': true })}
      {...rest}
    >
      <circle cx={12} cy={12} r={R} opacity={0.25} />
      <circle cx={12} cy={12} r={R} strokeDasharray={`${ARC.toFixed(2)} ${CIRC.toFixed(2)}`} />
    </svg>
  )
}
