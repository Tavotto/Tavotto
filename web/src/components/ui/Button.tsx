import { forwardRef, useCallback, useRef, useState, type ButtonHTMLAttributes } from 'react'
import { Loader2 } from 'lucide-react'
import { cn } from '@/lib/utils'

type Variant = 'ghost' | 'outline' | 'primary' | 'danger'
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
  ghost: 'text-ink hover:bg-ink/[.055] active:bg-ink/[.09]',
  outline: 'border border-border bg-surface text-ink hover:border-border-strong hover:bg-surface-2',
  // 主动作用近黑色；蓝色只留给选择 / 焦点 / 链接
  primary: 'bg-ink text-white hover:bg-ink/90 active:bg-ink/95',
  danger: 'text-danger hover:bg-danger/[.08]',
}

const SIZES: Record<Size, string> = {
  sm: 'h-7 px-2 gap-1 text-xs rounded-sm',
  md: 'h-7 px-2.5 gap-1.5 text-sm rounded-sm',
  icon: 'h-7 w-7 rounded-sm',
  // 图标点击区不小于 28px；两档只差图标字号
  'icon-sm': 'h-7 w-7 rounded-sm',
}

const SPINNER: Record<Size, number> = { sm: 11, md: 13, icon: 14, 'icon-sm': 12 }

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
        <Loader2 size={SPINNER[size]} className="animate-spin" />
        {loadingLabel}
      </span>
    </span>
  ) : (
    <>
      {busy && <Loader2 size={SPINNER[size]} className="animate-spin" />}
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
        'transition-[background-color,border-color,color] duration-100',
        'focus-visible:focus-ring outline-none',
        // 不用 pointer-events-none：那会连 not-allowed 光标和 tooltip 一起吞掉，
        // 点击本来就被原生 disabled 挡住了
        'disabled:cursor-not-allowed disabled:opacity-35',
        VARIANTS[variant],
        SIZES[size],
        active && variant !== 'primary' && 'bg-accent-subtle text-accent hover:bg-accent-subtle',
        className,
      )}
      {...props}
    >
      {content}
    </button>
  )
})
