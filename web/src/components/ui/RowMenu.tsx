import type { ReactNode } from 'react'
import { Ellipsis } from './icons'
import { ICON_SIZE } from './Icon'
import { IconButton } from './Button'
import { Menu, PointMenu } from './Menu'
import { cn } from '@/lib/utils'
import type { RowMenuState } from './useRowMenu'

/**
 * 行尾的「⋯」+ 它的菜单（2026-10-07 设计审计 §10.3）。与 `useRowMenu` 配对：
 *
 * ```tsx
 * const menu = useRowMenu()
 * <li {...menu.rowProps} className={listRowClass()}>
 *   …名字…
 *   <RowMenu state={menu} label={actionsLabel}>
 *     <MenuItem icon={Pencil} onSelect={rename}>{renameLabel}</MenuItem>
 *   </RowMenu>
 * </li>
 * ```
 *
 * - **同一份清单三个入口**：⋯、右键（贴着光标的 `PointMenu`）、⇧F10 / ContextMenu 键（从 ⋯ 垂下）。
 * - ⋯ 只在行 hover / focus-within / 菜单开着时看得见（`visible="always"` 常显）；**行聚焦时它在 Tab 顺序里**
 *   （tabIndex 0），行没焦点时 -1——一列 50 行不是 50 个 Tab 停靠点，焦点在行上时下一下 Tab 就到它。
 *   行元素要带 `group`（`listRowClass` 已带）。宿主自己不可聚焦、又不在一列里（当前项目卡）时给 `tabbable`，
 *   ⋯ 常驻 Tab 顺序，⇧F10 在它上面照样冒泡到宿主开菜单。
 * - 菜单项用 `MenuItem icon=`（图标 ink-2 由原语给）。
 */
export function RowMenu({
  state,
  label,
  children,
  width = 200,
  visible = 'hover',
  tabbable = false,
  className,
  ...rest
}: {
  state: RowMenuState
  /** ⋯ 的可达名与气泡 */
  label: string
  children: ReactNode
  width?: number
  visible?: 'hover' | 'always'
  /**
   * ⋯ 常驻 Tab 顺序（tabIndex 0）。只给**不在任何一列里、自己也不可聚焦**的宿主——当前项目卡：
   * 它没有主操作、不是漫游行，⋯ 是它唯一的键盘入口（Codex #832）。列表行别用：一列一个 Tab 停靠点
   */
  tabbable?: boolean
  className?: string
} & Record<`data-${string}`, string | number | boolean | undefined>) {
  if (!state.enabled) return null
  const { mode, setMode, focusWithin } = state
  const point = mode && typeof mode === 'object' ? mode : null
  return (
    <>
      <Menu
        align="end"
        width={width}
        open={mode === 'button'}
        onOpenChange={(v) => setMode(v ? 'button' : null)}
        trigger={
          <IconButton
            {...rest}
            data-row-menu-trigger
            iconSize="sm"
            label={label}
            tabIndex={tabbable || focusWithin || mode === 'button' ? 0 : -1}
            className={cn(
              'shrink-0 text-ink-3 hover:text-ink',
              visible === 'hover' &&
                'opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 focus-visible:opacity-100 data-[state=open]:opacity-100',
              className,
            )}
          >
            <Ellipsis size={ICON_SIZE.sm} />
          </IconButton>
        }
      >
        {children}
      </Menu>
      {point && (
        <PointMenu
          open
          onOpenChange={(v) => !v && setMode(null)}
          at={point}
          ariaLabel={label}
          width={width}
          data-row-point-menu
        >
          {children}
        </PointMenu>
      )}
    </>
  )
}
