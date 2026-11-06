import * as RP from '@radix-ui/react-popover'
import type { ReactElement, ReactNode } from 'react'
import { cn } from '@/lib/utils'

interface PopoverProps {
  trigger: ReactElement
  children: ReactNode
  align?: 'start' | 'center' | 'end'
  side?: 'top' | 'bottom' | 'left' | 'right'
  /**
   * 像素宽，或 'trigger' = 与触发器同宽（Radix 落在 Content 上的 --radix-popover-trigger-width；样张选择器用它，弹层宽跟着触发器走），
   * 或 `{ min }` = 至少这么宽、触发器更宽时跟触发器
   */
  width?: number | 'trigger' | { min: number }
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
  /** 内容自己出血到边（无内边距、裁圆角、一条发丝边）：出处卡这类整卡浮层用 */
  flush?: boolean
}

const stopAtPopover = (e: Event) => e.stopPropagation()

/**
 * 浮层经 Portal 挂在 body 下，在模态对话框的 DOM 之外；对话框的滚动锁（react-remove-scroll）在 document 上
 * `preventDefault` 一切目标不在对话框内的 wheel / touchmove，浮层里的列表就滚不动（滚动条拖得动，滚轮不行）。
 * 让这两种事件停在浮层上、到不了 document；被动监听，不干预浏览器原生滚动。React 的 `onWheel` 挂在根上，拦不住原生监听。
 */
const keepScrollInPopover = (el: HTMLElement | null) => {
  el?.addEventListener('wheel', stopAtPopover, { passive: true })
  el?.addEventListener('touchmove', stopAtPopover, { passive: true })
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
  flush,
}: PopoverProps) {
  return (
    <RP.Root open={open} onOpenChange={onOpenChange}>
      <RP.Trigger asChild>{trigger}</RP.Trigger>
      <RP.Portal>
        <RP.Content
          data-popover
          ref={keepScrollInPopover}
          align={align}
          side={side}
          sideOffset={6}
          style={{
            width:
              width === 'trigger'
                ? 'var(--radix-popover-trigger-width)'
                : typeof width === 'object'
                  ? `max(var(--radix-popover-trigger-width), ${width.min}px)`
                  : width,
          }}
          aria-label={ariaLabel}
          onOpenAutoFocus={keepFocus ? (e) => e.preventDefault() : undefined}
          onCloseAutoFocus={(e) => {
            // FocusScope 卸载后才延迟归还；此时用户可能已打开另一个浮层。
            // 与 Dialog 相同：焦点已交给层外有效元素就不抢回，否则保留 Radix 的正常归还。
            const content = e.currentTarget
            if (!(content instanceof HTMLElement)) return
            const now = content.ownerDocument.activeElement
            if (now instanceof HTMLElement && now !== content.ownerDocument.body && now.isConnected && !content.contains(now)) {
              e.preventDefault()
            }
          }}
          onKeyDown={(e) => e.stopPropagation()}
          className={cn(
            'z-popover rounded-lg bg-surface shadow-pop',
            flush ? 'overflow-hidden border border-border' : 'p-2',
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
