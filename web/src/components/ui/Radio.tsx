import { forwardRef, type InputHTMLAttributes } from 'react'
import { cn } from '@/lib/utils'

/**
 * 单选：全产品只有这一种（与 `Checkbox` 同一套尺寸与状态）。原生 `<input type="radio">`
 * 去掉外观、画成 14px 的圆——选中时近黑外圈 + 白底上一枚 6px 的近黑圆点。
 *
 * 语义、键盘（方向键在同名组里走）、`name` / `checked` / `onChange` 全是原生的；
 * 可达名照原生规则来——外面包 `<label>` 或给 `aria-label`。
 *
 * 之前唯一的调用点（编码 Agent 详情的模型服务）写的是 `accent-ink`：浏览器自带的
 * 单选每个平台长得都不一样，也和旁边 14px 的复选框对不上。
 */
export const Radio = forwardRef<
  HTMLInputElement,
  Omit<InputHTMLAttributes<HTMLInputElement>, 'type' | 'size'>
>(function Radio({ className, ...props }, ref) {
  return (
    <span className={cn('relative inline-flex h-3.5 w-3.5 shrink-0', className)}>
      <input
        ref={ref}
        type="radio"
        {...props}
        className={cn(
          'peer absolute inset-0 m-0 h-full w-full cursor-pointer appearance-none rounded-full',
          'border border-border-strong bg-surface outline-none transition-colors duration-fast',
          'hover:border-ink/45 checked:border-ink',
          'focus-visible:focus-ring disabled:cursor-not-allowed disabled:opacity-40',
        )}
      />
      <span
        aria-hidden
        className="pointer-events-none absolute inset-0 m-auto h-1.5 w-1.5 rounded-full bg-ink opacity-0 peer-checked:opacity-100"
      />
    </span>
  )
})
