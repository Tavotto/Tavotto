import { forwardRef, useId, type ForwardRefExoticComponent, type RefAttributes, type SVGProps } from 'react'

import { cn } from '@/lib/utils'
import { useIconDefaults } from '../Icon'
import type { IconDef, IconPath, IconPathSpec, IconVariant } from './defs'

/**
 * 图标组件工厂：把 `defs.ts` 里的一条几何定义变成一个 React 组件。
 *
 * - `size` / `strokeWidth` 不写就拿 `IconProvider` 的默认档（没套 Provider 也是同一
 *   套默认，Provider 只是根上改默认的地方）；描边按比例缩放，24 网格里的 2 画到
 *   14px 上是 1.17px、16px 上 1.33px。
 * - `filled` = 选中 / 激活态，渲染 `sel` 那份实心孪生；没有孪生的图标忽略它。
 * - 挖空（`k`）用 `<mask>`：id 由 `useId` 给，同一页里放多少个都不会串。
 * - 无障碍与 lucide 一致：没有 `aria-label` / `aria-labelledby` / 子元素时自动
 *   `aria-hidden`，图标是装饰，名字由旁边的文字或按钮的可达名给。
 * - 类名 `icon icon-<kebab-name>`，用例按它认形状（`svg.icon-triangle-alert`）。
 */
export interface IconProps extends Omit<SVGProps<SVGSVGElement>, 'ref'> {
  /** 渲染边长（px），只写 `ICON_SIZE.*` */
  size?: number
  /** 24 网格里的描边宽度，只写 `ICON_STROKE.*` */
  strokeWidth?: number
  /** 选中 / 激活态：实心孪生 */
  filled?: boolean
}

export type IconComponent = ForwardRefExoticComponent<IconProps & RefAttributes<SVGSVGElement>>

const kebab = (name: string) =>
  name
    .replace(/([a-z0-9])([A-Z])/g, '$1-$2')
    .replace(/([A-Za-z])(\d)/g, '$1-$2')
    .toLowerCase()

const spec = (p: IconPathSpec): IconPath => (typeof p === 'string' ? { d: p } : p)

function strokePath(p: IconPathSpec, i: number) {
  const o = spec(p)
  if (o.w === 0) return <path key={`s${i}`} d={o.d} fill="currentColor" stroke="none" />
  return <path key={`s${i}`} d={o.d} fill="none" strokeWidth={o.w} strokeDasharray={o.dash} />
}
function solidPath(p: IconPathSpec, i: number) {
  const o = spec(p)
  if (o.w === 0) return <path key={`f${i}`} d={o.d} fill="currentColor" stroke="none" />
  return <path key={`f${i}`} d={o.d} fill="currentColor" stroke="currentColor" strokeWidth={o.w} />
}
function cutPath(p: IconPathSpec, i: number) {
  const o = spec(p)
  if (o.w === 0) return <path key={`k${i}`} d={o.d} fill="#000" stroke="none" />
  return <path key={`k${i}`} d={o.d} fill={o.solid ? '#000' : 'none'} stroke="#000" strokeWidth={o.w} />
}
function overPath(p: IconPathSpec, i: number) {
  const o = spec(p)
  if (o.solid) return <path key={`o${i}`} d={o.d} fill="currentColor" stroke="currentColor" />
  return <path key={`o${i}`} d={o.d} fill="none" strokeWidth={o.w} />
}

function variantOf(def: IconDef, filled: boolean | undefined): IconVariant {
  return filled && def.sel ? def.sel : def
}

export function createIcon(name: string, def: IconDef): IconComponent {
  const shapeClass = `icon-${kebab(name)}`
  const Component = forwardRef<SVGSVGElement, IconProps>(function Icon(
    { size, strokeWidth, filled, className, children, ...rest },
    ref,
  ) {
    const defaults = useIconDefaults()
    const reactId = useId()
    const v = variantOf(def, filled)
    const px = size ?? defaults.size
    const sw = strokeWidth ?? defaults.strokeWidth
    const hasA11y = 'aria-label' in rest || 'aria-labelledby' in rest || !!children
    const maskId = v.k && v.k.length ? `icon-mask-${reactId.replace(/[^A-Za-z0-9_-]/g, '')}` : undefined
    return (
      <svg
        ref={ref}
        xmlns="http://www.w3.org/2000/svg"
        viewBox="0 0 24 24"
        width={px}
        height={px}
        fill="none"
        stroke="currentColor"
        strokeWidth={sw}
        strokeLinecap="round"
        strokeLinejoin="round"
        className={cn('icon', shapeClass, defaults.className, className)}
        {...(hasA11y ? {} : { 'aria-hidden': true })}
        {...rest}
      >
        {maskId && (
          <mask id={maskId} maskUnits="userSpaceOnUse" x={0} y={0} width={24} height={24}>
            <rect width={24} height={24} fill="#fff" />
            {v.k!.map(cutPath)}
          </mask>
        )}
        <g mask={maskId ? `url(#${maskId})` : undefined}>
          {v.s?.map(strokePath)}
          {v.f?.map(solidPath)}
        </g>
        {v.o?.map(overPath)}
        {children}
      </svg>
    )
  })
  Component.displayName = name
  return Component
}
