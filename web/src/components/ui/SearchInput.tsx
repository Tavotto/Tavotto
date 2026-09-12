import { forwardRef, type InputHTMLAttributes } from 'react'
import { Search, X } from 'lucide-react'
import { t } from '@/i18n'
import { cn } from '@/lib/utils'
import { ICON_SIZE } from './Icon'

/**
 * 抽屉 / 面板顶部的搜索框：全产品唯一的一种（素材、画布列表、图内元素树……）。
 *
 * 形态是「安静的填充框」：surface-2 底、无边框，hover 才浮出 hairline，聚焦才变成
 * 白底 + accent 边——它是面板工具栏的一部分，不是表单里的输入项，常态不该比
 * 旁边的图标钮更抢眼。左侧放大镜固定 16px 列，右侧清除钮只在有内容时出现。
 *
 * 键盘：按键一律不冒泡（全局快捷键在输入时闭嘴）；Esc 先清空、再失焦。
 * 调用方的 `onKeyDown` 在这两条之后仍会被调到（元素树用它把 ↓ 交给第一行）。
 */
export interface SearchInputProps
  extends Omit<InputHTMLAttributes<HTMLInputElement>, 'value' | 'onChange' | 'className'> {
  value: string
  onValueChange: (value: string) => void
  /** 清除钮的可达名；缺省用通用的「清除搜索」 */
  clearLabel?: string
  /** 落在外壳上（宽度 / flex 让位），输入框自己撑满外壳 */
  className?: string
}

export const SearchInput = forwardRef<HTMLInputElement, SearchInputProps>(function SearchInput(
  { value, onValueChange, clearLabel, className, onKeyDown, ...props },
  ref,
) {
  return (
    <div className={cn('relative min-w-0 flex-1', className)}>
      <Search
        size={ICON_SIZE.sm}
        aria-hidden
        className="pointer-events-none absolute left-2 top-1/2 -translate-y-1/2 text-ink-3"
      />
      <input
        ref={ref}
        type="text"
        value={value}
        onChange={(e) => onValueChange(e.target.value)}
        onKeyDown={(e) => {
          e.stopPropagation()
          if (e.key === 'Escape') {
            if (value) onValueChange('')
            else e.currentTarget.blur()
          }
          onKeyDown?.(e)
        }}
        className={cn(
          'h-7 w-full rounded-sm border border-transparent bg-surface-2 pl-6.5 text-xs text-ink',
          value ? 'pr-6.5' : 'pr-2',
          'placeholder:text-ink-3 outline-none transition-colors duration-fast',
          'hover:border-border focus:border-accent focus:bg-surface',
        )}
        {...props}
      />
      {value && (
        <button
          type="button"
          onClick={() => onValueChange('')}
          aria-label={clearLabel ?? t('actions.clearSearch')}
          className={cn(
            'absolute right-1 top-1/2 flex h-5 w-5 -translate-y-1/2 items-center justify-center rounded-xs',
            'text-ink-3 outline-none transition-colors duration-fast hover:text-ink focus-visible:focus-ring',
          )}
        >
          <X size={ICON_SIZE.sm} />
        </button>
      )}
    </div>
  )
})
