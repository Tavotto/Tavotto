import * as DM from '@radix-ui/react-dropdown-menu'
import { Check } from 'lucide-react'
import type { ReactElement, ReactNode } from 'react'
import { cn } from '@/lib/utils'

export function Menu({
  trigger,
  children,
  align = 'start',
  width = 200,
}: {
  trigger: ReactElement
  children: ReactNode
  align?: 'start' | 'center' | 'end'
  width?: number
}) {
  return (
    <DM.Root>
      <DM.Trigger asChild>{trigger}</DM.Trigger>
      <DM.Portal>
        <DM.Content
          align={align}
          sideOffset={6}
          style={{ minWidth: width }}
          className={cn(
            'z-50 rounded-md border border-border bg-surface p-1 shadow-pop',
            // 从触发器那个角展开，而不是从自己中心——菜单与按钮的因果关系才看得出来
            'origin-[var(--radix-dropdown-menu-content-transform-origin)]',
            'data-[state=open]:animate-pop-in data-[state=closed]:animate-pop-out',
          )}
        >
          {children}
        </DM.Content>
      </DM.Portal>
    </DM.Root>
  )
}

export function MenuItem({
  children,
  shortcut,
  onSelect,
  disabled,
  danger,
}: {
  children: ReactNode
  shortcut?: string
  onSelect?: () => void
  disabled?: boolean
  danger?: boolean
}) {
  return (
    <DM.Item
      disabled={disabled}
      onSelect={onSelect}
      className={cn(
        'flex h-7 cursor-default select-none items-center gap-3 rounded-sm px-2 text-xs outline-none',
        'data-[highlighted]:bg-ink/[.055] data-[disabled]:opacity-35',
        danger ? 'text-danger' : 'text-ink',
      )}
    >
      <span className="flex-1 truncate">{children}</span>
      {shortcut && <span className="font-mono text-xs text-ink-3">{shortcut}</span>}
    </DM.Item>
  )
}

export function MenuCheckItem({
  children,
  checked,
  onSelect,
}: {
  children: ReactNode
  checked: boolean
  onSelect: () => void
}) {
  return (
    <DM.CheckboxItem
      checked={checked}
      onSelect={(e) => {
        e.preventDefault()
        onSelect()
      }}
      className={cn(
        'flex h-7 cursor-default select-none items-center rounded-sm pl-6 pr-2 text-xs text-ink outline-none',
        'relative data-[highlighted]:bg-ink/[.055]',
      )}
    >
      <DM.ItemIndicator className="absolute left-1.5 flex items-center">
        <Check size={12} />
      </DM.ItemIndicator>
      {children}
    </DM.CheckboxItem>
  )
}

export const MenuSeparator = () => <DM.Separator className="my-1 h-px bg-border" />

export const MenuLabel = ({ children }: { children: ReactNode }) => (
  <DM.Label className="px-2 py-1 text-xs font-medium uppercase tracking-[.06em] text-ink-3">
    {children}
  </DM.Label>
)
