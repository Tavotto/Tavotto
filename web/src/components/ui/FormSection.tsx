import type { HTMLAttributes, ReactNode } from 'react'
import { InFieldGroup } from './fieldGroupContext'
import { cn } from '@/lib/utils'

/**
 * 表单分区（2026-10-07 设计审计 §4.3 / §9.1，宪法第十三节修订）：标题 13 / 600 / ink + 可选一句说明
 * （12 / ink-3、最多 60ch）+ 一个或几个 `FieldGroup`。分区之间 24 由外层容器的 gap 给（`gap-6`），
 * 分区自己不带外边距。`action` 是标题行右侧「管整个分区」的那一颗。
 */
export function FormSection({
  title,
  description,
  action,
  className,
  children,
  ...rest
}: Omit<HTMLAttributes<HTMLElement>, 'title'> & {
  title?: ReactNode
  description?: ReactNode
  action?: ReactNode
  children: ReactNode
}) {
  return (
    <section {...rest} data-form-section className={cn('flex flex-col gap-2', className)}>
      {(title != null || description != null || action != null) && (
        <header className={cn('flex items-start justify-between gap-3', action != null && 'min-h-7 items-center')}>
          <span className="flex min-w-0 flex-col gap-0.5">
            {title != null && <h3 className="text-base font-semibold text-ink">{title}</h3>}
            {description != null && <p className="type-caption max-w-[60ch]">{description}</p>}
          </span>
          {action}
        </header>
      )}
      {children}
    </section>
  )
}

/**
 * 一组字段：12 圆角、ink 3% 的底（`bg-group`），行内边距 12 / 16，行与行之间一条左右各内缩 16 的
 * hairline（index.css 的 `[data-ui-field-group]` 规则——用背景画线，border 会顶到组的边缘）。
 * macOS 系统设置 / Linear 设置的标准形态；组里的说明条（`Notice`）作为最后一行。
 */
export function FieldGroup({ className, children, ...rest }: HTMLAttributes<HTMLDivElement>) {
  return (
    <InFieldGroup.Provider value>
      {/* `@container`：组里的设置行按**组的宽度**决定并排还是叠放（`settings/SettingRow` 的 SETTING_ROW_STACK_BELOW） */}
      <div {...rest} data-ui-field-group className={cn('@container flex flex-col rounded-lg bg-group', className)}>
        {children}
      </div>
    </InFieldGroup.Provider>
  )
}
