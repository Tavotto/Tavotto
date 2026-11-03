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

/**
 * 分段式页签（`TabList variant="segmented"`）：整格可点；选中字 600 + ink，白色 thumb 由 TabList 画在下面；
 * 未选中 ink-2（槽是 ink 4% 叠在面板上，ink-3 对比度不够，同 `Segmented`）。字多了在格内省略号截断。
 */
export const segmentedTabClass = (active: boolean) =>
  cn(
    'relative block min-w-0 truncate rounded-full px-0.5 text-center text-sm outline-none transition-colors duration-fast focus-visible:focus-ring',
    active ? 'font-semibold text-ink' : 'text-ink-2 hover:text-ink',
  )
