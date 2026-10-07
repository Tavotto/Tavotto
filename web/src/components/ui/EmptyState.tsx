import type { IconComponent } from './icons'
import { ICON_SIZE } from './Icon'
import { Button } from './Button'
import type { Variant } from './buttonClass'

/**
 * 统一空状态（v2，2026-10-07 设计审计 §3.9）：一枚图标坐在 40px 的 lg 圆角底座上（surface-hover）+
 * 标题（type-title 15 / 600）+ 至多一句说明（type-caption，限宽 42ch）+ 至多一个主动作（32px `lg`）
 * （+ 至多一个次级链接，只给画布这种「起步」空态用），在可用区域内水平垂直居中。
 * 不画插画、不套卡片——全站空状态只此一种形态。
 *
 * 主动作默认 `secondary`：空态常常与本屏真正的主按钮同屏（顶栏的导出），一个上下文只有一颗填色主按钮；
 * 空态就是这一屏唯一的行动时（首页、空画布）传 `variant: 'primary'`。
 */
export function EmptyState({
  icon: Icon,
  title,
  hint,
  action,
  secondary,
  ...rest
}: {
  icon: IconComponent
  title: string
  hint?: string
  action?: { label: string; onClick: () => void; variant?: Extract<Variant, 'primary' | 'secondary'> }
  /** 次级入口，画成文字链接而不是第二颗按钮：一屏只有一个主要行动 */
  secondary?: { label: string; onClick: () => void }
} & Record<`data-${string}`, string | number | boolean | undefined>) {
  return (
    <div
      {...rest}
      data-empty-state
      className="flex h-full min-h-32 flex-1 flex-col items-center justify-center gap-1.5 px-6 py-8 text-center"
    >
      {/* 图标是这一屏唯一的图形线索：放进底座里才有分量（此前 20px 灰图标几乎看不见） */}
      <span aria-hidden className="mb-1.5 flex size-10 items-center justify-center rounded-lg bg-surface-hover text-ink-2">
        <Icon size={ICON_SIZE.lg} />
      </span>
      <p className="type-title">{title}</p>
      {hint && <p className="type-caption max-w-[42ch]">{hint}</p>}
      {action && (
        <Button variant={action.variant ?? 'secondary'} size="lg" className="mt-3" onClick={action.onClick}>
          {action.label}
        </Button>
      )}
      {secondary && (
        <button
          type="button"
          onClick={secondary.onClick}
          className="mt-0.5 rounded-sm text-sm text-ink-3 underline-offset-2 outline-none hover:text-ink-2 hover:underline focus-visible:focus-ring"
        >
          {secondary.label}
        </button>
      )}
    </div>
  )
}
