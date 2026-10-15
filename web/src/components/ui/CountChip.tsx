import type { ButtonHTMLAttributes, ReactNode } from 'react'
import { cn } from '@/lib/utils'

/** 圆点的色：只有「要改」「留意」带颜色，其余（建议 / 无法核验）永远是灰 */
export type CountTone = 'danger' | 'warn' | 'quiet'

const DOT: Record<CountTone, string> = {
  danger: 'bg-danger',
  warn: 'bg-warn',
  quiet: 'bg-ink-faint',
}

/**
 * 计数芯片（2026-10-16 UI 2.0 精修 03）：检查摘要的一格——「5 项要改 / 4 项留意 / 3 条建议」。
 *
 * 26 高、全圆角、`surface-hover` 底；**颜色只在 6px 圆点上**（`danger` / `warn`；`quiet` 灰），字永远是 ink，
 * 数字 tabular-nums。「建议」永不染色（用 `quiet`）。导出对话框的检查摘要与问题面板的等级开关是同一件东西，
 * 共用这一个：有 `onClick` 就是按钮（`pressed` 给可切换的那种，`aria-pressed`），没有就是只读的 span。
 * `lead` 换掉圆点（问题面板用等级记号，形状也说出等级）；`hideLabel` 把文字收进读屏（窄抽屉里只露记号 + 数）。
 */
export function CountChip({
  tone = 'quiet',
  count,
  label,
  lead,
  hideLabel,
  pressed,
  onClick,
  className,
  ...rest
}: Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'children'> & {
  tone?: CountTone
  count: number
  label: string
  lead?: ReactNode
  hideLabel?: boolean
  pressed?: boolean
}) {
  const body = (
    <>
      {lead ?? <span aria-hidden className={cn('size-1.5 shrink-0 rounded-full', DOT[tone])} />}
      <span className="font-medium tabular-nums text-ink">{count}</span>
      {/* 文本空格：flex 子项之间的空白不画出来（间距是 gap），但可达名 / 复制出来的文字是「5 to fix」不是「5to fix」 */}
      {' '}
      <span className={cn('text-ink-2', hideLabel && 'sr-only')}>{label}</span>
    </>
  )
  const box = cn(
    'inline-flex h-6.5 shrink-0 items-center gap-1.5 whitespace-nowrap rounded-full bg-surface-hover px-2.5 text-sm',
    className,
  )
  if (!onClick) {
    return (
      <span data-count-chip={tone} className={box}>
        {body}
      </span>
    )
  }
  return (
    <button
      {...rest}
      type="button"
      data-count-chip={tone}
      aria-pressed={pressed}
      onClick={onClick}
      className={cn(
        box,
        'outline-none transition-colors duration-fast hover:bg-surface-active focus-visible:focus-ring',
        pressed && 'bg-selected',
      )}
    >
      {body}
    </button>
  )
}
