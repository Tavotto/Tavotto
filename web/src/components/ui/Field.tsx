import { useEffect, useState, type HTMLAttributes, type ReactNode } from 'react'
import { DURATION, usePresence } from '@/lib/motion'
import { cn } from '@/lib/utils'

/**
 * Inspector 分组：标题 + 内容。属性栏里组与组之间一条内缩的 hairline（2026-09-30 重设计，index.css 的 `[data-section]` 规则）；别处靠留白。
 *
 * 三级标题（2026-10-07 设计审计 §9.2 P0-4）：**节**（这里）32px 12/500 ink · **组**（`inspector/GroupHead`）24px 11/500 ink-3 ·
 * **折叠行**（`ui/SummaryRow`）32px 12/400 ink + ink-3 摘要。三级此前同形（都是 12/500/ink），读不出谁管谁。
 */
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
  // 节头 32px（文字居中 = 上下各 8），标题贴着自己的内容：节的上距只留 4，
  // 下一节的分隔线由 index.css 画（2026-10-07 §9.2：L1 32px）
  return (
    <section data-section {...rest} className={cn('px-3 pb-3', title ? 'pt-1' : 'pt-3', className)}>
      {title && (
        <header className="flex h-8 items-center justify-between gap-2">
          <h3
            className={cn(
              'min-w-0 truncate',
              plainTitle ? 'text-sm font-medium text-ink-2' : 'type-section',
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
 * 行的标签列宽。数字 = 定宽（左栏素材筛选行 36 这类）；`'auto'` = 只占自己的宽度；
 * `'grid'` = **属性栏的行网格**（2026-10-07 设计审计 §9.2 P0-2 / P0-3）：
 *
 *     [ 标签 var(--insp-label) ] 8 [ 控件 minmax(0,1fr) ] 8 [ 状态槽 20 ]
 *
 * 标签列宽 `--insp-label` = `clamp(88px, 28cqi, 112px)`，由属性栏的容器（`Inspector` 的
 * `@container`）给出；控件列有了右缘（控件只有 full / half 两档）；状态槽**常驻** 20px，
 * 恢复钮住在里面——改值不再让控件少 34px、整行跳一下。不在属性栏里时退回 88。
 */
export type RowLabelWidth = number | 'auto' | 'grid'

/** 属性栏行网格的列模板（Row 与「对齐到控件列」的块共用这一处） */
export const ROW_GRID_COLS = 'grid-cols-[var(--insp-label,88px)_minmax(0,1fr)_1.25rem]'

/**
 * 标签在左、控件在右的紧凑行：标签列定宽，控件从同一条竖线起排（固定的控件列），
 * **不**把控件推到侧栏最右边——那样每行的控件各漂各的，读不出一列
 * （Design Constitution 第三节；2026-09-11 Session 2 把第四批的 justify-end 改回来）。
 *
 * `align='start'` 给**多行高的控件**用（图例位置的内 / 外两带那种）：默认的
 * 垂直居中会把标签推到控件的半腰，看上去像在给下面那一段命名——真浏览器里
 * 一眼就看得出来，jsdom 量不到。标签自己用 `leading-6` 对齐到第一行控件的
 * 中线，而不是顶到最上沿。
 *
 * `status` 只在 `labelWidth='grid'` 时有位置：第三列的 20px 状态槽（恢复钮 / 披露 chevron）。
 */
export function Row({
  label,
  children,
  className,
  labelWidth = 44,
  align = 'center',
  status,
}: {
  label?: ReactNode
  children: ReactNode
  className?: string
  /** 'auto' = 标签只占自己的宽度；'grid' = 属性栏行网格（见 `RowLabelWidth`） */
  labelWidth?: RowLabelWidth
  align?: 'center' | 'start'
  /** 状态槽里的东西（恢复到脚本的 ↺）。没有时槽位照样留着 */
  status?: ReactNode
}) {
  const top = align === 'start'
  if (labelWidth === 'grid') {
    return (
      <div
        data-row-grid
        className={cn(
          'grid min-h-7 gap-x-2',
          ROW_GRID_COLS,
          top ? 'items-start' : 'items-center',
          className,
        )}
      >
        {/* 标签 12/400 ink-2，放不下折两行（不截省略号：省略号遮住的正是用户认识的那个词） */}
        <span
          data-row-label
          className={cn(
            'relative min-w-0 text-sm leading-tight text-ink-2 break-words',
            top && 'flex min-h-7 items-center',
          )}
        >
          {typeof label === 'string' ? <span className="line-clamp-2">{label}</span> : label}
        </span>
        <div className={cn('flex min-w-0 gap-1.5', top ? 'items-start' : 'items-center')}>{children}</div>
        <span data-row-status className={cn('flex w-5 justify-center', top ? 'h-7 items-center' : 'items-center')}>
          {status}
        </span>
      </div>
    )
  }
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
      {status}
    </div>
  )
}

export function Grid2({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cn('grid grid-cols-2 gap-1.5', className)}>{children}</div>
}
