import { forwardRef, useCallback, useRef, useState, type ButtonHTMLAttributes } from 'react'
import { LoaderCircle } from 'lucide-react'
import { ICON_SIZE, type IconSizeStep } from './Icon'
import { cn } from '@/lib/utils'
import { Tip } from './Tooltip'

/**
 * 按钮层级只有四档（docs/ux/DESIGN_CONSTITUTION.md 第七节）：
 *   primary   近黑填色。每个上下文最多一个（顶栏=导出、助手=发送、弹窗=确认）。
 *   secondary 细边框 + 白底。工具类操作的默认形态。
 *   ghost     无边无底，hover 才浮出一层 surface-hover。工具栏、行内动作、图标钮。
 *   danger    红字 ghost。只给不可逆操作。
 * 蓝色不出现在任何一档里：它只属于焦点、链接、画布选择框。
 */
type Variant = 'ghost' | 'secondary' | 'primary' | 'danger'
/**
 * 高度只有一档 28px（h-7）——Tavotto 的控件密度是「紧凑工具」那一档，
 * 输入框 / 下拉 / 树行 / 图标钮全都是它，按钮不另起炉灶。
 * sm / md 只差字号与内边距；icon / icon-sm 是 28×28 的方钮，只差图标档。
 */
type Size = 'sm' | 'md' | 'icon' | 'icon-sm'

export interface ButtonProps
  extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'onClick'> {
  variant?: Variant
  size?: Size
  active?: boolean
  /** 外部控制的忙碌态；返回 Promise 的 onClick 会自动进入忙碌，无需自己传 */
  loading?: boolean
  /** 忙碌时替换的文案；给了它按钮就按两种文案里较宽的那个定宽，不会跳动 */
  loadingLabel?: string
  /** 返回 Promise 时按钮自动置忙并挡住重复提交，直到它 settle */
  onClick?: (e: React.MouseEvent<HTMLButtonElement>) => void | Promise<unknown>
}

const VARIANTS: Record<Variant, string> = {
  ghost: 'text-ink hover:bg-surface-hover active:bg-surface-active',
  secondary:
    'border border-border bg-surface text-ink hover:border-border-strong hover:bg-surface-2 active:bg-surface-active',
  // 主动作用近黑色；蓝色只留给选择 / 焦点 / 链接
  primary: 'bg-ink text-white hover:bg-ink/90 active:bg-ink/95',
  danger: 'text-danger hover:bg-danger-subtle active:bg-danger/15',
}

const SIZES: Record<Size, string> = {
  sm: 'h-7 px-2 gap-1 text-xs rounded-sm',
  md: 'h-7 px-2.5 gap-1.5 text-sm rounded-sm',
  icon: 'h-7 w-7 rounded-sm',
  // 图标点击区不小于 28px；两档只差图标字号
  'icon-sm': 'h-7 w-7 rounded-sm',
}

