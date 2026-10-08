import type { HTMLAttributes } from 'react'
import { cn } from '@/lib/utils'
import type { StatusTone } from './Notice'

const PILL: Record<StatusTone, { box: string; dot: string }> = {
  neutral: { box: 'bg-surface-active text-ink-2', dot: 'bg-ink-3' },
  info: { box: 'bg-info-surface text-info-content', dot: 'bg-info' },
  ok: { box: 'bg-ok-surface text-ok-content', dot: 'bg-ok' },
  warn: { box: 'bg-warn-surface text-warn-content', dot: 'bg-warn' },
  danger: { box: 'bg-danger-surface text-danger-content', dot: 'bg-danger' },
}

/**
 * 状态胶囊：20px 高、11px / 500、锚点派生的底与字。「就绪 / 需要登录 / 失败」这类**状态**用它
 * （此前只有一颗绿点、其它状态有字——一种状态一种写法）；计数 / 版本号这类数据仍是 `Badge`。
 * `dot` 在字前加一颗 6px 锚点色圆点（装饰，aria-hidden）。
 */
export function StatusPill({
  tone = 'neutral',
  dot = false,
  className,
  children,
  ...rest
}: HTMLAttributes<HTMLSpanElement> & { tone?: StatusTone; dot?: boolean }) {
  return (
    <span
      {...rest}
      data-status-pill={tone}
      className={cn(
        'inline-flex h-5 shrink-0 items-center gap-1 whitespace-nowrap rounded-full px-2 text-xs font-medium',
        PILL[tone].box,
        className,
      )}
    >
      {dot && <span aria-hidden className={cn('size-1.5 shrink-0 rounded-full', PILL[tone].dot)} />}
      {children}
    </span>
  )
}
