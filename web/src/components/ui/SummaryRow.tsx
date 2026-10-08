import type { HTMLAttributes, ReactNode } from 'react'

/** 只放 `data-*` 稳定锚点（选择器认它们，不认文案 / class） */
type DataAttrs = { [K: `data-${string}`]: string | undefined }
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
 *  - 行与行之间**不画线**（2026-10-07 设计审计 §9.2：一屏的发丝线只留节与节之间那一条，
 *    index.css 在「节后面接摘要行」处画它）；相邻几行靠 32px 的同一节拍读成一张清单。
 *  - 第三级标题：32px、12/400 ink，右值 ink-3，chevron 坐在与 `Row` 同一条 20px 状态槽里
 *    （节 32 12/500 · 组 24 11/500 ink-3 · 折叠行 32 12/400）。
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
  triggerProps,
  ...rest
}: {
  label: ReactNode
  /** 当前值或项数；空串 / undefined = 不写 */
  value?: ReactNode
  open: boolean
  onToggle: () => void
  children: ReactNode
  /** 落在开关按钮上的 `data-*` 锚点（e2e 要点的是按钮、不是外壳；外壳的锚点走普通 `data-*` 属性） */
  triggerProps?: DataAttrs
} & Omit<HTMLAttributes<HTMLDivElement>, 'children' | 'className'> & { className?: string }) {
  const showValue = !open && value != null && value !== ''
  return (
    <div
      data-summary-row
      {...rest}
      className={cn('mx-3', className)}
    >
      <button
        type="button"
        {...triggerProps}
        onClick={onToggle}
        aria-expanded={open}
        className="grid h-8 w-full grid-cols-[minmax(40%,1fr)_minmax(0,auto)_1.25rem] items-center gap-x-2 rounded-sm text-left text-sm text-ink outline-none hover:text-ink focus-visible:focus-ring"
      >
        <span className="min-w-0 truncate">{label}</span>
        {showValue ? (
          <span className="flex min-w-0 justify-end">
            {/* 名字按内容取：标签与值之间没有分隔时读屏念成「背景#FFFFFF」 */}
            <span className="sr-only">, </span>
            <span data-summary-value className="min-w-0 truncate text-right text-sm text-ink-3">
              {value}
            </span>
          </span>
        ) : (
          <span aria-hidden />
        )}
        <span aria-hidden className="flex w-5 justify-center">
          <ChevronRight
            size={ICON_SIZE.xs}
            className={cn('shrink-0 text-ink-3 transition-transform', open && 'rotate-90')}
          />
        </span>
      </button>
      <Reveal open={open}>
        <div className="pb-3 pt-0.5">{children}</div>
      </Reveal>
    </div>
  )
}