// 忙碌指示器跟按钮里其它图标同一档：有文字的按钮 sm，纯图标按钮 md
const SPINNER: Record<Size, IconSizeStep> = { sm: 'sm', md: 'sm', icon: 'md', 'icon-sm': 'sm' }

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  {
    className,
    variant = 'ghost',
    size = 'md',
    active = false,
    type = 'button',
    loading,
    loadingLabel,
    disabled,
    onClick,
    children,
    ...props
  },
  ref,
) {
  const [pending, setPending] = useState(false)
  // 卸载后不再 setState：异步动作常以关闭弹窗收尾，按钮可能先没了
  const alive = useRef(true)
  const busy = loading || pending
  const blocked = busy || disabled

  const handleClick = useCallback(
    (e: React.MouseEvent<HTMLButtonElement>) => {
      if (blocked || !onClick) return
      const result = onClick(e)
      if (!(result instanceof Promise)) return
      setPending(true)
      alive.current = true
      void result.finally(() => {
        if (alive.current) setPending(false)
      })
    },
    [blocked, onClick],
  )

  // 有 loadingLabel 时把两份文案叠在同一个网格单元里：
  // 宽度取两者较大值，切换忙碌态不会让按钮（以及它旁边的东西）跳一下
  const content = loadingLabel ? (
    <span className="grid place-items-center">
      <span
        className={cn(
          'col-start-1 row-start-1 inline-flex items-center',
          SIZES[size].includes('gap-1.5') ? 'gap-1.5' : 'gap-1',
          busy && 'invisible',
        )}
      >
        {children}
      </span>
      <span
        className={cn(
          'col-start-1 row-start-1 inline-flex items-center',
          SIZES[size].includes('gap-1.5') ? 'gap-1.5' : 'gap-1',
          !busy && 'invisible',
        )}
        aria-hidden={!busy}
      >
        <LoaderCircle size={ICON_SIZE[SPINNER[size]]} className="animate-spin" />
        {loadingLabel}
      </span>
    </span>
  ) : (
    <>
      {busy && <LoaderCircle size={ICON_SIZE[SPINNER[size]]} className="animate-spin" />}
      {children}
    </>
  )

  return (
    <button
      ref={(node) => {
        alive.current = node != null
        if (typeof ref === 'function') ref(node)
        else if (ref) ref.current = node
      }}
      type={type}
      disabled={blocked}
      aria-busy={busy || undefined}
      data-active={active || undefined}
      onClick={handleClick}
      className={cn(
        'inline-flex shrink-0 cursor-pointer select-none items-center justify-center whitespace-nowrap',
        'transition-[background-color,border-color,color] duration-fast',
        'focus-visible:focus-ring outline-none',
        // 不用 pointer-events-none：那会连 not-allowed 光标和 tooltip 一起吞掉，
        // 点击本来就被原生 disabled 挡住了
        'disabled:cursor-not-allowed disabled:opacity-35',
        VARIANTS[variant],
        SIZES[size],
        // 按下 / 选中态：轻 tint + 字重，不靠深灰块
        active && variant !== 'primary' && 'bg-selected font-medium text-ink hover:bg-selected',
        className,
      )}
      {...props}
    >
      {content}
    </button>
  )
})

/**
 * 只有图标的按钮：Pin / Close / Copy / Refresh / More… 全部走它。
 *
 * 28×28、16px 图标（`iconSize="sm"` 时 14px）、默认透明、hover 才浮出
 * surface-hover、按下 / 选中是 selected 那一档轻 tint——不用大块灰底。
 * **名字与气泡同一份**：`label` 既是 `aria-label` 也是 tooltip 文案，两者不会分叉
 * （审计 T39 担心的正是分叉）。`tip={false}` 只在已经有可见文字说明它的场合用，
 * 名字照样给。
 */
export interface IconButtonProps extends Omit<ButtonProps, 'size' | 'aria-label' | 'children'> {
  label: string
  /** 图标档：默认 md（16px）；与 11–12px 文字并排的小钮用 sm（14px） */
  iconSize?: 'md' | 'sm'
  /**
   * 气泡：默认显示 `label`；传 false 关掉（旁边已有可见文字时）；传字符串则气泡说
   * 另一句（只给「名字是动作、气泡讲当前状态」的开关钮，如钉住 / 宽高比锁）。
   */
  tip?: boolean | string
  shortcut?: string
  side?: 'top' | 'bottom' | 'left' | 'right'
  children: React.ReactNode
}

export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(function IconButton(
  { label, iconSize = 'md', tip = true, shortcut, side, children, ...props },
  ref,
) {
  const btn = (
    <Button ref={ref} size={iconSize === 'md' ? 'icon' : 'icon-sm'} aria-label={label} {...props}>
      {children}
    </Button>
  )
  return tip ? (
    <Tip label={typeof tip === 'string' ? tip : label} shortcut={shortcut} side={side}>
      {btn}
    </Tip>
  ) : (
    btn
  )
})
