import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'

/**
 * 对话框里的一行表单（2026-09-15 打磨批次 D，L1「一个表单一种行语法」）：
 * **标签列在左 80px、控件在右、行高 28**；没有标签的行第一列留空，控件仍从
 * 同一条竖线起排。
 *
 * 这一份原先长在 `ExportDialog` 里，只有导出对话框用得到（全面打磨 D24 / D25 /
 * D26：论文样式对话框自己画了另一副——名称框没有标签、字段行是「角色 64 +
 * 属性 80 + 控件 224」三列、应用范围的标签用的是分区标题的字重）。抽出来之后
 * 两个对话框读的是同一条竖线，改一处两处都跟着。
 *
 * `align="start"` 给控件比一行高的场合（一列复选框、一段说明）：标签用
 * `leading-7` 与第一行控件对齐，不跟着整块居中。
 */
export function FormRow({
  label,
  children,
  align = 'center',
  asLabel = false,
}: {
  label?: ReactNode
  children: ReactNode
  align?: 'center' | 'start'
  /**
   * 整行渲染成 `<label>`：控件是**一个** `<input>` 时点标签文字等于点控件。
   * 只对隐式关联成立的控件用它——`<button role="switch">` 那种靠外面包一层
   * `<label>` 是拿不到名字的（#299，webkit 腿），那一族走 `aria-labelledby`。
   */
  asLabel?: boolean
}) {
  const Root = asLabel ? 'label' : 'div'
  return (
    <Root
      className={cn(
        'grid min-h-7 grid-cols-[80px_minmax(0,1fr)] gap-x-3',
        align === 'start' ? 'items-start' : 'items-center',
      )}
    >
      <span className={cn('min-w-0 text-xs text-ink-2', align === 'start' && 'leading-7')}>
        {label}
      </span>
      <div
        className={cn('flex min-w-0 gap-2', align === 'start' ? 'items-start' : 'items-center')}
      >
        {children}
      </div>
    </Root>
  )
}
