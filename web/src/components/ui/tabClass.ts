import { cn } from '@/lib/utils'

/**
 * Tabs.tsx 的类名部分；单独成文件是为了让那边只导出组件（Fast refresh 的要求）。
 *
 * `TAB_UNDERLINE` 只剩画布标签栏在用（那边结构不同，只借视觉）；`TabList` 自己渲染
 * **一条**共享的下划线滑到当前页签（`slidingIndicator`，2026-09-14 二审 E2），页签本身不再各画一条。
 */
export const TAB_UNDERLINE = 'after:absolute after:bottom-0 after:h-0.5 after:rounded-full after:bg-ink'

/**
 * 选中 = ink 色 + 那条滑过来的下划线（形状是第二重线索，不单靠颜色）；未选中 ink-3。
 * 选中项**不再加粗**（二审 A4）：SF Pro 的 500 比 400 宽 2–3%，en-US 切页签时邻居会挪 1.5px；
 * 页签的子元素是图标 + 文字 + 计数 + 运行点的组合，没法用「隐藏的加粗影子」占位，
 * 而下划线已经把「不靠颜色」这一条守住了。
 */
export const tabClass = (active: boolean) =>
  cn(
    'relative h-full text-xs outline-none transition-colors focus-visible:focus-ring',
    active ? 'font-semibold text-ink' : 'text-ink-3 hover:text-ink-2',
  )
