import { useRef, type KeyboardEvent, type ReactNode } from 'react'
import { Check } from 'lucide-react'
import { cn } from '@/lib/utils'
import { Tip } from '../../ui/Tooltip'

export interface GridOption<T extends string = string> {
  value: T
  /** 无障碍名与 tooltip：视觉预览之外必须有文字名（不能只有图形） */
  label: string
  /** 视觉预览；缺省时显示 label 文字 */
  preview?: ReactNode
  /**
   * 原始代码（marker 的 "D"、hatch 的 "//"）。**只落成 `data-code`，不进
   * 可见文案**：格子的名字是中文（或当前语言）的图形名，tooltip 也只说名字
   * ——「无 · None」「点线 · :」这种把实现值贴在名字后面的写法是审计
   * T15 / T21 点名的噪音。要看代码的人在源文件与高级里看。
   */
  code?: string
}

/** 格子的 tooltip / 可达名：只有名字，没有代码 */
export const tipLabelOf = (opt: { label: string; code?: string }): string => opt.label

/**
 * 视觉选择器共用的网格：radiogroup 语义 + 方向键漫游 + 选中态角标。
 *
 * 选中态不只靠颜色：accent 边框之外还有左上角的 check 角标；
 * 每个格子的名字是文字 label（aria-label + tooltip），图形只是预览。
 */
export function OptionGrid<T extends string>({
  value,
  options,
  onChange,
  columns = 5,
  ariaLabel,
  cellClassName,
}: {
  value: T | null
  options: GridOption<T>[]
  onChange: (v: T) => void
  columns?: number
  ariaLabel: string
  cellClassName?: string
}) {
  const ref = useRef<HTMLDivElement>(null)

  /** 方向键在网格里漫游（radiogroup 的键盘契约）；漫游即选中 */
  const onKeyDown = (e: KeyboardEvent) => {
    const keys: Record<string, number> = {
      ArrowRight: 1,
      ArrowLeft: -1,
      ArrowDown: columns,
      ArrowUp: -columns,
    }
    const delta = keys[e.key]
    if (delta == null) return
    e.preventDefault()
    const i = options.findIndex((o) => o.value === value)
    const next = options[Math.max(0, Math.min(options.length - 1, (i < 0 ? 0 : i) + delta))]
    if (next && next.value !== value) {
      onChange(next.value)
      // 焦点跟着选中走，连续按方向键才能继续漫游
      requestAnimationFrame(() => {
        ref.current
          ?.querySelector<HTMLButtonElement>(`[data-value="${CSS.escape(next.value)}"]`)
          ?.focus()
      })
    }
  }

  return (
    <div
      ref={ref}
      role="radiogroup"
      aria-label={ariaLabel}
      className="grid gap-1"
      style={{ gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` }}
      onKeyDown={onKeyDown}
    >
      {options.map((opt) => {
        const active = opt.value === value
        return (
          <Tip key={opt.value} label={tipLabelOf(opt)}>
            <button
              type="button"
              role="radio"
              aria-checked={active}
              aria-label={opt.label}
              data-value={opt.value}
              data-code={opt.code}
              // radiogroup 的漫游焦点：选中项可 Tab 进入，其余用方向键到达
              tabIndex={active || (value == null && opt === options[0]) ? 0 : -1}
              onClick={() => onChange(opt.value)}
              className={cn(
                'relative flex h-8 items-center justify-center rounded-sm border outline-none transition-colors',
                'focus-visible:focus-ring',
                active
                  ? 'border-accent bg-accent-subtle text-ink'
                  : 'border-border bg-surface text-ink-2 hover:border-border-strong hover:text-ink',
                cellClassName,
              )}
            >
              {active && (
                <Check
                  size={9}
                  aria-hidden
                  className="absolute left-0.5 top-0.5 text-accent"
                />
              )}
              {opt.preview ?? <span className="truncate px-1 text-xs">{opt.label}</span>}
            </button>
          </Tip>
        )
      })}
    </div>
  )
}
