import * as DM from '@radix-ui/react-dropdown-menu'
import { Check, ChevronRight } from 'lucide-react'
import { useState, type ComponentType, type ReactElement, type ReactNode } from 'react'
import { cn } from '@/lib/utils'

/** 浮层外壳样式：菜单本体与子菜单共用一份，别各抄一遍 */
const CONTENT_CLASS = cn(
  'z-50 rounded-md border border-border bg-surface p-1 shadow-pop',
  'data-[state=open]:animate-pop-in data-[state=closed]:animate-pop-out',
)

/** 一条菜单项的样式：`MenuItem` 与子菜单的触发项共用 */
const ITEM_CLASS = cn(
  'flex min-h-7 cursor-default select-none items-center gap-2 rounded-sm px-2 py-1 text-xs outline-none',
  'data-[highlighted]:bg-ink/[.055] data-[disabled]:opacity-35',
)

export function Menu({
  trigger,
  children,
  align = 'start',
  width = 200,
}: {
  trigger: ReactElement
  children: ReactNode
  align?: 'start' | 'center' | 'end'
  width?: number
}) {
  return (
    <DM.Root>
      <DM.Trigger asChild>{trigger}</DM.Trigger>
      <DM.Portal>
        <DM.Content
          align={align}
          sideOffset={6}
          style={{ minWidth: width }}
          className={cn(
            CONTENT_CLASS,
            // 从触发器那个角展开，而不是从自己中心——菜单与按钮的因果关系才看得出来
            'origin-[var(--radix-dropdown-menu-content-transform-origin)]',
          )}
        >
          {children}
        </DM.Content>
      </DM.Portal>
    </DM.Root>
  )
}

/**
 * 贴着一个**点**（光标）打开的菜单：右键菜单的外壳。
 *
 * Radix 的菜单只认「触发器」这一个锚，所以锚是一个钉在 `at` 处的零尺寸元素
 * （`aria-hidden`、不可 Tab）。键盘方向键 / Home / End / 首字母跳转、子菜单、
 * `role="menu"` + `menuitem`、越界翻转、Esc、点外部关闭全部由 Radix 负责——
 * 自己写一份「不完整的 submenu」正是这层要避免的。
 *
 * 三条与画布共处的纪律：
 *
 * * **`modal={false}`**：不给 body 套 `pointer-events: none`。点菜单外面 = 关掉
 *   本菜单，**事件照常落到画布上**——在另一个对象上右键会直接开出它的菜单，
 *   与从前手写的弹层一致；模态的话那次右键只会关掉旧菜单、什么都不开。
 * * **键盘事件不出菜单**：Esc 在 Radix 的 document 捕获层就止步，全局快捷键
 *   （Esc 逐层退出、V/T/A/R 换工具、Delete）看不到菜单里的按键——菜单里按
 *   首字母跳转时，画布不能跟着换工具。
 * * **关闭后焦点不落在那个零尺寸锚上**：Radix 默认把焦点还给触发器，而锚随菜单
 *   一起卸载，WebKit 上焦点掉进已消失的节点之后 Tab 双向都不动（`lib/focusRescue`
 *   的实测）。这里改成还给打开前的焦点元素（还活着才还），否则不动。
 */
export function PointMenu({
  open,
  onOpenChange,
  at,
  ariaLabel,
  width = 208,
  children,
  ...rest
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  at: { x: number; y: number }
  ariaLabel: string
  width?: number
  children: ReactNode
} & Record<`data-${string}`, string | number | boolean | undefined>) {
  // 打开前谁有焦点，**只在挂载那一刻**取一次：之后每次重渲染时活动元素都是菜单自己
  const [before] = useState(() => (typeof document !== 'undefined' ? document.activeElement : null))
  return (
    <DM.Root open={open} onOpenChange={onOpenChange} modal={false}>
      <DM.Trigger asChild>
        <span
          aria-hidden
          tabIndex={-1}
          style={{ position: 'fixed', left: at.x, top: at.y, width: 0, height: 0, pointerEvents: 'none' }}
        />
      </DM.Trigger>
      <DM.Portal>
        <DM.Content
          {...rest}
          role="menu"
          aria-label={ariaLabel}
          side="bottom"
          align="start"
          sideOffset={2}
          collisionPadding={8}
          loop
          style={{ minWidth: width }}
          className={cn(CONTENT_CLASS, 'origin-top-left')}
          onContextMenu={(e) => e.preventDefault()}
          onKeyDown={(e) => e.stopPropagation()}
          onEscapeKeyDown={(e) => e.stopPropagation()}
          onCloseAutoFocus={(e) => {
            e.preventDefault()
            if (
              before instanceof HTMLElement &&
              before !== document.body &&
              before.isConnected
            ) {
              before.focus({ preventScroll: true })
            }
          }}
        >
          {children}
        </DM.Content>
      </DM.Portal>
    </DM.Root>
  )
}

