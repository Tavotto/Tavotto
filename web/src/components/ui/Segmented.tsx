import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'
import { Tip } from './Tooltip'

export interface SegmentedItem<T extends string> {
  value: T
  icon?: ReactNode
  label?: ReactNode
  tip?: string
  /**
   * 只有图标的分段项的**可达名**。tooltip 不是可达名——屏幕阅读器读到的是
   * 一个没有名字的 radio。缺省时退回 `tip`，两者都没有才真的无名。
   */
  ariaLabel?: string
  /**
   * 这一档此刻选不了（「当前图」没有正在编辑的图时）。留在原位、灰掉、
   * 不响应点击；原因走 `title`——原生 title 在禁用按钮上也出得来，tooltip 不行。
   */
  disabled?: boolean
  title?: string
}

interface SegmentedProps<T extends string> {
  value: T | null
  onChange: (v: T) => void
  items: SegmentedItem<T>[]
  className?: string
  size?: 'sm' | 'md'
  /** quiet：选中态不用 accent，融进低对比的界面 */
  tone?: 'accent' | 'quiet'
  /** 整组的可达名（「方向」「作用范围」…）——无名的 radiogroup 说不清在选什么 */
  ariaLabel?: string
}

/**
 * 单选下划线 Tabs：整组只有一条底部基线，选中项在基线上压一段实线。
 * 选中态除颜色外还有下划线与字重两重线索（未选中的文字压到 50% 不透明度），
 * 不单靠颜色区分。整组默认撑满容器宽度，各档等分。
 */
export function Segmented<T extends string>({
  value,
  onChange,
  items,
  className,
  size = 'sm',
  tone = 'accent',
  ariaLabel,
}: SegmentedProps<T>) {
  return (
    <div
      role="radiogroup"
      aria-label={ariaLabel}
      className={cn('flex w-full items-stretch border-b border-border', className)}
    >
      {items.map((item) => {
        const active = item.value === value
        const btn = (
          <button
            key={item.value}
            type="button"
            onClick={() => {
              if (!item.disabled) onChange(item.value)
            }}
            role="radio"
            aria-checked={active}
            aria-disabled={item.disabled || undefined}
            disabled={item.disabled}
            title={item.title}
            aria-label={item.label == null ? (item.ariaLabel ?? item.tip) : undefined}
            className={cn(
              'relative flex flex-1 items-center justify-center gap-1 whitespace-nowrap outline-none',
              'transition-[color,opacity] focus-visible:focus-ring',
              size === 'sm' ? 'h-7 min-w-7 px-2 text-xs' : 'h-8 min-w-8 px-2.5 text-xs',
              active
                ? tone === 'quiet'
                  ? 'font-medium text-ink opacity-100'
                  : 'font-medium text-ink opacity-100'
                : item.disabled
                  ? 'cursor-default text-ink-faint opacity-50'
                  : // 未选中的标签是要读的字：ink-3（≥4.5:1），不用 opacity 淡化
                    // （`text-ink opacity-50` 量出来 3.32:1，a11y 那条 e2e 当场红）
                    'text-ink-3 hover:text-ink',
            )}
          >
            {item.icon}
            {item.label}
            {active && (
              <span
                aria-hidden
                className={cn(
                  'absolute inset-x-0 -bottom-px h-[1.5px]',
                  'bg-ink',
                )}
              />
            )}
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
