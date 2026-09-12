import type { HTMLAttributes } from 'react'
import { cn } from '@/lib/utils'

/**
 * 键帽。全产品只有这一种，两档尺寸：
 *   * `sm`（默认）：16px 内联小片，xs 圆角、surface-2 底、**没有边框**——设置页 / 说明
 *     文字里提到一个按键时用。有边框 + 28px 高就长得像一颗按钮（Session 6 之前常规页
 *     「快捷键」旁的那个 `?` 被读成帮助按钮）。
 *   * `md`：22px 的键帽（hairline + 2px 底边，像一颗真键），只给快捷键速查表——那里键帽
 *     本身就是内容，一列键帽要能一眼扫过去。
 */
export function Kbd({
  size = 'sm',
  className,
  ...props
}: HTMLAttributes<HTMLElement> & { size?: 'sm' | 'md' }) {
  return (
    <kbd
      {...props}
      className={cn(
        'inline-flex items-center justify-center font-mono text-xs leading-none',
        size === 'sm'
          ? 'h-4 min-w-4 rounded-xs bg-surface-2 px-1 text-ink-3'
          : 'h-[22px] min-w-[23px] rounded-xs border border-border border-b-2 border-b-border-strong bg-surface-2 px-1.5 font-medium text-ink-2',
        className,
      )}
    />
  )
}
