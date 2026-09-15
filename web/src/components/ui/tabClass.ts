import { cn } from '@/lib/utils'

/**
 * Tabs.tsx 的类名部分；单独成文件是为了让那边只导出组件（Fast refresh 的要求）。
 *
 * `TAB_UNDERLINE` 只剩画布标签栏在用（那边结构不同，只借视觉）；`TabList` 自己渲染
 * **一条**共享的下划线滑到当前页签（`slidingIndicator`，2026-09-14 二审 E2），页签本身不再各画一条。
 */
export const TAB_UNDERLINE = 'after:absolute after:bottom-0 after:h-0.5 after:rounded-full after:bg-ink'

/**
 * 选中 = 600 + ink + 那条滑过来的下划线（形状是第二重线索，不单靠颜色）；未选中 ink-3。
 * 600 是 2026-09-15 用户拍板（批次 A T8）；加粗带来的宽度抖动由 `Tab` 用 useLayoutEffect
 * 量出加粗宽度写成 min-width 挡住（二审 A4 担心的正是这个）。
 * 字号 12（type-body）：页签切换的是 12px 的内容，自己不能比内容小一号（2026-09-15 审计 A04）。
 */
export const tabClass = (active: boolean) =>
  cn(
    'relative h-full text-sm outline-none transition-colors focus-visible:focus-ring',
    active ? 'font-semibold text-ink' : 'text-ink-3 hover:text-ink-2',
  )
