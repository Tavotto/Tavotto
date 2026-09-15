import { useEffect, useState, type HTMLAttributes, type ReactNode } from 'react'
import { ChevronRight } from './icons'
import { ICON_SIZE } from './Icon'
import { DURATION, usePresence } from '@/lib/motion'
import { cn } from '@/lib/utils'

/** Inspector 分组：标题 + 内容。组间靠留白分层，不再画分隔线 */
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
    <section {...rest} className={cn('px-3 pb-4 pt-4 [&+&]:pt-0', className)}>
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
        'grid data-[state=open]:animate-reveal-in data-[state=closed]:animate-reveal-out',
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

/** 折叠分组：低频内容默认收起，标题行即开关 */
export function Disclosure({
  title,
  open,
  onToggle,
  children,
  summary,
}: {
  title: ReactNode
  open: boolean
  onToggle: () => void
  children: ReactNode
  /** 折叠时跟在标题后的一句现状摘要 */
  summary?: ReactNode
}) {
  return (
    // pb-4：与 Section 同一个节拍（分区之间 16），画布页五组折叠行与「源文件与高级」不再各走一套 40 / 44（2026-09-15 检查器批次 L5）
    <section className="px-3 pb-4">
      <button
        onClick={onToggle}
        aria-expanded={open}
        // 分区级折叠头 = 分区标题那一档（12/500/ink，type-section）：「浅 + 重」两头都不占（2026-09-15 审计 B06）
        className="flex h-7 w-full items-center gap-1 rounded-sm text-left text-sm text-ink outline-none focus-visible:focus-ring"
      >
        <ChevronRight
          size={ICON_SIZE.xs}
          aria-hidden
          className={cn('shrink-0 transition-transform', open && 'rotate-90')}
        />
        <span className="font-medium">{title}</span>
        {!open && summary != null && (
          <>
            {/* 名字按内容取：标题与摘要之间没有分隔时读屏念成「背景#FFFFFF」「GuidesNone」
                （2026-09-12 critique 的可访问名清单）。只给辅助技术加一个停顿，视觉不变 */}
            <span className="sr-only">, </span>
            <span className="ml-auto min-w-0 truncate text-right text-xs text-ink-3">{summary}</span>
          </>
        )}
      </button>
      <Reveal open={open}>
        <div className="mt-1.5">{children}</div>
      </Reveal>
    </section>
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
