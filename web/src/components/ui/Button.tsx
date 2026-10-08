import { forwardRef, useCallback, useRef, useState, type ButtonHTMLAttributes } from 'react'
import { LoaderCircle } from './icons'
import { ICON_SIZE, type IconSizeStep } from './Icon'
import { cn } from '@/lib/utils'
import { BUTTON_SIZES, buttonClass, type Size, type Variant } from './buttonClass'
import { Tip } from './Tooltip'

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

// 忙碌指示器跟按钮里其它图标同一档：有文字的按钮 sm，纯图标按钮 md
const SPINNER: Record<Size, IconSizeStep> = {
  sm: 'sm',
  md: 'sm',
  lg: 'sm',
  icon: 'md',
  'icon-sm': 'sm',
  'icon-lg': 'md',
  'icon-xs': 'sm',
}

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
          BUTTON_SIZES[size].includes('gap-1.5') ? 'gap-1.5' : 'gap-1',
          busy && 'invisible',
        )}
      >
        {children}
      </span>
      <span
        className={cn(
          'col-start-1 row-start-1 inline-flex items-center',
          BUTTON_SIZES[size].includes('gap-1.5') ? 'gap-1.5' : 'gap-1',
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
      // 层级的稳定判据（「这一屏有几颗主按钮」之类的用例认它，不认类名）
      data-variant={variant}
      onClick={handleClick}
      className={buttonClass({ variant, size, active, className })}
      {...props}
    >
      {content}
    </button>
  )
})

/**
 * 只有图标的按钮：Pin / Close / Copy / Refresh / More… 全部走它。
 *
 * 28 的圆钮、16px 图标（`iconSize="sm"` 时 14px；`lg` 是 32 的圆钮 + 16px 图标）、默认透明、hover 才浮出
 * surface-hover、按下 / 选中是 selected 那一档轻 tint——不用大块灰底。
 * **名字与气泡同一份**：`label` 既是 `aria-label` 也是 tooltip 文案，两者不会分叉
 * （审计 T39 担心的正是分叉）。`tip={false}` 只在已经有可见文字说明它的场合用，
 * 名字照样给。
 */
export interface IconButtonProps extends Omit<ButtonProps, 'size' | 'aria-label' | 'children'> {
  label: string
  /**
   * 档位：默认 md（28 钮、16px 图标）；与 11–12px 文字并排的小钮用 sm（28 钮、14px 图标）；
   * xs = 20 的行内小钮（14px 图标）；lg = 32 的钮（16px 图标：对话框页脚、命令面板输入行、助手发送）
   */
  iconSize?: 'md' | 'sm' | 'xs' | 'lg'
  /**
   * 气泡：默认显示 `label`；传 false 关掉（旁边已有可见文字时）；传字符串则气泡说
   * 另一句（只给「名字是动作、气泡讲当前状态」的开关钮，如钉住 / 宽高比锁）。
   */
  tip?: boolean | string
  shortcut?: string
  side?: 'top' | 'bottom' | 'left' | 'right'
  children: React.ReactNode
}

const ICON_BUTTON_SIZE = { md: 'icon', sm: 'icon-sm', xs: 'icon-xs', lg: 'icon-lg' } as const

export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(function IconButton(
  { label, iconSize = 'md', tip = true, shortcut, side, children, ...props },
  ref,
) {
  const btn = (
    <Button
      ref={ref}
      size={ICON_BUTTON_SIZE[iconSize]}
      aria-label={label}
      {...props}
    >
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
