import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'

/**
 * 执行器图标（`AgentIcon`）与厂商图标（`VendorIcon`）共用的外框：固定边长、居中、无底块无边框、
 * 默认 `text-ink-2`（单色图形跟随 currentColor，暗色模式下自然翻转）。两处各自只管里面画什么。
 */
export function IconFrame({
  size,
  className,
  children,
}: {
  /** 外框边长 */
  size: number
  className?: string
  children: ReactNode
}) {
  return (
    <span
      aria-hidden
      style={{ width: size, height: size }}
      className={cn('flex shrink-0 items-center justify-center text-ink-2', className)}
    >
      {children}
    </span>
  )
}
