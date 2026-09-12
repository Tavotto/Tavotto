import type { ReactNode } from 'react'
import { ChevronRight, type LucideIcon } from 'lucide-react'
import { cn } from '@/lib/utils'
import { ICON_SIZE } from './Icon'

/**
 * 树行（图层树、图内元素树）的固定列。行本身仍由调用方拼（`listRowClass` 给外观，
 * role / 键盘 / 拖拽各处不同），这里只保证**列**在每棵树里落在同一条竖线上：
 *
 *   [缩进 8 + 14 × depth][chevron 16][类型图标 16] 标签 …… [计数，右对齐]
 *
 * 层级只靠缩进与 chevron 表达，不靠行间留白；叶子行留一个空的 chevron 列，
 * 同一层的图标与文字才对得齐。
 */
export const TREE_INDENT = 14

/** 行首内边距：基础 8px + 每层 14px。写成 style，Tailwind 生成不了按 depth 变的类名 */
export const treeIndent = (depth: number, base = 8) => ({ paddingLeft: base + depth * TREE_INDENT })

/**
 * 折叠箭头列。`expanded` 缺省 = 叶子，只占位；给了 `onToggle` 是一颗不可 Tab 的按钮
 * （行本身才是焦点落点，方向键 ← → 走行的键盘处理）；不给的话只是指示物，整行才是开关。
 */
export function TreeChevron({
  expanded,
  onToggle,
  label,
}: {
  expanded?: boolean
  onToggle?: () => void
  /** 按钮形态时必填：展开 / 折叠的可达名 */
  label?: string
}) {
  if (expanded === undefined) return <span className="w-4 shrink-0" aria-hidden />
  const icon = (
    <ChevronRight
      size={ICON_SIZE.xs}
      aria-hidden
      className={cn('transition-transform duration-fast', expanded && 'rotate-90')}
    />
  )
  if (!onToggle) {
    return (
      <span className="flex h-4 w-4 shrink-0 items-center justify-center text-ink-3" aria-hidden>
        {icon}
      </span>
    )
  }
  return (
    <button
      type="button"
      tabIndex={-1}
      aria-label={label}
      onPointerDown={(e) => e.stopPropagation()}
      onClick={onToggle}
      className="flex h-4 w-4 shrink-0 items-center justify-center rounded-xs text-ink-3 outline-none hover:text-ink"
    >
      {icon}
    </button>
  )
}

/** 类型图标列：14px 图标坐在 16px 格里；选中行用墨色，其余 ink-3 */
export function TreeIcon({ icon: Icon, selected }: { icon: LucideIcon; selected?: boolean }) {
  return (
    <span className="flex h-4 w-4 shrink-0 items-center justify-center" aria-hidden>
      <Icon size={ICON_SIZE.sm} className={selected ? 'text-ink' : 'text-ink-3'} />
    </span>
  )
}

/** 行尾计数 / 元数据：右对齐、等宽数字、ink-3 */
export function TreeCount({ children, title }: { children: ReactNode; title?: string }) {
  return (
    <span className="ml-auto shrink-0 pl-2 type-meta tabular-nums" title={title}>
      {children}
    </span>
  )
}
