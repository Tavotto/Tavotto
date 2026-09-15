import * as RP from '@radix-ui/react-popover'
import type { ReactElement, ReactNode } from 'react'
import { cn } from '@/lib/utils'

interface PopoverProps {
  trigger: ReactElement
  children: ReactNode
  align?: 'start' | 'center' | 'end'
  side?: 'top' | 'bottom' | 'left' | 'right'
  /** 像素宽，或 'trigger' = 与触发器同宽（Radix 落在 Content 上的 --radix-popover-trigger-width；样张选择器用它，弹层宽跟着触发器走） */
  width?: number | 'trigger'
  open?: boolean
  onOpenChange?: (v: boolean) => void
  /**
   * 打开时不把焦点搬进浮层。悬停 / 聚焦即开的帮助气泡必须传 true——
   * 否则鼠标划过一个小问号就会把键盘焦点抢走，Tab 顺序当场错乱。
   * 内容仍可 Tab 进入，Esc 仍然关闭（Radix 挂在 document 上）。
   */
  keepFocus?: boolean
  /** 浮层内容的可达名（帮助气泡用；不给的话屏幕阅读器只念到正文） */
  ariaLabel?: string
}

export function Popover({
  trigger,
  children,
  align = 'end',
  side = 'bottom',
  width = 220,
  open,
  onOpenChange,
  keepFocus,
  ariaLabel,
}: PopoverProps) {
  return (
    <RP.Root open={open} onOpenChange={onOpenChange}>
      <RP.Trigger asChild>{trigger}</RP.Trigger>
      <RP.Portal>
        <RP.Content
          align={align}
          side={side}
          sideOffset={6}
          style={{ width: width === 'trigger' ? 'var(--radix-popover-trigger-width)' : width }}
          aria-label={ariaLabel}
          onOpenAutoFocus={keepFocus ? (e) => e.preventDefault() : undefined}
          onKeyDown={(e) => e.stopPropagation()}
          className={cn(
            'z-50 rounded-md bg-surface p-2 shadow-pop',
            'origin-[var(--radix-popover-content-transform-origin)]',
            'data-[state=open]:animate-pop-in data-[state=closed]:animate-pop-out',
          )}
        >
          {children}
        </RP.Content>
      </RP.Portal>
    </RP.Root>
  )
}
