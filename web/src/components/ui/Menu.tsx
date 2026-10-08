import * as DM from '@radix-ui/react-dropdown-menu'
import { Check, ChevronRight } from './icons'
import { ICON_SIZE } from './Icon'
import { useRef, useState, type ButtonHTMLAttributes, type ComponentType, type ReactElement, type ReactNode } from 'react'
import { cn } from '@/lib/utils'

/** 浮层外壳样式：菜单本体与子菜单共用一份，别各抄一遍 */
const CONTENT_CLASS = cn(
  'z-popover rounded-lg bg-surface p-1 shadow-pop',
  'data-[state=open]:animate-pop-in data-[state=closed]:animate-pop-out',
)

/** 一条菜单项的样式：`MenuItem` 与子菜单的触发项共用 */
const ITEM_CLASS = cn(
  'flex min-h-7 cursor-default select-none items-center gap-2 rounded-md px-2 py-1 text-sm outline-none',
  'data-[highlighted]:bg-surface-hover data-[disabled]:opacity-40',
)

export function Menu({
  trigger,
  children,
  align = 'start',
  width = 200,
  open,
  onOpenChange,
  onCloseAutoFocus,
  modal,
  pointerKeepsFocus,
}: {
  trigger: ReactElement
  children: ReactNode
  align?: 'start' | 'center' | 'end'
  width?: number
  /** 受控打开（`RowMenu` 用它让 ⇧F10 / 右键打开同一份菜单）；不给就是 Radix 自己管 */
  open?: boolean
  onOpenChange?: (open: boolean) => void
  /**
   * 关闭时焦点回到触发器之前调用；`preventDefault()` = 不还焦点。只给「菜单项打开了一个要接焦点的
   * 输入框」的场合（行内改名）：否则还给 ⋯ 的那一下就是那个输入框的 blur
   */
  onCloseAutoFocus?: (e: Event) => void
  /**
   * 默认模态（打开时页面其余部分不吃指针）。触发器自己还要认双击的（顶栏文档名：单击开菜单、双击改名，
   * 2026-10-07 设计审计 §10.1）给 false：第二下才落得回触发器上
   */
  modal?: boolean
  /**
   * 指针打开的菜单关掉后，焦点回到**打开前**那里（还活着才还，否则不动），不落到触发器上。触发器本身按下
   * 不拿焦点的（画布工具条：点完接着按方向键是在推画布上的对象）给 true——Radix 关菜单时默认把焦点还给
   * 触发器，按下时拦住的那次聚焦会在这里被补上，下一个方向键就被工具条吃掉了（Codex #833）。
   * 键盘打开的照旧回到触发器：键盘用户是从那里出发的。
   */
  pointerKeepsFocus?: boolean
}) {
  // 这一次是怎么打开的、打开前焦点在哪（`pointerKeepsFocus` 用）
  const opened = useRef<{ pointer: boolean; before: Element | null }>({ pointer: false, before: null })
  return (
    <DM.Root open={open} onOpenChange={onOpenChange} modal={modal}>
      <DM.Trigger
        asChild
        onPointerDown={() => {
          opened.current = { pointer: true, before: document.activeElement }
        }}
        onKeyDown={() => {
          opened.current = { pointer: false, before: null }
        }}
      >
        {trigger}
      </DM.Trigger>
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
          onCloseAutoFocus={
            onCloseAutoFocus || pointerKeepsFocus
              ? (e) => {
                  // 调用方先说话（行内改名要把焦点留给输入框）；它拦下了就不再还焦点
                  onCloseAutoFocus?.(e)
                  if (e.defaultPrevented || !pointerKeepsFocus) return
                  const { pointer, before } = opened.current
                  if (!pointer) return
                  e.preventDefault()
                  if (before instanceof HTMLElement && before !== document.body && before.isConnected) {
                    before.focus({ preventScroll: true })
                  }
                }
              : undefined
          }
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
  hint,
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
  /**
   * 第二行的**辨认信息**（同名项目的父目录之类）：与 `reason` 同一个位置、
   * 同一种字号，但它不意味着不可用。两者都给时先 reason 后 hint。
   */
  hint?: string
  icon?: ComponentType<{ size?: number; className?: string }>
} & Record<`data-${string}`, string | number | boolean | undefined>) {
  return (
    <DM.Item
      {...rest}
      disabled={disabled}
      onSelect={onSelect}
      className={cn(ITEM_CLASS, danger ? 'text-danger' : 'text-ink')}
    >
      {/* 菜单项图标一律 ink-2（2026-10-07 设计审计 §3.4）——比字淡一档，危险项跟着字走红；
          图标只经 `icon=` 传，不塞进 children 自己排（否则每处各挑一个颜色） */}
      {Icon && <Icon size={ICON_SIZE.sm} aria-hidden className={cn('shrink-0', danger ? 'text-danger' : 'text-ink-2')} />}
      <span className="flex min-w-0 flex-1 flex-col">
        <span className="truncate">{children}</span>
        {reason && <span className="truncate text-xs leading-4 text-ink-3">{reason}</span>}
        {hint && <span className="truncate font-mono text-xs leading-4 text-ink-3">{hint}</span>}
      </span>
      {shortcut && <span className="shrink-0 text-xs tabular-nums text-ink-3">{shortcut}</span>}
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
        className={cn(ITEM_CLASS, 'text-ink data-[state=open]:bg-surface-hover')}
      >
        {Icon && <Icon size={ICON_SIZE.sm} aria-hidden className="shrink-0 text-ink-2" />}
        <span className="min-w-0 flex-1 truncate">{label}</span>
        <ChevronRight size={ICON_SIZE.xs} className="shrink-0 text-ink-3" aria-hidden />
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
  ...rest
}: {
  children: ReactNode
  checked: boolean
  onSelect: () => void
} & Record<`data-${string}`, string | number | boolean | undefined>) {
  return (
    <DM.CheckboxItem
      {...rest}
      checked={checked}
      onSelect={(e) => {
        e.preventDefault()
        onSelect()
      }}
      className={cn(ITEM_CLASS, 'relative pl-6 text-ink')}
    >
      <DM.ItemIndicator className="absolute left-1.5 flex items-center">
        <Check size={ICON_SIZE.sm} />
      </DM.ItemIndicator>
      {children}
    </DM.CheckboxItem>
  )
}

