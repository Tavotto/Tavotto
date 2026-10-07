/**
 * 内嵌工作台（网站 /try 与 Codex 里的 MCP 画布）共用的 44px 顶栏（2026-10-07 设计审计 §10.4 P2）。
 *
 * 左：20px 品牌图形标 + 名字（/try 是产品名 = 回站链接，MCP 是图名）+ 一行 type-meta 的标题 / 元信息；
 * 右：调用方的控件（`children`）。顶栏坐在灰色桌面上、不画底边线——与桌面版顶栏同一种关系
 * （宪法第二十五节）：下面的白色工作面板自己就是边界。
 *
 * 两个内嵌面此前各写一条 header，高度相同、内容排法各异；同一类东西只有一份实现。
 */
import type { ReactNode } from 'react'
import { BrandMark } from '@/components/ui/BrandMark'
import { cn } from '@/lib/utils'

/**
 * 坐在灰色桌面上的侧栏：`--drawer-bg` = bg，可编辑框底换成白（field 比桌面还浅，放在灰上就看不出
 * 是个框）——与桌面版 LeftPanel 停靠态同一组 token 改写，fieldBox 原语不动。
 */
export const DESK_DRAWER =
  'bg-bg [--drawer-bg:var(--color-bg)] [--color-field-hover:var(--color-surface-2)] [--color-field:var(--color-surface)]'

/** 白色工作面板里的侧栏（属性页）：`--drawer-bg` = surface，不画分隔线 */
export const PANEL_DRAWER = 'bg-surface [--drawer-bg:var(--color-surface)]'

/** 白色工作面板（画布 + 属性页）：灰桌面上的一块圆角白面，与桌面版 `data-work-panel` 同形（不带投影） */
export const WORK_PANEL = 'mx-2 mb-2 flex min-h-0 min-w-0 flex-1 overflow-hidden rounded-panel bg-surface'

export function WidgetHeader({
  name,
  title,
  brand = true,
  className,
  children,
}: {
  /** 名字：文本或一枚链接（/try 的产品名回站） */
  name: ReactNode
  /** 名字后面那行 type-meta：页面名 / 规范与尺寸 */
  title?: ReactNode
  /** 品牌图形标；名字本身已经是产品名链接、标在链接里时传 false */
  brand?: boolean
  className?: string
  /** 右侧控件 */
  children?: ReactNode
}) {
  return (
    <header data-widget-header className={cn('flex h-11 shrink-0 items-center gap-2 bg-bg px-3', className)}>
      {brand && <BrandMark size={20} />}
      <span className="flex min-w-0 shrink items-baseline gap-2">
        <span className="min-w-0 truncate text-base font-medium text-ink">{name}</span>
        {title != null && <span className="type-meta min-w-0 truncate">{title}</span>}
      </span>
      {children}
    </header>
  )
}
