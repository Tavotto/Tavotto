import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'

/**
 * 卡内 / 组内的小节标题。与 `ui/Field.Section` 的头同一个节奏——**上 16 下 4**，
 * 标题贴着自己的内容（2026-09-15 全面打磨 L5）。此前它是「上 8 下 10」：同一个面板里
 * Section 上宽下紧、GroupHead 上紧下宽，两种呼吸。
 *
 * 上距由自己带（`mt-2.5` + 组容器的 6px 行距 = 16），组里第一个头由 `first:mt-0` 抵消
 * ——组与组之间的 16 由外层容器的 `gap-4` 给。下距同理：`-mb-0.5` 抵掉那 6 里的 2。
 * 两个数都是相对组容器的 `gap-1.5`（组内行距）算的，所以用它的容器必须是这个行距。
 *
 * `meta` 是组名右边的一个数（条目数这类）：名字 + meta 数字，不写「名字（N）」
 * （第十九节批次 E–G）。
 */
export function GroupHead({
  children,
  meta,
  className,
}: {
  children: ReactNode
  meta?: ReactNode
  className?: string
}) {
  return (
    <p
      className={cn(
        '-mb-0.5 mt-2.5 flex h-4 items-center gap-1.5 type-section first:mt-0',
        className,
      )}
    >
      <span className="min-w-0 truncate">{children}</span>
      {meta != null && <span className="type-meta shrink-0 tabular-nums">{meta}</span>}
    </p>
  )
}