export function MenuItem({
  children,
  shortcut,
  onSelect,
  disabled,
  danger,
  reason,
  icon: Icon,
  ...rest
}: {
  children: ReactNode
  shortcut?: string
  onSelect?: () => void
  disabled?: boolean
  danger?: boolean
  /**
   * 不可用的**原因**，作为第二行常驻在项里（不是 tooltip：禁用项收不到指针事件，
   * 而且 tooltip 不能是唯一的可访问说明）。给了它通常也给 `disabled`。
   */
  reason?: string
  icon?: ComponentType<{ size?: number; className?: string }>
} & Record<`data-${string}`, string | number | boolean | undefined>) {
  return (
    <DM.Item
      {...rest}
      disabled={disabled}
      onSelect={onSelect}
      className={cn(ITEM_CLASS, danger ? 'text-danger' : 'text-ink')}
    >
      {Icon && <Icon size={12} className="shrink-0 text-ink-2" />}
      <span className="flex min-w-0 flex-1 flex-col">
        <span className="truncate">{children}</span>
        {reason && <span className="truncate text-[11px] leading-4 text-ink-3">{reason}</span>}
      </span>
      {shortcut && <span className="shrink-0 font-mono text-xs text-ink-3">{shortcut}</span>}
    </DM.Item>
  )
}

/**
 * 子菜单：触发项 + 侧边展开的内容。方向键 → / Enter 打开、← / Esc 收回，
 * 靠右放不下自动翻到左边（Radix `avoidCollisions`）。
 */
export function MenuSub({
  label,
  children,
  icon: Icon,
  disabled,
  ...rest
}: {
  label: ReactNode
  children: ReactNode
  icon?: ComponentType<{ size?: number; className?: string }>
  disabled?: boolean
} & Record<`data-${string}`, string | number | boolean | undefined>) {
  return (
    <DM.Sub>
      <DM.SubTrigger
        {...rest}
        disabled={disabled}
        className={cn(ITEM_CLASS, 'text-ink data-[state=open]:bg-ink/[.055]')}
      >
        {Icon && <Icon size={12} className="shrink-0 text-ink-2" />}
        <span className="min-w-0 flex-1 truncate">{label}</span>
        <ChevronRight size={12} className="shrink-0 text-ink-3" aria-hidden />
      </DM.SubTrigger>
      <DM.Portal>
        <DM.SubContent
          sideOffset={4}
          alignOffset={-5}
          collisionPadding={8}
          loop
          style={{ minWidth: 184 }}
          className={CONTENT_CLASS}
          onKeyDown={(e) => e.stopPropagation()}
          // Esc 落在子菜单上时是子菜单那一层在处理（Radix 只让最上层的 layer 接 Esc，
          // 并且它会把整个菜单一起关掉）；不在这里止步的话事件照样冒到 window，
          // 全局 Esc 会顺手清空选区——真浏览器抓到的，jsdom 里用方向键收回没撞见
          onEscapeKeyDown={(e) => e.stopPropagation()}
        >
          {children}
        </DM.SubContent>
      </DM.Portal>
    </DM.Sub>
  )
}

export function MenuCheckItem({
  children,
  checked,
  onSelect,
}: {
  children: ReactNode
  checked: boolean
  onSelect: () => void
}) {
  return (
    <DM.CheckboxItem
      checked={checked}
      onSelect={(e) => {
        e.preventDefault()
        onSelect()
      }}
      className={cn(
        'flex h-7 cursor-default select-none items-center rounded-sm pl-6 pr-2 text-xs text-ink outline-none',
        'relative data-[highlighted]:bg-ink/[.055]',
      )}
    >
      <DM.ItemIndicator className="absolute left-1.5 flex items-center">
        <Check size={12} />
      </DM.ItemIndicator>
      {children}
    </DM.CheckboxItem>
  )
}

export const MenuSeparator = () => <DM.Separator className="my-1 h-px bg-border" />

export const MenuLabel = ({ children }: { children: ReactNode }) => (
  <DM.Label className="px-2 py-1 text-xs font-medium uppercase tracking-[.06em] text-ink-3">
    {children}
  </DM.Label>
)

/** 不大写、不加字距的普通说明行（对象名 / 「已选 N 个」这类用户内容不该被大写） */
export const MenuHeading = ({ children, ...rest }: { children: ReactNode } & Record<`data-${string}`, string | number | boolean | undefined>) => (
  <DM.Label {...rest} className="truncate px-2 py-1 text-xs text-ink-3" title={typeof children === 'string' ? children : undefined}>
    {children}
  </DM.Label>
)
