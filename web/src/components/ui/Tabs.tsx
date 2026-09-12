import type { ButtonHTMLAttributes, HTMLAttributes, ReactNode } from 'react'
import { cn } from '@/lib/utils'
import { tabClass } from './tabClass'

/**
 * 下划线标签页：整行只有文字与一条 2px 的近黑下划线，没有框、没有底色。
 * 选中态 = 字重 + 下划线（两重线索，不单靠颜色）；未选中 ink-3，hover 提到 ink-2。
 *
 * 右栏的「属性 / 画布」与画布标签栏共用同一条下划线（`TAB_UNDERLINE`）；
 * 画布标签有拖拽 / 重命名 / 关闭钮，结构不同，只借用视觉，不借用组件。
 */
export function TabList({
  label,
  className,
  children,
  ...rest
}: HTMLAttributes<HTMLDivElement> & { label: string; children: ReactNode }) {
  return (
    <div {...rest} role="tablist" aria-label={label} className={cn('flex h-full items-center gap-3', className)}>
      {children}
    </div>
  )
}

export function Tab({
  active,
  className,
  children,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { active: boolean; children: ReactNode }) {
  return (
    <button
      {...rest}
      type="button"
      role="tab"
      aria-selected={active}
      className={cn(tabClass(active), className)}
    >
      {children}
    </button>
  )
}
