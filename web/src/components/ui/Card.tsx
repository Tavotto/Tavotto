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
   * 克制浮起（登记豁免，宪法第二十五节「Card lift」）：hover / 键盘聚焦时 translateY(-2px) + 背景层放大 + 阴影 / 光晕升档，
   * 按下回落。**只缩放背景层**（`::before`：底色 + 阴影 + 光晕 + 轮廓 + 焦点环，scale ≤1.02），卡本身与文字不缩放、最多整数 px 上移，
   * 所以文字不会重新栅格化。只在「品牌特色位置」开（首页最近项目卡），不全局开；无 will-change 常驻、无 translateZ。
   * reduced-motion 下不位移不缩放、只换阴影 / 光晕；disabled / selected 不浮起。只对 raised 卡有意义（阴影就是浮起的那一块）
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
  // lift 的「分层」实现（推荐方案）：卡本身不缩放——底色 / 阴影 / 光晕 / 轮廓 / 焦点环都搬到 `::before` 这一层，
  // hover 时只有这一层 scale（--card-lift-scale，≤1.02），文字与内容层保持原尺寸（最多整数 px 上移），所以缩放时文字不会重新栅格化。
  // 只对 raised 且非 selected 的卡有意义（selected 的底色另有主人）
  const layered = lift && appearance === 'raised' && !selected
  return (
    <div
      ref={ref}
      data-card={appearance}
      data-interactive={interactive || undefined}
      data-selected={selected || undefined}
      data-disabled={(interactive && disabled) || undefined}
      data-lift={layered || undefined}
      className={cn(
        'relative rounded-lg',
        layered ? 'isolate' : APPEARANCE[appearance],
        PADDING[padding],
        // ---- 常规（非分层）卡：outline 画在卡自己身上 ----
        (interactive || selected) && !layered && 'outline-1 -outline-offset-1 outline-transparent',
        (interactive || selected) && !layered && 'transition-[outline-color,background-color] duration-fast',
        live && !layered && 'hover:outline-border',
        // 选中的卡底色另有主人（bg-selected），按压不去抢；直接子按钮 = 整卡大小的主控件（⋯ 菜单钮等深层控件不算）
        live && !selected && !layered && 'has-[>button:active:not(:disabled)]:bg-surface-active',
        selected && 'bg-selected outline-border-strong hover:outline-border-strong',
        // 焦点环只给可交互的卡：静态卡里的按钮自己有焦点环，外面再套一圈就成了两道
        live &&
          !layered &&
          'focus-visible:ring-2 focus-visible:ring-accent has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-accent',
        // ---- lift 分层卡 ----
        // 根：只整数 px 上移（-2px）；静止态就写 translate-y-0（恒等位移，不是 none）：none ↔ 非 none 的切换会让 Blink 每次 hover 整页重排一次
        // （LayoutCount +1 / hover，TaskDuration +50%）。不用 will-change 常驻，不用 translateZ(0)
        layered && interactive && 'translate-y-0 transition-[translate] duration-base ease-[var(--ease-pop)]',
        // ::before = 背景层：底色 + shadow-card，静止态 scale-100（同样是恒等变换）；负 z 落在 isolate 的根里，内容盖在它上面
        layered &&
          cn(
            "before:pointer-events-none before:absolute before:inset-0 before:-z-10 before:rounded-lg before:bg-surface before:shadow-card before:content-['']",
            'before:outline-1 before:-outline-offset-1 before:outline-transparent before:scale-100',
            interactive &&
              'before:transition-[outline-color,background-color,scale,box-shadow] before:duration-base before:ease-[var(--ease-pop)]',
          ),
        // 可交互且没禁用：hover / 键盘聚焦 → 根上浮 2px、背景层放大 + 阴影 / 光晕升档 + 轮廓；按下回落（像被按下去：位移 0、缩放 1、阴影回 card 档、底换 surface-active）。
        // 位移与缩放只在 motion-safe 下有，reduced-motion 只换阴影 / 光晕 / 轮廓
        layered &&
          live &&
          cn(
            'hover:before:shadow-lift hover:before:outline-border has-[:focus-visible]:before:shadow-lift',
            'motion-safe:hover:-translate-y-0.5 motion-safe:hover:before:scale-(--card-lift-scale)',
            'motion-safe:has-[:focus-visible]:-translate-y-0.5 motion-safe:has-[:focus-visible]:before:scale-(--card-lift-scale)',
            'has-[:focus-visible]:before:ring-2 has-[:focus-visible]:before:ring-accent focus-visible:before:ring-2 focus-visible:before:ring-accent',
            'has-[>button:active:not(:disabled)]:translate-y-0 has-[>button:active:not(:disabled)]:before:scale-100',
            'has-[>button:active:not(:disabled)]:before:shadow-card has-[>button:active:not(:disabled)]:before:bg-surface-active',
          ),
        className,
      )}
      {...rest}
    >
      {children}
    </div>
  )
})
