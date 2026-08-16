import type { ReactNode } from 'react'
import { ChevronRight } from 'lucide-react'
import { cn } from '@/lib/utils'

/** Inspector 分组：标题 + 内容。组间靠留白分层，不再画分隔线 */
export function Section({
  title,
  action,
  children,
  className,
  plainTitle = false,
}: {
  title?: ReactNode
  action?: ReactNode
  children: ReactNode
  className?: string
  /** 标题是内容而非分组名（如图内元素名）时关掉全大写 */
  plainTitle?: boolean
}) {
  return (
    <section className={cn('px-3 pb-4 pt-3 [&+&]:pt-0', className)}>
      {title && (
        <header className="mb-2 flex h-4 items-center justify-between">
          <h3
          className={cn(
            'min-w-0 truncate text-xs font-medium',
            plainTitle ? 'text-ink-2' : 'uppercase tracking-[.06em] text-ink-3',
          )}
        >
          {title}
        </h3>
          {action}
        </header>
      )}
      {children}
    </section>
  )
}

/** 折叠分组：低频内容默认收起，标题行即开关 */
export function Disclosure({
  title,
  open,
  onToggle,
  children,
  summary,
}: {
  title: ReactNode
  open: boolean
  onToggle: () => void
  children: ReactNode
  /** 折叠时跟在标题后的一句现状摘要 */
  summary?: ReactNode
}) {
  return (
    <section className="px-3 pb-3">
      <button
        onClick={onToggle}
        aria-expanded={open}
        className="flex h-7 w-full items-center gap-1 rounded-sm text-left text-xs text-ink-2 outline-none hover:text-ink focus-visible:focus-ring"
      >
        <ChevronRight
          size={11}
          aria-hidden
          className={cn('shrink-0 transition-transform', open && 'rotate-90')}
        />
        <span className="font-medium">{title}</span>
        {!open && summary != null && (
          <span className="ml-auto min-w-0 truncate text-right text-xs text-ink-3">{summary}</span>
        )}
      </button>
      {open && <div className="mt-1.5">{children}</div>}
    </section>
  )
}

/** 标签在左、控件在右的紧凑行 */
export function Row({
  label,
  children,
  className,
  labelWidth = 44,
}: {
  label?: ReactNode
  children: ReactNode
  className?: string
  labelWidth?: number
}) {
  return (
    <div className={cn('flex min-h-6 items-center gap-2', className)}>
      {label != null && (
        <span
          style={{ width: labelWidth }}
          className="shrink-0 text-xs text-ink-2"
        >
          {label}
        </span>
      )}
      <div className="flex min-w-0 flex-1 items-center gap-1.5">{children}</div>
    </div>
  )
}

export function Grid2({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cn('grid grid-cols-2 gap-1.5', className)}>{children}</div>
}
