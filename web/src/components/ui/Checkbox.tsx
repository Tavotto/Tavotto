import { forwardRef, type InputHTMLAttributes } from 'react'
import { Check } from './icons'
import { ICON_SIZE, ICON_STROKE } from './Icon'
import { cn } from '@/lib/utils'

/**
 * 复选框：全产品只有这一种。原生 `<input type="checkbox">` 去掉外观、画成
 * 16px 的方块（xs 圆角；2026-09-15 全面审计 B04：14 在 48px 的设置行里像角标，
 * OpenAI 18 / Claude 16），选中时近黑填色 + 白色对勾——对勾是全仓唯一允许用
 * 加粗描边（`ICON_STROKE.emphasis`）的图标，1px 的勾在 16px 底上看不见。
 * hover 只能加深（ink-2），此前 ink/45 合成到白上比静态的 border-control 还淡。
 *
 * 语义、键盘、`checked` / `disabled` / `onChange` 全是原生的；可达名照原生规则
 * 来——外面包 `<label>` 或给 `aria-label`，两者对 `<input>` 都成立
 * （与 `Toggle` 那颗 `<button>` 不同，那儿包 label 不算数）。
 *
 * 之前五处各写各的：`accent-ink-2`、什么都不写、`h-3.5 w-3.5`……浏览器自带的
 * 复选框每个平台长得都不一样，也和旁边的开关对不上。
 */
export const Checkbox = forwardRef<
  HTMLInputElement,
  Omit<InputHTMLAttributes<HTMLInputElement>, 'type' | 'size'>
>(function Checkbox({ className, ...props }, ref) {
  return (
    <span className={cn('relative inline-flex h-4 w-4 shrink-0', className)}>
      <input
        ref={ref}
        type="checkbox"
        {...props}
        className={cn(
          'peer absolute inset-0 m-0 h-full w-full appearance-none rounded-xs',
          // 未选中的方框：border-control（对白 3.48:1）。border-strong 只有 1.57:1，方框是复选框的全部识别信息
          'border border-border-control bg-surface outline-none transition-colors duration-fast',
          'hover:border-ink-2 checked:border-ink checked:bg-ink',
          'focus-visible:focus-ring disabled:cursor-not-allowed disabled:opacity-40',
        )}
      />
      <Check
        size={ICON_SIZE.sm}
        strokeWidth={ICON_STROKE.emphasis}
        aria-hidden
        className="pointer-events-none absolute inset-0 m-auto text-white opacity-0 transition-opacity duration-fast peer-checked:opacity-100"
      />
    </span>
  )
})
