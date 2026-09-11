import type { ReactNode } from 'react'
import { Info } from 'lucide-react'
import { cn } from '@/lib/utils'
import { ICON_SIZE } from './Icon'

/**
 * 低权重的说明条：一句话 + 至多一个轻动作（「知道了」）。
 *
 * 它不是卡片：surface-2 底、无边框、无投影，字是 caption 级——放在列表上方时
 * 读作一行脚注，而不是第二张卡。需要警告语义的用设置页的 `InlineWarning`；
 * 这里只说「这是怎么回事」，不说「小心」。
 */
export function Notice({
  children,
  action,
  className,
}: {
  children: ReactNode
  /** 右侧的轻动作（ghost 小按钮）；按钮 28px 高，用 -my-1.5 收进单行说明的内边距里 */
  action?: ReactNode
  className?: string
}) {
  return (
    <div
      role="note"
      className={cn('flex items-start gap-1.5 rounded-sm bg-surface-2 px-2 py-1.5', className)}
    >
      <Info size={ICON_SIZE.sm} aria-hidden className="mt-px shrink-0 text-ink-3" />
      <p className="type-caption min-w-0 flex-1">{children}</p>
      {action && <span className="-my-1.5 -mr-1 shrink-0">{action}</span>}
    </div>
  )
}
