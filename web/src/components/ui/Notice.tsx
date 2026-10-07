import type { HTMLAttributes, ReactNode } from 'react'
import { CircleCheck, Info, OctagonAlert, TriangleAlert, type IconComponent } from './icons'
import { ICON_SIZE } from './Icon'
import { cn } from '@/lib/utils'

export type StatusTone = 'neutral' | 'info' | 'ok' | 'warn' | 'danger'

/**
 * 语义色从锚点派生（index.css：`--color-<s>-surface` 10% / `-border` 30% / `-content` 70% 压黑），
 * 新增一种状态色零手调。info 的锚点就是 accent。字一律用 -content（≥4.5:1），
 * 锚点本色只给图标 / 圆点（非文字 3:1）。
 */
const TONE: Record<StatusTone, { box: string; icon: string }> = {
  neutral: { box: 'bg-surface-2 text-ink-2 inset-ring inset-ring-border', icon: 'text-ink-3' },
  info: { box: 'bg-info-surface text-info-content inset-ring inset-ring-info-border', icon: 'text-info' },
  ok: { box: 'bg-ok-surface text-ok-content inset-ring inset-ring-ok-border', icon: 'text-ok' },
  warn: { box: 'bg-warn-surface text-warn-content inset-ring inset-ring-warn-border', icon: 'text-warn' },
  danger: { box: 'bg-danger-surface text-danger-content inset-ring inset-ring-danger-border', icon: 'text-danger' },
}

/** 每个语气一枚图标（宪法第二十六节的严重度表：阻断八角 / 警告三角 / 信息圆 / 完成对勾） */
const ICON: Record<StatusTone, IconComponent> = {
  neutral: Info,
  info: Info,
  ok: CircleCheck,
  warn: TriangleAlert,
  danger: OctagonAlert,
}

/**
 * 说明条（2026-10-07 设计审计 §9.1 / §10.2 恢复为原语）：一块 8 圆角、内边距 8 / 12 的语气底，
 * 图标 + （可选标题）+ 正文 + （可选）右侧一颗动作。对话框里放在页脚上方（加载错误放顶部），
 * 设置组里作为组内最后一行。danger 带 `role="alert"`。
 */
export function Notice({
  tone = 'neutral',
  title,
  action,
  icon,
  className,
  children,
  ...rest
}: Omit<HTMLAttributes<HTMLDivElement>, 'title'> & {
  tone?: StatusTone
  title?: ReactNode
  /** 右侧动作（一颗 sm / md 按钮） */
  action?: ReactNode
  /** 换图标；false = 不画图标 */
  icon?: IconComponent | false
  children?: ReactNode
}) {
  const Icon = icon === false ? null : (icon ?? ICON[tone])
  return (
    <div
      role={tone === 'danger' ? 'alert' : undefined}
      {...rest}
      data-notice={tone}
      className={cn('flex items-start gap-2 rounded-md px-3 py-2 text-sm leading-5', TONE[tone].box, className)}
    >
      {Icon && <Icon size={ICON_SIZE.sm} aria-hidden className={cn('mt-0.5 shrink-0', TONE[tone].icon)} />}
      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        {title != null && <p className="font-medium">{title}</p>}
        {children != null && <div className="min-w-0 break-words">{children}</div>}
      </div>
      {action != null && <div className="-my-0.5 flex shrink-0 items-center gap-1.5">{action}</div>}
    </div>
  )
}
