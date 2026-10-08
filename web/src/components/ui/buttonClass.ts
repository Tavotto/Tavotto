import { cn } from '@/lib/utils'

/**
 * 按钮层级（docs/ux/DESIGN_CONSTITUTION.md 第五节）：
 *   primary       近黑填色、surface 色字。每个上下文最多一个（顶栏=导出、助手=发送、弹窗=确认）。
 *   secondary     灰底胶囊、无边线（2026-09-30）。工具类操作的默认形态。
 *   ghost         无边无底，hover 才浮出一层 surface-hover。工具栏、行内动作、图标钮。
 *   danger        红字 ghost。行内 / 菜单旁的不可逆操作。
 *   danger-tinted 危险浅底胶囊：danger-surface 底 + danger-border 内描边 + danger-content 字 + 600。
 *                 **只给对话框页脚**的破坏性确认（「删除」「不保存」）——红字 ghost 比灰底「取消」还轻，
 *                 实心红又太吵（2026-10-07 设计审计 §10.2，用户拍板：永远不用实心红）。配 size="lg"。
 * 蓝色不出现在任何一档里：它只属于焦点、AI、画布选择框。
 */
export type Variant = 'ghost' | 'secondary' | 'primary' | 'danger' | 'danger-tinted'
/**
 * 高度两档：28px（h-7）是工具默认——输入框 / 下拉 / 树行 / 图标钮全都是它；
 * 32px（`lg`，h-8）只给对话框页脚的按钮、页面级 CTA、命令面板的输入行（2026-10-07 设计审计 §3.1）。
 * sm / md 只差内边距（字号同为 12——同一高度的控件只有一种字号，2026-09-15 审计 A02）；lg 是 13。
 * icon / icon-sm 是 28 的圆形图标钮，只差图标档；icon-lg 是 32 的圆钮；icon-xs 是 20 的行内小钮
 * （标题行里的 ?、搜索框的清除、通知的 ×）——它是唯一低于 28 的一档，只给行内。
 * 图标钮一律是圆（rounded-full，2026-10-07：发送键不再是胶囊堆里的方块）。
 */
export type Size = 'sm' | 'md' | 'lg' | 'icon' | 'icon-sm' | 'icon-lg' | 'icon-xs'

const VARIANTS: Record<Variant, string> = {
  // data-[state=open]：作为菜单 / 弹层触发器时（Radix Trigger asChild 把 data-state 落在这颗钮上）
  // 浮层开着的期间底色常驻，浮层与它的按钮才看得出因果（2026-09-15 审计 B01）
  ghost: 'text-ink hover:bg-surface-hover active:bg-surface-active data-[state=open]:bg-surface-active',
  // 次按钮 = 灰底胶囊、无边线（2026-09-30 重设计）：白面板与灰桌面上都是「比底深一档」，
  // 与黑色主按钮一深一浅两档，不再靠一圈描边说「我是按钮」
  secondary:
    'bg-surface-hover text-ink hover:bg-surface-active active:bg-selected data-[state=open]:bg-surface-active',
  // 主动作用近黑色；字是 surface 色而不是写死的白（暗色主题只换 token）
  primary: 'bg-ink text-surface hover:bg-ink/90 active:bg-ink/95',
  danger: 'text-danger hover:bg-danger-surface active:bg-danger-surface-hover',
  'danger-tinted':
    'bg-danger-surface text-danger-content font-semibold inset-ring inset-ring-danger-border hover:bg-danger-surface-hover active:bg-danger-surface-hover',
}

export const BUTTON_SIZES: Record<Size, string> = {
  // 带字的按钮一律胶囊（2026-09-30 重设计，参照 OpenBitFun）；图标钮是圆（2026-10-07）
  sm: 'h-7 px-2.5 gap-1 text-sm rounded-full',
  md: 'h-7 px-3 gap-1.5 text-sm rounded-full',
  lg: 'h-8 px-3.5 gap-1.5 text-base rounded-full',
  icon: 'h-7 w-7 rounded-full',
  // 图标点击区不小于 28px；两档只差图标字号
  'icon-sm': 'h-7 w-7 rounded-full',
  'icon-lg': 'h-8 w-8 rounded-full',
  'icon-xs': 'h-5 w-5 rounded-full',
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
