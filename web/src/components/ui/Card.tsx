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
  /**
   * 卡的主控件不可用（如切换项目中）：不亮 hover 轮廓、不画焦点环、不响应按压，写 `data-disabled`。
   * 只管卡的外观；`aria-disabled` / `disabled` 由卡里的真按钮自己负责，这里不重复
   */
  disabled?: boolean
}

/**
 * 卡片原语（2026-10-07 设计审计 §5，宪法第二节 / 第二十六节）。圆角 lg 12；外层圆角 = 内层圆角 + 内边距，
 * 卡里的封面 / 内块用 md 8（+ padding sm）。交互态全部走 outline：
 *   hover    outline 透明 → border（offset -1，画在卡内侧，不挤邻居）
 *   selected outline border-strong + bg-selected
 *   press    卡的直接子按钮被按下（且没禁用）→ 底换 surface-active；只换底色，不缩放不位移
 *   focus    卡本身 focus-visible，或卡里任何控件 :focus-visible → 2px accent 环（ring 与 shadow-card 叠加，不互相顶掉）
 */
export const Card = forwardRef<HTMLDivElement, CardProps>(function Card(
  { appearance = 'raised', padding = 'md', interactive = false, selected = false, disabled = false, className, children, ...rest },
  ref,
) {
  // 交互外观（hover / press / 焦点环）只给「可交互且没禁用」的卡
  const live = interactive && !disabled
  return (
    <div
      ref={ref}
      data-card={appearance}
      data-interactive={interactive || undefined}
      data-selected={selected || undefined}
      data-disabled={(interactive && disabled) || undefined}
      className={cn(
        'relative rounded-lg',
        APPEARANCE[appearance],
        PADDING[padding],
        (interactive || selected) &&
          'outline-1 -outline-offset-1 outline-transparent transition-[outline-color,background-color] duration-fast',
        live && 'hover:outline-border',
        // 选中的卡底色另有主人（bg-selected），按压不去抢；直接子按钮 = 整卡大小的主控件（⋯ 菜单钮等深层控件不算）
        live && !selected && 'has-[>button:active:not(:disabled)]:bg-surface-active',
        selected && 'bg-selected outline-border-strong hover:outline-border-strong',
        // 焦点环只给可交互的卡：静态卡里的按钮自己有焦点环，外面再套一圈就成了两道
        live &&
          'focus-visible:ring-2 focus-visible:ring-accent has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-accent',
        className,
      )}
      {...rest}
    >
      {children}
    </div>
  )
})
