import type { ButtonHTMLAttributes, ReactNode } from 'react'
import { cn } from '@/lib/utils'

/**
 * 组内的折叠开关：**28px 的文字链接，11/500 ink-2，不带 chevron**
 * （2026-09-15 全面打磨 L4）。
 *
 * **属性栏的低频项（更多 / 源文件 / 间距 / 层级……）不再用它**：一律是
 * `ui/SummaryRow`（名字 + 当前值 + ›，2026-10-01）。这个文字链接只留给**嵌在某张卡里的
 * 小尾巴**（分别设置各边 / 技术详情 / 隐藏元素）——它们不是一个角色的低频项，是一张卡
 * 内部的细节开关，不该再长成一行摘要。
 * 此前六种形态并存：高 15 / 24 / 28，chevron 有 / 无，字重 400 / 500，色 ink-2 / ink-3；
 * 同一个词「更多」在对象页带 chevron、在元素页不带。
 *
 * 右侧 `summary` 是现状（改过几项 / 各边不同），不是解释：展开后它就没用了，所以只在
 * 收起时出现——除非这个开关是 `disabled` 的（收不起来），那时它是「为什么点不动」的答案。
 */
export function GroupToggle({
  open,
  onToggle,
  summary,
  disabled,
  className,
  children,
  ...rest
}: Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'onToggle' | 'children'> & {
  open: boolean
  onToggle: () => void
  summary?: ReactNode
  children: ReactNode
}) {
  return (
    <button
      type="button"
      {...rest}
      disabled={disabled}
      onClick={onToggle}
      aria-expanded={open}
      className={cn(
        'flex h-7 w-full items-center gap-1 rounded-sm text-left text-xs text-ink-2 outline-none',
        'hover:text-ink focus-visible:focus-ring disabled:hover:text-ink-2',
        className,
      )}
    >
      <span className="font-medium">{children}</span>
      {/* 收起时报现状；**收不起来的那种（disabled）展开着也要报**——「各边不同」正是
          这个开关点不动的原因，藏起来等于把原因也藏了 */}
      {(!open || disabled) && summary != null && (
        <span className="ml-auto min-w-0 shrink-0 truncate pl-2 text-right text-xs text-ink-3">
          {summary}
        </span>
      )}
    </button>
  )
}
