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
  /**
   * 克制浮起（登记豁免，宪法第二十五节「Card lift」）：hover / 键盘聚焦时整卡 translateY(-1px) + 阴影升一档，
   * 按下回落到 0。只在「品牌特色位置」开（首页最近项目卡），不全局开；整卡 translateY 整数 px（-2px）+ scale(--card-lift-scale，≤1.02) + 阴影升档 + 1px 品牌蓝描边；无外发光、无 will-change、无 translateZ。
   * reduced-motion 下不位移只换阴影；disabled 不浮起。只对 raised 卡有意义（阴影就是浮起的那一块）
   */
  lift?: boolean
}

/**
 * 卡片原语（2026-10-07 设计审计 §5，宪法第二节 / 第二十六节）。**几何默认不变**（只画 outline，不缩放不位移）；
 * `lift` 变体是登记过的唯一豁免（只许首页最近项目卡，translateY 整数 px、上限 2px、无 scale，见 ui-visual-discipline.md）。圆角 lg 12；外层圆角 = 内层圆角 + 内边距，
 * 卡里的封面 / 内块用 md 8（+ padding sm）。交互态全部走 outline：
 *   hover    outline 透明 → border（offset -1，画在卡内侧，不挤邻居）
 *   selected outline border-strong + bg-selected
 *   press    卡的直接子按钮被按下（且没禁用）→ 底换 surface-active（lift 变体另回落到 0 位移）
 *   focus    卡本身 focus-visible，或卡里任何控件 :focus-visible → 2px accent 环（ring 与 shadow-card 叠加，不互相顶掉）
 */
export const Card = forwardRef<HTMLDivElement, CardProps>(function Card(
  { appearance = 'raised', padding = 'md', interactive = false, selected = false, disabled = false, lift = false, className, children, ...rest },
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
          'outline-1 -outline-offset-1 outline-transparent',
        (interactive || selected) && !lift && 'duration-fast',
        // lift 变体多过渡位移与阴影（`translate` 是 Tailwind v4 translate 工具类写的独立属性）
        (interactive || selected) &&
          (lift ? 'transition-[outline-color,background-color,translate,scale,box-shadow] duration-base ease-[var(--ease-pop)]' : 'transition-[outline-color,background-color]'),
        live && 'hover:outline-border',
        // 选中的卡底色另有主人（bg-selected），按压不去抢；直接子按钮 = 整卡大小的主控件（⋯ 菜单钮等深层控件不算）
        live && !selected && 'has-[>button:active:not(:disabled)]:bg-surface-active',
        // lift：hover / 键盘聚焦 → 上浮 2px + 放大 --card-lift-scale（≤1.02）+ 阴影 / 描边升档；按下回落（像被按下去：位移 0、缩放 1、阴影回 card 档）。
        // 位移与缩放只在 motion-safe 下有，reduced-motion 只换阴影 / 描边。
        // 静止态就写 translate-y-0 scale-100（恒等变换，不是 none）：none ↔ 非 none 的切换会让 Blink 每次 hover 整页重排一次
        // （LayoutCount +1 / hover，TaskDuration +50%）；一直带着恒等变换就只剩合成 / 绘制。不用 will-change 常驻
        live &&
          lift &&
          !selected &&
          appearance === 'raised' &&
          cn(
            'translate-y-0 scale-100 hover:shadow-lift has-[:focus-visible]:shadow-lift',
            'motion-safe:hover:-translate-y-0.5 motion-safe:hover:scale-(--card-lift-scale)',
            'motion-safe:has-[:focus-visible]:-translate-y-0.5 motion-safe:has-[:focus-visible]:scale-(--card-lift-scale)',
            'has-[>button:active:not(:disabled)]:translate-y-0 has-[>button:active:not(:disabled)]:scale-100 has-[>button:active:not(:disabled)]:shadow-card',
          ),
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
