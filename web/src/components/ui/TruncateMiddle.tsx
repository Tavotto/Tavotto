import { cn } from '@/lib/utils'

/**
 * 长名字保留有区分力的尾部：`X 轴 “Reaction time (mi…”` 截掉的正是括号里的单位，
 * 中间省略成 `X 轴 “Reaction ti…(min)”` 才认得出（2026-09-14 二审 D3；组件模式 §4
 * 「长名字保留有区分力的部分」）。CSS 没有中间省略，这里把尾部 `tail` 个字符固定住、
 * 头部按宽度截；读屏读到的是两段连着的完整文字，完整值另由调用方放在 title / aria-label。
 */
export function TruncateMiddle({
  text,
  tail = 6,
  className,
}: {
  text: string
  /** 固定保留的尾部字符数 */
  tail?: number
  className?: string
}) {
  if (text.length <= tail + 4) return <span className={cn('block truncate', className)}>{text}</span>
  // whitespace-pre：切口两侧的空格不能被行尾折叠掉（「time (mi」切成「time」+「 (mi」时那个空格在头段末尾）
  return (
    <span className={cn('flex min-w-0', className)}>
      <span className="min-w-0 truncate whitespace-pre">{text.slice(0, -tail)}</span>
      <span className="shrink-0 whitespace-pre">{text.slice(-tail)}</span>
    </span>
  )
}
