import * as RD from '@radix-ui/react-dialog'
import { X } from 'lucide-react'
import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'

export type DialogSize = 'sm' | 'md' | 'lg'

const WIDTH: Record<DialogSize, number> = { sm: 360, md: 420, lg: 560 }

interface DialogProps {
  open: boolean
  onOpenChange: (v: boolean) => void
  title: ReactNode
  description?: ReactNode
  children: ReactNode
  footer?: ReactNode
  size?: DialogSize
  /** 特殊场合才用；常规尺寸走 size */
  width?: number
  /**
   * 有不可中断的操作在跑：标记 aria-busy，并挡住 Esc / 点外面 / 右上角关闭。
   * 破坏性写入（写回原始文件、历史恢复）中途被关掉会让用户以为已取消，其实没有。
   */
  busy?: boolean
  /** 与 busy 分开：不忙但也不许随手关（例如必须做出选择的确认框） */
  blockDismiss?: boolean
}

export function Dialog({
  open,
  onOpenChange,
  title,
  description,
  children,
  footer,
  size = 'md',
  width,
  busy = false,
  blockDismiss = false,
}: DialogProps) {
  const locked = busy || blockDismiss

  return (
    <RD.Root open={open} onOpenChange={(v) => (locked && !v ? undefined : onOpenChange(v))}>
      <RD.Portal>
        <RD.Overlay
          className={cn(
            'fixed inset-0 z-40 bg-ink/20 backdrop-blur-[1px]',
            'data-[state=open]:animate-fade-in data-[state=closed]:animate-fade-out',
          )}
        />
        <RD.Content
          style={{ width: width ?? WIDTH[size] }}
          aria-busy={busy || undefined}
          onKeyDown={(e) => e.stopPropagation()}
          onEscapeKeyDown={(e) => locked && e.preventDefault()}
          onInteractOutside={(e) => locked && e.preventDefault()}
          className={cn(
            'fixed left-1/2 top-1/2 z-50 max-h-[86vh] max-w-[calc(100vw-2rem)]',
            '-translate-x-1/2 -translate-y-1/2',
            'flex flex-col overflow-hidden rounded-lg border border-border bg-surface shadow-pop',
            // 退场靠 Radix 的 Presence 保活（它会等 animationend）——**不要**改成条件
            // 渲染，那样只有进场、没有退场，浮层会「淡入之后瞬间消失」
            'data-[state=open]:animate-pop-in data-[state=closed]:animate-pop-out',
          )}
        >
          <div className="flex items-start justify-between gap-3 px-4 pb-1 pt-3.5">
            <div className="min-w-0">
              <RD.Title className="text-lg font-medium text-ink">{title}</RD.Title>
              {description && (
                <RD.Description className="mt-0.5 text-xs text-ink-2">{description}</RD.Description>
              )}
            </div>
            {!locked && (
              <RD.Close
                className="-mr-1.5 -mt-1 flex h-7 w-7 shrink-0 cursor-pointer items-center justify-center rounded-sm text-ink-3 hover:bg-ink/[.055] hover:text-ink"
                aria-label="关闭"
              >
                <X size={14} />
              </RD.Close>
            )}
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">{children}</div>
          {footer && (
            <div className="flex items-center justify-end gap-2 px-4 pb-3.5 pt-1">
              {footer}
            </div>
          )}
        </RD.Content>
      </RD.Portal>
    </RD.Root>
  )
}
