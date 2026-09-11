import * as RS from '@radix-ui/react-select'
import { Check, ChevronDown } from 'lucide-react'
import { ICON_SIZE } from './Icon'
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
          // `group`：箭头方向要跟着 Trigger 的 data-state 走，而 data-state 挂在
          // Trigger 自己身上，子元素上的 data-[state=open]: 是读不到的。
          'group flex h-7 w-full items-center justify-between gap-1 rounded-sm border border-transparent',
          'bg-surface-2 px-1.5 text-xs text-ink outline-none transition-colors',
          'hover:border-border data-[state=open]:border-accent',
          // **弹层关着的时候键盘用户也得看得见焦点在哪。** `outline-none` 只是
          // 关掉浏览器默认那圈，不补一个替代品就是把焦点指示整个删掉——本仓库
          // 别的可聚焦控件（Button / Toggle / Segmented / Field）一律配这条。
          // 原生 `<select>` 自带默认焦点环，迁到这里时缺了它就是一条 a11y 回归。
          'focus-visible:focus-ring',
          'disabled:pointer-events-none disabled:opacity-40',
          className,
        )}
      >
        {/* 值可能是用户起的名字（接口标签后端允许 60 字）：**必须能被截断**，
            否则一个长名字会把它撑出所在的那一行。`min-w-0` 是让 flex 子项真的
            缩得下去的那一半，少了它 truncate 不生效 */}
        <span className="min-w-0 flex-1 truncate text-left">
          <RS.Value placeholder={placeholder} />
        </span>
        {/* 收起朝左、展开转到朝下——始终是同一枚 ChevronDown 在转，不做图标
            互换：两枚不同字形的视觉重心和光学尺寸难对齐，切换时会看到箭头
            「跳」一下；转过去还顺带说清了「是这一个东西被展开了」。
            时长/缓动取动效 token，与浮层 pop-in 同拍；reduced-motion 由
            index.css 全局兜底压到 0.01ms，方向照常正确，只是不播过程。 */}
        <ChevronDown
          size={ICON_SIZE.xs}
          className={cn(
            'shrink-0 text-ink-3 transition-transform duration-fast ease-pop',
            'rotate-90 group-data-[state=open]:rotate-0',
          )}
        />
      </RS.Trigger>
      <RS.Portal>
        <RS.Content
          position="popper"
          sideOffset={4}
          className={cn(
            'z-50 min-w-[var(--radix-select-trigger-width)] overflow-hidden rounded-md',
            'border border-border bg-surface p-1 shadow-pop',
            'origin-[var(--radix-select-content-transform-origin)]',
            'data-[state=open]:animate-pop-in data-[state=closed]:animate-pop-out',
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
                  'data-[highlighted]:bg-ink/[.055] data-[state=checked]:font-medium data-[state=checked]:text-ink',
                )}
              >
                <RS.ItemIndicator className="absolute left-1.5 flex items-center">
                  <Check size={ICON_SIZE.sm} />
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
