import { cn } from '@/lib/utils'

/**
 * 列表 / 树的一行（元素树、图层树、素材列表……）的类名。不是组件：各处的行
 * 元素类型、role、拖拽与键盘处理都不同，共用的只是**看起来是同一种行**：
 * 28px 高、6px 圆角、左右各让 4px、hover 是 surface-hover 那一档、
 * 选中是 selected 轻 tint + 字重（不靠深灰块）、隐藏项压到 45%。
 * 内边距（pl / pr）由调用方按自己的缩进给。
 */
export function listRowClass({
  selected = false,
  muted = false,
  hidden = false,
}: {
  selected?: boolean
  /** 分组行这类次级行：常态用 ink-2 */
  muted?: boolean
  hidden?: boolean
} = {}): string {
  return cn(
    'group relative mx-1 flex h-7 cursor-default items-center gap-1 rounded-sm text-xs outline-none',
    'transition-colors duration-fast focus-visible:focus-ring',
    selected
      ? 'bg-selected font-medium text-ink'
      : cn(muted ? 'text-ink-2' : 'text-ink', 'hover:bg-surface-hover'),
    hidden && 'opacity-45',
  )
}
