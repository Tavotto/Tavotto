import { useEffect, useState, type HTMLAttributes, type ReactNode } from 'react'
import { DURATION, usePresence } from '@/lib/motion'
import { cn } from '@/lib/utils'

/** Inspector 分组：标题 + 内容。属性栏里组与组之间一条内缩的 hairline（2026-09-30 重设计，index.css 的 `[data-section]` 规则）；别处靠留白 */
export function Section({
  title,
  action,
  children,
  className,
  plainTitle = false,
  ...rest
}: {
  title?: ReactNode
  action?: ReactNode
  children: ReactNode
  className?: string
  /** 标题是内容而非分组名（如图内元素名）时关掉全大写 */
  plainTitle?: boolean
} & Omit<HTMLAttributes<HTMLElement>, 'title' | 'children' | 'className'>) {
  // 分区头「上宽下紧」（上 16 / 下 4，Claude 分区头 padding 16 6 4）：标题贴着自己的内容，
  // 不是均匀地悬在两组之间（2026-09-15 审计 B14）
  return (
    <section data-section {...rest} className={cn('px-3 pb-4 pt-4 [&+&]:pt-0', className)}>
      {title && (
        <header className="mb-1 flex h-4 items-center justify-between">
          <h3
            className={cn(
              'min-w-0 truncate',
              plainTitle ? 'text-xs font-medium text-ink-2' : 'type-section',
            )}
          >
            {title}
          </h3>
          {action}
        </header>
      )}
      {children}
    </section>
  )
}

/**
 * 折叠内容的展开 / 收起（2026-09-14 二审 E3）：`grid-template-rows: 0fr → 1fr` + 淡入，
 * 下面的行跟着内容长高而不是瞬间跳出——动效解释的是「哪些行是新出现的」。
 * 收起走 `usePresence` 保活 exit 那 90ms 播 1fr → 0fr，之后才真的卸载（内容不常驻）。
 * 裁切只在动画期间生效：播完就把 overflow-hidden 撤掉，否则贴边控件的焦点环会被切。
 * `prefers-reduced-motion` 由 index.css 的全局兜底把两段动画压到 0.01ms。
 */
export function Reveal({ open, className, children }: { open: boolean; className?: string; children: ReactNode }) {
  const { mounted, state } = usePresence(open, DURATION.exit)
  const [settled, setSettled] = useState(false)
  useEffect(() => {
    if (!open) setSettled(false)
  }, [open])
  if (!mounted) return null
  return (
    <div
      data-state={state}
      data-reveal
      className={cn(
        // 列钉成 minmax(0,1fr)：grid 的隐式列按 min-content 定宽，里面一行 truncate 的长路径会把列
        // 撑过容器右缘（编码 Agent 详情「找过这些位置」实测）；钉住之后 truncate 才生效（2026-09-15 对话框批次）
        'grid grid-cols-[minmax(0,1fr)] data-[state=open]:animate-reveal-in data-[state=closed]:animate-reveal-out',
        className,
      )}
      onAnimationEnd={(e) => {
        if (e.target === e.currentTarget && open) setSettled(true)
      }}
    >
      <div className={cn('min-h-0', !settled && 'overflow-hidden')}>{children}</div>
    </div>
  )
}

/**
 * 标签在左、控件在右的紧凑行：标签列定宽，控件从同一条竖线起排（固定的控件列），
 * **不**把控件推到侧栏最右边——那样每行的控件各漂各的，读不出一列
 * （Design Constitution 第三节；2026-09-11 Session 2 把第四批的 justify-end 改回来）。
 *
 * `align='start'` 给**多行高的控件**用（图例位置的内 / 外两带那种）：默认的
 * 垂直居中会把标签推到控件的半腰，看上去像在给下面那一段命名——真浏览器里
 * 一眼就看得出来，jsdom 量不到。标签自己用 `leading-6` 对齐到第一行控件的
 * 中线，而不是顶到最上沿。
 */
export function Row({
  label,
  children,
  className,
  labelWidth = 44,
  align = 'center',
}: {
  label?: ReactNode
  children: ReactNode
  className?: string
  /** 'auto' = 标签只占自己的宽度（字号那种紧挨着输入框的短标签） */
  labelWidth?: number | 'auto'
  align?: 'center' | 'start'
}) {
  const top = align === 'start'
  return (
    <div className={cn('flex min-h-7 gap-2', top ? 'items-start' : 'items-center', className)}>
      {label != null && (
        <span
          style={labelWidth === 'auto' ? undefined : { width: labelWidth }}
          className={cn('shrink-0 text-xs text-ink-2', top && 'leading-6')}
        >
          {label}
        </span>
      )}
      <div
        className={cn(
          'flex min-w-0 flex-1 gap-1.5',
          top ? 'items-start' : 'items-center',
        )}
      >
        {children}
      </div>
    </div>
  )
}

export function Grid2({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cn('grid grid-cols-2 gap-1.5', className)}>{children}</div>
}
