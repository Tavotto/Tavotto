import type { HTMLAttributes } from 'react'
import { cn } from '@/lib/utils'
import type { StatusTone } from './Notice'

/** 圆点是唯一的语义色；neutral 的点是 ink-3 */
const DOT: Record<StatusTone, string> = {
  neutral: 'bg-ink-3',
  info: 'bg-info',
  ok: 'bg-ok',
  warn: 'bg-warn',
  danger: 'bg-danger',
}

/**
 * 状态胶囊：20px 高、11px / 500、中性底（surface-hover）、**文字永远 ink**，语义色只在字前那颗 6px 圆点上
 * （2026-10-16 UI 2.0：此前整枚染色，与 Notice 同一个「红橙大块」问题）。「就绪 / 需要登录 / 失败」这类**状态**用它；
 * 计数 / 版本号这类数据仍是 `Badge`。圆点是装饰（aria-hidden），非 neutral 默认画（neutral 是版本号 / 选区这类数据，不画），可用 `dot` 显式开关。
 */
export function StatusPill({
  tone = 'neutral',
  dot,
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
        'bg-surface-hover text-ink',
        className,
      )}
    >
      {(dot ?? tone !== 'neutral') && <span aria-hidden className={cn('size-1.5 shrink-0 rounded-full', DOT[tone])} />}
      {children}
    </span>
  )
}
