import * as RP from '@radix-ui/react-popover'
import type { ReactElement, ReactNode } from 'react'
import { cn } from '@/lib/utils'

interface PopoverProps {
  trigger: ReactElement
  children: ReactNode
  align?: 'start' | 'center' | 'end'
  side?: 'top' | 'bottom' | 'left' | 'right'
  width?: number
  open?: boolean
  onOpenChange?: (v: boolean) => void
}

export function Popover({
  trigger,
  children,
  align = 'end',
  side = 'bottom',
  width = 220,
  open,
  onOpenChange,
}: PopoverProps) {
  return (
    <RP.Root open={open} onOpenChange={onOpenChange}>
      <RP.Trigger asChild>{trigger}</RP.Trigger>
      <RP.Portal>
        <RP.Content
          align={align}
          side={side}
          sideOffset={6}
          style={{ width }}
          onKeyDown={(e) => e.stopPropagation()}
          className={cn(
            'z-50 rounded-md border border-border bg-surface p-2 shadow-pop animate-pop-in',
          )}
        >
          {children}
        </RP.Content>
      </RP.Portal>
    </RP.Root>
  )
}
