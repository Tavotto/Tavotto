import type { LucideIcon } from 'lucide-react'
import { Button } from './Button'

/**
 * 统一空状态：一个 Lucide 图标 + 短标题 + 至多一句说明 + 至多一个主动作
 * （+ 至多一个次级链接，只给画布这种「起步」空态用），在可用区域内水平垂直
 * 居中。不画插画、不套卡片——全站空状态只此一种形态。
 */
export function EmptyState({
  icon: Icon,
  title,
  hint,
  action,
  secondary,
}: {
  icon: LucideIcon
  title: string
  hint?: string
  action?: { label: string; onClick: () => void }
  /** 次级入口，画成文字链接而不是第二颗按钮：一屏只有一个主要行动 */
  secondary?: { label: string; onClick: () => void }
}) {
  return (
    <div className="flex h-full min-h-32 flex-1 flex-col items-center justify-center gap-1.5 px-6 py-8 text-center">
      <Icon size={20} className="text-ink-faint" aria-hidden />
      <p className="text-xs font-medium text-ink-2">{title}</p>
      {hint && <p className="max-w-60 text-xs leading-relaxed text-ink-3">{hint}</p>}
      {action && (
        <Button variant="outline" size="sm" className="mt-1.5" onClick={action.onClick}>
          {action.label}
        </Button>
      )}
      {secondary && (
        <button
          type="button"
          onClick={secondary.onClick}
          className="mt-0.5 rounded-sm text-xs text-ink-3 underline-offset-2 outline-none hover:text-ink-2 hover:underline focus-visible:focus-ring"
        >
          {secondary.label}
        </button>
      )}
    </div>
  )
}
