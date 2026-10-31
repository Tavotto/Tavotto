import {
  forwardRef,
  useId,
  type CSSProperties,
  type ForwardRefExoticComponent,
  type RefAttributes,
  type SVGProps,
} from 'react'

import { cn } from '@/lib/utils'
import { useIconDefaults } from '../Icon'
import type { IconDef, IconPath, IconPathSpec, IconVariant } from './defs'
import './motion.css'
import { ICON_MOTION, motionRoles, type IconMotion, type MotionRole } from './motion'

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
 * - 微动效（ADR 0154）：`motion.ts` 里登记了的图标带 `data-motion=<触发>`、各路径带 `data-m=<角色>`，
 *   关键帧在 `motion.css`；`playIn` = 挂载时播一次（状态图标刚出现的那一下）。
 */
export interface IconProps extends Omit<SVGProps<SVGSVGElement>, 'ref'> {
  /** 渲染边长（px），只写 `ICON_SIZE.*` */
  size?: number
  /** 24 网格里的描边宽度，只写 `ICON_STROKE.*` */
  strokeWidth?: number
  /** 选中 / 激活态：实心孪生 */
  filled?: boolean
  /** 挂载时播一次这枚图标的动作（只对 `motion.ts` 登记了的图标有效） */
  playIn?: boolean
}

export type IconComponent = ForwardRefExoticComponent<IconProps & RefAttributes<SVGSVGElement>>

const kebab = (name: string) =>
  name
    .replace(/([a-z0-9])([A-Z])/g, '$1-$2')
    .replace(/([A-Za-z])(\d)/g, '$1-$2')
    .toLowerCase()

const spec = (p: IconPathSpec): IconPath => (typeof p === 'string' ? { d: p } : p)

type Role = { role: MotionRole | null; k: number } | undefined

// 动作角色贴在路径上：`d` / `e`（一笔画出）要 pathLength=1 才能按比例走描边进度，`t`（依次动）带错开下标
function motionAttrs(r: Role) {
  if (!r?.role) return {}
  return {
    'data-m': r.role,
    ...(r.role === 'd' || r.role === 'e' ? { pathLength: 1 } : {}),
    ...(r.role === 't' ? { style: { '--k': r.k } as CSSProperties } : {}),
  }
}

function strokePath(p: IconPathSpec, i: number, r?: Role) {
  const o = spec(p)
  if (o.w === 0) return <path key={`s${i}`} d={o.d} fill="currentColor" stroke="none" {...motionAttrs(r)} />
  return (
    <path
      key={`s${i}`}
      d={o.d}
      fill="none"
      strokeWidth={o.w}
      strokeDasharray={o.dash}
      strokeOpacity={o.ghost ? 0 : undefined}
      {...motionAttrs(r)}
    />
  )
}
function solidPath(p: IconPathSpec, i: number, r?: Role) {
  const o = spec(p)
  if (o.w === 0) return <path key={`f${i}`} d={o.d} fill="currentColor" stroke="none" {...motionAttrs(r)} />
  const ghost = o.ghost ? { fillOpacity: 0, strokeOpacity: 0 } : {}
  return <path key={`f${i}`} d={o.d} fill="currentColor" stroke="currentColor" strokeWidth={o.w} {...ghost} {...motionAttrs(r)} />
}
function cutPath(p: IconPathSpec, i: number, r?: Role) {
  const o = spec(p)
  if (o.w === 0) return <path key={`k${i}`} d={o.d} fill="#000" stroke="none" {...motionAttrs(r)} />
  return (
    <path
      key={`k${i}`}
      d={o.d}
      fill={o.solid ? '#000' : 'none'}
      stroke="#000"
      strokeWidth={o.w}
      strokeOpacity={o.ghost ? 0 : undefined}
      {...motionAttrs(r)}
    />
  )
}
function overPath(p: IconPathSpec, i: number, r?: Role) {
  const o = spec(p)
  if (o.solid) return <path key={`o${i}`} d={o.d} fill="currentColor" stroke="currentColor" {...motionAttrs(r)} />
  return <path key={`o${i}`} d={o.d} fill="none" strokeWidth={o.w} {...motionAttrs(r)} />
}

function variantOf(def: IconDef, filled: boolean | undefined): IconVariant {
  return filled && def.sel ? def.sel : def
}

export function createIcon(name: string, def: IconDef): IconComponent {
  const shapeClass = `icon-${kebab(name)}`
  const motion: IconMotion | undefined = (ICON_MOTION as Record<string, IconMotion | undefined>)[name]
  const rolesPlain = motionRoles(motion, def, false)
  const rolesSel = def.sel ? motionRoles(motion, def.sel, true) : null
  const Component = forwardRef<SVGSVGElement, IconProps>(function Icon(
    { size, strokeWidth, filled, playIn, className, children, style, ...rest },
    ref,
  ) {
    const defaults = useIconDefaults()
    const reactId = useId()
    const v = variantOf(def, filled)
    const roles = v === def ? rolesPlain : rolesSel
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
        // 图标集出的图标都带这个标记：微动效的委托按它认图标（ADR 0154），不认 class
        data-icon=""
        {...(hasA11y ? {} : { 'aria-hidden': true })}
        {...(roles && motion
          ? {
              'data-motion': motion.on,
              ...(playIn ? { 'data-play': '' } : {}),
              style: { '--icon-ms': `${motion.ms}ms`, ...style } as CSSProperties,
            }
          : { style })}
        {...rest}
      >
        {maskId && (
          <mask id={maskId} maskUnits="userSpaceOnUse" x={0} y={0} width={24} height={24}>
            <rect width={24} height={24} fill="#fff" />
            {v.k!.map((p, i) => cutPath(p, i, roles?.k?.[i]))}
          </mask>
        )}
        <g mask={maskId ? `url(#${maskId})` : undefined}>
          {v.s?.map((p, i) => strokePath(p, i, roles?.s?.[i]))}
          {v.f?.map((p, i) => solidPath(p, i, roles?.f?.[i]))}
        </g>
        {v.o?.map((p, i) => overPath(p, i, roles?.o?.[i]))}
        {children}
      </svg>
    )
  })
  Component.displayName = name
  return Component
}
