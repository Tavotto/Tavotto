import type { ReactNode } from 'react'
import { Check } from 'lucide-react'
import { cn } from '@/lib/utils'
import { Tip } from './Tooltip'

export interface SegmentedItem<T extends string> {
  value: T
  icon?: ReactNode
  label?: ReactNode
  tip?: string
}

interface SegmentedProps<T extends string> {
  value: T | null
  onChange: (v: T) => void
  items: SegmentedItem<T>[]
  className?: string
  size?: 'sm' | 'md'
  /** quiet：选中态不用 accent，融进低对比的界面 */
  tone?: 'accent' | 'quiet'
}

/**
 * 单选分段控件：整体一个 1px 边框，内部用分隔线。
 * 选中态除颜色外还带 check 标记（有文字时）或加重底色 + 字重（仅图标时），
 * 不单靠颜色区分。
 */
export function Segmented<T extends string>({
  value,
  onChange,
  items,
  className,
  size = 'sm',
  tone = 'accent',
}: SegmentedProps<T>) {
  return (
    <div
      role="radiogroup"
      className={cn(
        'inline-flex items-stretch overflow-hidden rounded-sm border border-border bg-surface',
        className,
      )}
    >
      {items.map((item, i) => {
        const active = item.value === value
        const btn = (
          <button
            key={item.value}
            type="button"
            onClick={() => onChange(item.value)}
            role="radio"
            aria-checked={active}
            className={cn(
              'flex flex-1 items-center justify-center gap-1 whitespace-nowrap outline-none transition-colors',
              'focus-visible:focus-ring',
              size === 'sm' ? 'h-[26px] min-w-[26px] px-1.5 text-xs' : 'h-7 min-w-7 px-2 text-xs',
              i > 0 && 'border-l border-border',
              active
                ? tone === 'quiet'
                  ? 'bg-ink/[.06] font-medium text-ink'
                  : 'bg-accent-subtle font-medium text-accent'
                : 'text-ink-3 hover:bg-ink/[.04] hover:text-ink-2',
            )}
          >
            {active && item.label != null && <Check size={11} className="shrink-0" aria-hidden />}
            {item.icon}
            {item.label}
          </button>
        )
        return item.tip ? (
          <Tip key={item.value} label={item.tip}>
            {btn}
          </Tip>
        ) : (
          btn
        )
      })}
    </div>
  )
}
