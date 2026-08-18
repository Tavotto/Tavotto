import * as RT from '@radix-ui/react-tooltip'
import type { ReactElement, ReactNode } from 'react'
import { cn } from '@/lib/utils'

export const TooltipProvider = ({ children }: { children: ReactNode }) => (
  <RT.Provider delayDuration={420} skipDelayDuration={280}>
    {children}
  </RT.Provider>
)

interface TipProps {
  label: ReactNode
  shortcut?: string
  side?: 'top' | 'bottom' | 'left' | 'right'
  children: ReactElement
}

export function Tip({ label, shortcut, side = 'bottom', children }: TipProps) {
  return (
    <RT.Root>
      <RT.Trigger asChild>{children}</RT.Trigger>
      <RT.Portal>
        <RT.Content
          side={side}
          sideOffset={6}
          className={cn(
            'z-50 flex items-center gap-2 rounded-sm border border-border bg-surface',
            'px-2 py-1 text-xs text-ink shadow-pop',
            'origin-[var(--radix-tooltip-content-transform-origin)]',
            'data-[state=delayed-open]:animate-pop-in',
            // instant-open = 连续划过同组按钮时的即时切换：再播一次进场会闪，只淡入
            'data-[state=instant-open]:animate-fade-in',
            'data-[state=closed]:animate-fade-out',
          )}
        >
          <span>{label}</span>
          {shortcut && (
            <span className="font-mono text-xs text-ink-3">{shortcut}</span>
          )}
        </RT.Content>
      </RT.Portal>
    </RT.Root>
  )
}
