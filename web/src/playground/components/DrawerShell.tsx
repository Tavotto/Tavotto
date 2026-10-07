/**
 * 工作台左侧抽屉的外壳（/try 编辑态的元素树）：与桌面版 `LeftPanel` 同一副骨架——
 * 宽 280（`LEFT_MIN`，应用抽屉的下限；此前 224 比应用里最窄的抽屉还窄）、36px 标题行
 * （type-section 标题 + `DrawerCount` 计数）、坐在灰色桌面上（`--drawer-bg` = bg）、不画分隔线。
 *
 * 只借 LeftPanel 的样式，不借它本身：LeftPanel 绑着图标轨道、钉住、宽度拖拽这些桌面工作台的
 * 状态，playground 一个都没有（2026-10-07 设计审计 §10.4）。
 */
import type { ReactNode } from 'react'
import { DrawerCount } from '@/components/left/DrawerCount'
import { DESK_DRAWER } from '@/embedded/WidgetHeader'
import { cn } from '@/lib/utils'
import { LEFT_MIN } from '@/store/uiStore'

export function DrawerShell({
  title,
  count,
  countLabel,
  className,
  children,
  ...rest
}: {
  title: string
  /** 标题旁的计数（只有数字，单位进读屏）；不给或 0 不显示 */
  count?: number
  countLabel?: string
  className?: string
  children: ReactNode
} & Record<`data-${string}`, string | number | boolean | undefined>) {
  return (
    <aside
      aria-label={title}
      {...rest}
      data-drawer-shell
      style={{ width: LEFT_MIN }}
      className={cn('shrink-0 flex-col', DESK_DRAWER, className)}
    >
      <div className="flex h-9 shrink-0 items-center gap-1.5 px-3">
        <h2 className="type-section">{title}</h2>
        {!!count && <DrawerCount value={count} label={countLabel} />}
      </div>
      <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">{children}</div>
    </aside>
  )
}
