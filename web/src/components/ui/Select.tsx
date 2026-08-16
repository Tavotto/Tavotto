import * as RS from '@radix-ui/react-select'
import { Check, ChevronDown } from 'lucide-react'
import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'

export interface SelectOption<T extends string = string> {
  value: T
  label: ReactNode
  hint?: string
}

interface SelectProps<T extends string> {
  value: T
  onChange: (v: T) => void
  options: SelectOption<T>[]
  className?: string
  placeholder?: string
  disabled?: boolean
  ariaLabel?: string
}

export function Select<T extends string>({
  value,
  onChange,
  options,
  className,
  placeholder,
  disabled,
  ariaLabel,
}: SelectProps<T>) {
  return (
    <RS.Root value={value} onValueChange={(v) => onChange(v as T)} disabled={disabled}>
      <RS.Trigger
        aria-label={ariaLabel}
        className={cn(
          'flex h-7 w-full items-center justify-between gap-1 rounded-sm border border-transparent',
          'bg-surface-2 px-1.5 text-xs text-ink outline-none transition-colors',
          'hover:border-border data-[state=open]:border-accent',
          'disabled:pointer-events-none disabled:opacity-40',
          className,
        )}
      >
        <RS.Value placeholder={placeholder} />
        <ChevronDown size={12} className="shrink-0 text-ink-3" />
      </RS.Trigger>
      <RS.Portal>
        <RS.Content
          position="popper"
          sideOffset={4}
          className={cn(
            'z-50 min-w-[var(--radix-select-trigger-width)] overflow-hidden rounded-md',
            'border border-border bg-surface p-1 shadow-pop animate-pop-in',
          )}
        >
          <RS.Viewport className="max-h-72">
            {options.map((opt) => (
              <RS.Item
                key={opt.value}
                value={opt.value}
                className={cn(
                  'relative flex h-7 cursor-default select-none items-center gap-2 rounded-sm',
                  'pl-6 pr-2 text-xs text-ink outline-none',
                  'data-[highlighted]:bg-ink/[.055] data-[state=checked]:text-accent',
                )}
              >
                <RS.ItemIndicator className="absolute left-1.5 flex items-center">
                  <Check size={12} />
                </RS.ItemIndicator>
                <RS.ItemText>{opt.label}</RS.ItemText>
                {opt.hint && <span className="ml-auto font-mono text-xs text-ink-3">{opt.hint}</span>}
              </RS.Item>
            ))}
          </RS.Viewport>
        </RS.Content>
      </RS.Portal>
    </RS.Root>
  )
}
