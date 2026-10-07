import { forwardRef, type HTMLAttributes } from 'react'
import { cn } from '@/lib/utils'

export type CardAppearance = 'raised' | 'subtle' | 'plain'
export type CardPadding = 'none' | 'sm' | 'md'

const APPEARANCE: Record<CardAppearance, string> = {
  // 真的是一张卡：白底 + shadow-card（全仓只有这里写 shadow-card，foundation.test 守着）
  raised: 'bg-surface shadow-card',
  // 填充、无投影：提示框、组内的小卡
  subtle: 'bg-surface-2',
  // 透明：只要 Card 的圆角 / 交互态，不要面
  plain: '',
}

const PADDING: Record<CardPadding, string> = { none: '', sm: 'p-2', md: 'p-3' }

export interface CardProps extends HTMLAttributes<HTMLDivElement> {
  appearance?: CardAppearance
  /** 只有两档：sm 8 / md 12（none 给自己管内边距的卡，如带满宽封面的素材卡） */
  padding?: CardPadding
  /** 可点 / 可选的卡：hover 画 1px 轮廓（outline，不是 border——几何永不变化） */
  interactive?: boolean
  /** 选中：border-strong 轮廓 + selected 底；选中态还要由调用方用字重再说一遍 */
  selected?: boolean
}

/**
 * 卡片原语（2026-10-07 设计审计 §5，宪法第二节 / 第二十六节）。圆角 lg 12；外层圆角 = 内层圆角 + 内边距，
 * 卡里的封面 / 内块用 md 8（+ padding sm）。交互态全部走 outline：
 *   hover    outline 透明 → border（offset -1，画在卡内侧，不挤邻居）
 *   selected outline border-strong + bg-selected
 *   focus    卡本身 focus-visible，或卡里任何控件 :focus-visible → 2px accent 环（ring 与 shadow-card 叠加，不互相顶掉）
 */
export const Card = forwardRef<HTMLDivElement, CardProps>(function Card(
  { appearance = 'raised', padding = 'md', interactive = false, selected = false, className, children, ...rest },
  ref,
) {
  return (
    <div
      ref={ref}
      data-card={appearance}
      data-interactive={interactive || undefined}
      data-selected={selected || undefined}
      className={cn(
        'relative rounded-lg',
        APPEARANCE[appearance],
        PADDING[padding],
        (interactive || selected) &&
          'outline-1 -outline-offset-1 outline-transparent transition-[outline-color,background-color] duration-fast',
        interactive && 'hover:outline-border',
        selected && 'bg-selected outline-border-strong hover:outline-border-strong',
        // 焦点环只给可交互的卡：静态卡里的按钮自己有焦点环，外面再套一圈就成了两道
        interactive &&
          'focus-visible:ring-2 focus-visible:ring-accent has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-accent',
        className,
      )}
      {...rest}
    >
      {children}
    </div>
  )
})
