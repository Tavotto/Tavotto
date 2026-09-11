import type { HTMLAttributes } from 'react'
import { cn } from '@/lib/utils'

/**
 * 键帽：设置页 / 说明文字里提到一个按键时的小片。16px 高、xs 圆角、surface-2 底、
 * 等宽字，**没有边框**——有边框 + 28px 高就长得像一颗按钮（Session 6 之前常规页
 * 「快捷键」旁的那个 `?` 被读成帮助按钮）。快捷键速查表里的大键帽是另一种
 * （那里键帽本身是内容），不共用。
 */
export function Kbd({ className, ...props }: HTMLAttributes<HTMLElement>) {
  return (
    <kbd
      {...props}
      className={cn(
        'inline-flex h-4 min-w-4 items-center justify-center rounded-xs bg-surface-2 px-1',
        'font-mono text-xs leading-none text-ink-3',
        className,
      )}
    />
  )
}