/**
 * 单选组（`role="menuitemradio"`）：一组互斥取值，当前那个带勾。
 *
 * 与 `MenuCheckItem` 的区别不只是图标：那个是「开 / 关」，选中它不该关掉菜单
 * （所以它 `preventDefault`）；这个是「换成哪一个」，选完就该走人。语义也不同
 * ——屏幕阅读器会把一组 radio 念成「N 之 K」，把一排 checkbox 念成 N 个各自
 * 独立的开关。取值不一致（多选）时把 `value` 留空，一个都不勾。
 */
export function MenuRadioGroup({
  value,
  onValueChange,
  children,
  ...rest
}: {
  value?: string
  onValueChange: (value: string) => void
  children: ReactNode
} & Record<`data-${string}`, string | number | boolean | undefined>) {
  return (
    <DM.RadioGroup {...rest} value={value} onValueChange={onValueChange}>
      {children}
    </DM.RadioGroup>
  )
}

export function MenuRadioItem({
  value,
  children,
  icon: Icon,
  shortcut,
  disabled,
  reason,
  ...rest
}: {
  value: string
  children: ReactNode
  icon?: ComponentType<{ size?: number; className?: string }>
  /** 与 MenuItem 同一列的快捷键（标注工具的 A / R / O / L、缩放预设的 ⌘0） */
  shortcut?: string
  disabled?: boolean
  /** 不可用的原因，第二行常驻（与 `MenuItem.reason` 同一种写法；问题面板「当前图」没有当前图时） */
  reason?: string
} & Record<`data-${string}`, string | number | boolean | undefined>) {
  return (
    <DM.RadioItem {...rest} value={value} disabled={disabled} className={cn(ITEM_CLASS, 'relative pl-6 text-ink')}>
      <DM.ItemIndicator className="absolute left-1.5 flex items-center">
        <Check size={ICON_SIZE.sm} />
      </DM.ItemIndicator>
      {Icon && <Icon size={ICON_SIZE.sm} className="shrink-0 text-ink-2" aria-hidden />}
      <span className="flex min-w-0 flex-1 flex-col">
        <span className="truncate">{children}</span>
        {reason && <span className="truncate text-xs leading-4 text-ink-3">{reason}</span>}
      </span>
      {shortcut && <span className="shrink-0 text-xs tabular-nums text-ink-3">{shortcut}</span>}
    </DM.RadioItem>
  )
}

/** 菜单里能停焦点的项（`MenuField` 用它把方向键 / Tab 交还给菜单） */
const MENU_ITEM_SELECTOR = '[role="menuitem"]:not([data-disabled]),[role="menuitemradio"]:not([data-disabled]),[role="menuitemcheckbox"]:not([data-disabled])'

/**
 * 菜单里的一格**输入框**（缩放菜单顶上的倍率框，2026-10-07 设计审计 §10.1）。
 *
 * 不能是菜单里一个普通 `div`：Radix 菜单的方向键只在菜单项之间漫游、Tab 被它吞掉，键盘根本走不进去
 * （Codex #833 P2）。这一行本身是一个 Radix 菜单项（进漫游顺序；键盘打开菜单时它是第一项），但角色是
 * `group`（带名字）而不是 `menuitem`——菜单项里套可编辑控件是 nested-interactive。焦点落到这一行时转交给
 * 里面的输入框；在输入框里：
 *
 * * ↓ / Tab 回到下一条菜单项（菜单的语义优先，框里不拿方向键步进），↑ / ⇧Tab 留在框里；
 * * Enter 由输入框自己提交（`NumberField`），提交后焦点留在框里、菜单不关——看得见改完的读数；
 * * Esc 照常关菜单、焦点还给触发器（Radix 在 document 上接住 Esc，先于这里）。
 *
 * 指针一侧与普通 `div` 一样：悬停不抢焦点（不让 Radix 把焦点挪到这一行、移出时挪回菜单本体——那会把正在
 * 输入的框失焦提交），点进框里就是点进框里；点这一行不关菜单。
 */
