import { cn } from '@/lib/utils'

export type ListRowSize = 'sm' | 'md' | 'lg'

/** 三档行高：sm 28（树行 / 紧凑列表）· md 44（两行：名字 + meta）· lg 52（带缩略图的行） */
const SIZE: Record<ListRowSize, string> = {
  sm: 'h-7 text-xs',
  md: 'min-h-11 py-1.5 text-sm',
  lg: 'min-h-13 py-1.5 text-sm',
}

/**
 * 列表 / 树的一行（元素树、图层树、素材列表……）的类名。不是组件：各处的行
 * 元素类型、role、拖拽与键盘处理都不同，共用的只是**看起来是同一种行**：
 * 三档高（`size`）、8px 圆角（md）、左右各让 4px、hover 是 surface-hover 那一档、
 * 焦点环是内凹的（`focus-ring-inset`：行贴着邻行，外扩的环会盖到上下行并触到抽屉边）、
 * 选中是 selected 轻 tint + **600**（不靠深灰块；2026-10-07 设计审计 §2 / §10.3）、隐藏项压到 45%。
 * 行里的 meta 用 `rowMetaClass(selected)`——选中底上 ink-3 不到 4.5:1，选中时自动升 ink-2。
 * 内边距（pl / pr）由调用方按自己的缩进给。
 */
export function listRowClass({
  size = 'sm',
  selected = false,
  muted = false,
  hidden = false,
  dragging = false,
  tree = false,
  group = false,
}: {
  size?: ListRowSize
  selected?: boolean
  /** 分组行这类次级行：常态用 ink-2 */
  muted?: boolean
  hidden?: boolean
  /** 这一行正被拖着：原位淡到 `opacity-dragging`（压过 hidden 的 45%） */
  dragging?: boolean
  /**
   * 结构侧栏（元素树 / 图层树）变体（2026-10-16 UI 2.0 第二轮 04）：圆角 6；叶子 ink-2、
   * 分组头（`group`）500 + ink，选中仍是 selected 底 + 600 + ink。hover / 选中底沿用同一档 token。
   */
  tree?: boolean
  /** 树变体里的分组头（带子节点的行） */
  group?: boolean
} = {}): string {
  return cn(
    'group relative mx-1 flex cursor-default items-center gap-1 outline-none',
    tree ? 'rounded-sm' : 'rounded-md',
    SIZE[size],
    'transition-colors duration-fast focus-visible:focus-ring-inset',
    selected
      ? 'bg-selected font-semibold text-ink'
      : cn(
          tree ? (group ? 'font-medium text-ink' : 'text-ink-2') : muted ? 'text-ink-2' : 'text-ink',
          'hover:bg-surface-hover',
        ),
    dragging ? 'opacity-dragging' : hidden && 'opacity-45',
  )
}

/**
 * 行里的元信息（计数 / 路径 / 时间）：11px、400（不跟着选中行的 600 走）、等宽数字；
 * 选中时 ink-3 → ink-2（selected 底上 ink-3 只有 ≈4.4:1）。
 */
export function rowMetaClass(selected = false): string {
  return cn('text-xs font-normal tabular-nums', selected ? 'text-ink-2' : 'text-ink-3')
}

/**
 * 拖放落点线：2px accent 横条 + 左端 4px 圆点，画在行的上缘（before）或下缘（after）。
 * 给一个 `aria-hidden` 的空 span 用，行本身要 `relative`（listRowClass 已带）。
 * pos 为 null 时返回空串——调用点可以无条件渲染。
 */
export function dropLineClass(pos: 'before' | 'after' | null): string {
  if (!pos) return 'hidden'
  return cn(
    'pointer-events-none absolute inset-x-1 z-sticky h-0.5 rounded-full bg-accent',
    pos === 'before' ? '-top-px' : '-bottom-px',
    'before:absolute before:-left-0.5 before:top-1/2 before:size-1 before:-translate-y-1/2 before:rounded-full before:bg-accent',
  )
}
