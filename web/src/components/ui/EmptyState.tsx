import { Sketch, type SketchName } from './Sketch'
import { Button } from './Button'
import type { Variant } from './buttonClass'

/**
 * 统一空状态（v3，2026-10-28 手帐线稿）：一幅 54×42 的线稿（`ui/Sketch`，ink-faint，无底座无底色）+
 * 标题（type-title 15 / 600）+ 至多一句说明（type-caption，限宽 42ch）+ 至多一个主动作（32px `lg`）
 * （+ 至多一个次级链接，只给画布这种「起步」空态用），在可用区域内水平垂直居中。
 * 不套卡片——全站空状态只此一种形态。线稿从 `Sketch` 的封闭集里按意思选，同义复用。
 *
 * 主动作默认 `secondary`：空态常常与本屏真正的主按钮同屏（顶栏的导出），一个上下文只有一颗填色主按钮；
 * 空态就是这一屏唯一的行动时（首页、空画布）传 `variant: 'primary'`。
 */
export function EmptyState({
  art,
  title,
  hint,
  action,
  secondary,
  ...rest
}: {
  art: SketchName
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
      {/* 线稿是这一屏唯一的图形线索：与标题同一条竖中线（容器 items-center，svg 宽 54 → 无半像素） */}
      <span aria-hidden className="mb-2 flex text-ink-faint">
        <Sketch name={art} className="block" />
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