export function MenuField({
  label,
  children,
  ...rest
}: { label: string; children: ReactNode } & Record<`data-${string}`, string | number | boolean | undefined>) {
  const rowRef = useRef<HTMLDivElement>(null)
  const field = () => rowRef.current?.querySelector<HTMLElement>('input, textarea') ?? null
  const nextItem = () => {
    const row = rowRef.current
    const menu = row?.closest('[role="menu"]')
    if (!row || !menu) return null
    return (
      [...menu.querySelectorAll<HTMLElement>(MENU_ITEM_SELECTOR)].find(
        (el) => row.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING,
      ) ?? null
    )
  }
  return (
    <DM.Item
      {...rest}
      ref={rowRef}
      role="group"
      aria-label={label}
      textValue=""
      className="px-1 pb-1 outline-none"
      // 点进框里不是「选中一项」：菜单不关
      onSelect={(e) => e.preventDefault()}
      onPointerMove={(e) => e.preventDefault()}
      onPointerLeave={(e) => e.preventDefault()}
      onFocus={(e) => {
        if (e.target === e.currentTarget) field()?.focus()
      }}
      onKeyDownCapture={(e) => {
        if (e.target === e.currentTarget) return
        const toMenu = e.key === 'ArrowDown' || (e.key === 'Tab' && !e.shiftKey)
        const stay = e.key === 'ArrowUp' || (e.key === 'Tab' && e.shiftKey)
        if (toMenu || stay) {
          e.preventDefault()
          e.stopPropagation()
          if (toMenu) nextItem()?.focus()
        } else if (e.key === 'Enter') {
          // 输入框提交后会自己失焦；焦点留在这一格，键盘用户不至于掉到 body 上
          const el = field()
          setTimeout(() => el?.isConnected && el.focus(), 0)
        }
      }}
    >
      {children}
    </DM.Item>
  )
}

export const MenuSeparator = () => <DM.Separator className="my-1 h-px bg-border" />

/**
 * 不在 Radix 菜单树里的「菜单项形状的按钮」：给 `role="dialog"` 的快捷编辑弹层这类
 * 里面有输入控件、套不进 DropdownMenu 的浮层用（2026-09-15 全面打磨 M2）。外观与
 * `MenuItem` 同一份 `ITEM_CLASS`，hover / 键盘焦点用同一档 surface-hover；语义与键盘
 * 由调用方的容器负责（它就是一颗普通 button）。
 */
export function MenuButton({
  icon: Icon,
  shortcut,
  danger,
  className,
  children,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  icon?: ComponentType<{ size?: number; className?: string }>
  shortcut?: string
  danger?: boolean
}) {
  return (
    <button
      type="button"
      {...rest}
      className={cn(
        ITEM_CLASS,
        'w-full text-left hover:bg-surface-hover focus-visible:bg-surface-hover',
        danger ? 'text-danger' : 'text-ink',
        className,
      )}
    >
      {Icon && <Icon size={ICON_SIZE.sm} aria-hidden className={cn('shrink-0', danger ? 'text-danger' : 'text-ink-2')} />}
      <span className="min-w-0 flex-1 truncate">{children}</span>
      {shortcut && <span className="shrink-0 text-xs tabular-nums text-ink-3">{shortcut}</span>}
    </button>
  )
}

/**
 * 菜单分组标题：12 / 400 / ink-3——比菜单项**淡**一档（OpenAI 14/400 secondary、Claude 12.5 muted 都
 * 是这么排的）。此前是 type-section（12/500/ink）压在 11px 的项上，组头比项还重（2026-09-15 审计 A03）。
 * `MenuHeading` 是同一样式带截断的版本，给对象名 / 「已选 N 个」这类用户内容。
 */
const LABEL_CLASS = 'px-2 py-1 text-sm text-ink-3'

export const MenuLabel = ({ children }: { children: ReactNode }) => (
  <DM.Label className={LABEL_CLASS}>{children}</DM.Label>
)

export const MenuHeading = ({ children, ...rest }: { children: ReactNode } & Record<`data-${string}`, string | number | boolean | undefined>) => (
  <DM.Label {...rest} className={cn(LABEL_CLASS, 'truncate')} title={typeof children === 'string' ? children : undefined}>
    {children}
  </DM.Label>
)
