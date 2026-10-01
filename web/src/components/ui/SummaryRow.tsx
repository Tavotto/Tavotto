import type { HTMLAttributes, ReactNode } from 'react'
import { ChevronRight } from './icons'
import { ICON_SIZE } from './Icon'
import { Reveal } from './Field'
import { cn } from '@/lib/utils'

/**
 * 摘要行：「名字 + 当前值 + ›」，点开在原地展开成原来的控件
 * （2026-10-01 属性栏重设计；设计稿 `.s-sum`）。
 *
 * 规则只有一套，属性栏所有角色共用：最常改的留在面上，低频项收成一行摘要。
 *  - 右边只写**当前值或项数**（「原样」「不显示」「5 项」），不写内容清单；
 *  - 展开后右值就没用了（控件本身说得更准），所以只在收起时出现；
 *  - 展开方式只有「原地展开」一种，不推子页——同一页里的状态不丢；
 *  - 行与行之间一条内缩的 hairline（行自带上边线），相邻几行读成一张清单。
 *
 * `children` 在收起时不挂载（`Reveal` 保活到收起动画播完），所以收起的行里
 * 没有可聚焦的控件；需要它「收起也在 DOM 里」的调用方（受控值、e2e 锚点）
 * 不该用这个组件。
 */
export function SummaryRow({
  label,
  value,
  open,
  onToggle,
  children,
  className,
  ...rest
}: {
  label: ReactNode
  /** 当前值或项数；空串 / undefined = 不写 */
  value?: ReactNode
  open: boolean
  onToggle: () => void
  children: ReactNode
} & Omit<HTMLAttributes<HTMLDivElement>, 'children' | 'className'> & { className?: string }) {
  const showValue = !open && value != null && value !== ''
  return (
    <div
      data-summary-row
      {...rest}
      className={cn('mx-3 border-t border-border', className)}
    >
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        className="flex h-10 w-full items-center gap-2 rounded-sm text-left text-sm text-ink outline-none focus-visible:focus-ring"
      >
        <span className="min-w-0 flex-1 truncate font-medium">{label}</span>
        {showValue && (
          <>
            {/* 名字按内容取：标签与值之间没有分隔时读屏念成「背景#FFFFFF」 */}
            <span className="sr-only">, </span>
            <span data-summary-value className="max-w-[60%] min-w-0 shrink-0 truncate text-right text-xs text-ink-3">
              {value}
            </span>
          </>
        )}
        <ChevronRight
          size={ICON_SIZE.xs}
          aria-hidden
          className={cn('shrink-0 text-ink-3 transition-transform', open && 'rotate-90')}
        />
      </button>
      <Reveal open={open}>
        <div className="pb-3 pt-0.5">{children}</div>
      </Reveal>
    </div>
  )
}
