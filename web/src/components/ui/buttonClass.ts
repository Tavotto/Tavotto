import { cn } from '@/lib/utils'

/**
 * 按钮层级只有四档（docs/ux/DESIGN_CONSTITUTION.md 第七节）：
 *   primary   近黑填色。每个上下文最多一个（顶栏=导出、助手=发送、弹窗=确认）。
 *   secondary 灰底胶囊、无边线（2026-09-30）。工具类操作的默认形态。
 *   ghost     无边无底，hover 才浮出一层 surface-hover。工具栏、行内动作、图标钮。
 *   danger    红字 ghost。只给不可逆操作。
 * 蓝色不出现在任何一档里：它只属于焦点、链接、画布选择框。
 */
export type Variant = 'ghost' | 'secondary' | 'primary' | 'danger'
/**
 * 高度只有一档 28px（h-7）——Tavotto 的控件密度是「紧凑工具」那一档，
 * 输入框 / 下拉 / 树行 / 图标钮全都是它，按钮不另起炉灶。
 * sm / md 只差内边距（字号同为 12——同一高度的控件只有一种字号，2026-09-15 审计 A02）；
 * icon / icon-sm 是 28×28 的方钮，只差图标档；icon-xs 是 20×20 的行内小钮（标题行里的 ?、
 * 搜索框的清除、通知的 ×），此前四处各手写一遍——它是 28 之外唯一的一档，只给行内。
 */
export type Size = 'sm' | 'md' | 'icon' | 'icon-sm' | 'icon-xs'

const VARIANTS: Record<Variant, string> = {
  // data-[state=open]：作为菜单 / 弹层触发器时（Radix Trigger asChild 把 data-state 落在这颗钮上）
  // 浮层开着的期间底色常驻，浮层与它的按钮才看得出因果（2026-09-15 审计 B01）
  ghost: 'text-ink hover:bg-surface-hover active:bg-surface-active data-[state=open]:bg-surface-active',
  // 次按钮 = 灰底胶囊、无边线（2026-09-30 重设计）：白面板与灰桌面上都是「比底深一档」，
  // 与黑色主按钮一深一浅两档，不再靠一圈描边说「我是按钮」
  secondary:
    'bg-surface-hover text-ink hover:bg-surface-active active:bg-selected data-[state=open]:bg-surface-active',
  // 主动作用近黑色；蓝色只留给选择 / 焦点 / 链接
  primary: 'bg-ink text-white hover:bg-ink/90 active:bg-ink/95',
  danger: 'text-danger hover:bg-danger-subtle active:bg-danger/15',
}

export const BUTTON_SIZES: Record<Size, string> = {
  // 带字的按钮一律胶囊（2026-09-30 重设计，参照 OpenBitFun）；图标钮是圆角方块（md 10）
  sm: 'h-7 px-2.5 gap-1 text-sm rounded-full',
  md: 'h-7 px-3 gap-1.5 text-sm rounded-full',
  icon: 'h-7 w-7 rounded-md',
  // 图标点击区不小于 28px；两档只差图标字号
  'icon-sm': 'h-7 w-7 rounded-md',
  // 20px 行内小钮：圆角仍是 6（Claude 的 20px 行钮圆角 5，不降到 3）
  'icon-xs': 'h-5 w-5 rounded-sm',
}

/**
 * 按钮外观的唯一出处。`Button` 自己用它（独立成文件：组件文件只导出组件，fast refresh 才生效）；**只有**「长得像按钮的链接」（`<a href>`，
 * 如 /try 的「下载桌面版」）直接拿它给 `<a>` 上外观——导航就该是链接，
 * 不为了外观改成 `<button onClick={location.assign}>`，也不为它再手写一套按钮类名。
 */
export function buttonClass({
  variant = 'ghost',
  size = 'md',
  active = false,
  className,
}: { variant?: Variant; size?: Size; active?: boolean; className?: string } = {}) {
  return cn(
    'inline-flex shrink-0 select-none items-center justify-center whitespace-nowrap',
    'transition-[background-color,border-color,color] duration-fast',
    'focus-visible:focus-ring outline-none',
    // 不用 pointer-events-none：那会连 not-allowed 光标和 tooltip 一起吞掉，
    // 点击本来就被原生 disabled 挡住了
    // 禁用态全站一档：opacity-40 + not-allowed（foundation.test 守着）
    'disabled:cursor-not-allowed disabled:opacity-40',
    VARIANTS[variant],
    BUTTON_SIZES[size],
    // 按下 / 选中态：轻 tint + 字重，不靠深灰块
    active && variant !== 'primary' && 'bg-selected font-medium text-ink hover:bg-selected',
    className,
  )
}
