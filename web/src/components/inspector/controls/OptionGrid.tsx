import { Fragment, useRef, type KeyboardEvent, type ReactNode } from 'react'
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
 * 视觉选择器共用的网格：radiogroup 语义 + 方向键漫游。
 *
 * **与 `ui/Segmented` 同一副皮**（2026-10-07 设计审计 §9.2 P1）：灰槽里一格格无边的样张，
 * 选中那一格浮起成白底 + `shadow-thumb` + 600 字重——不是第三种控件皮肤，也不再在角上压一个
 * check（角标盖住的正是预览图形的左上角）。选中态不只靠颜色：浮起的白块是形状变化。
 * 每个格子的名字是文字 label（aria-label + tooltip），图形只是预览。
 */
export function OptionGrid<T extends string>({
  value,
  options,
  onChange,
  onPick,
  columns = 5,
  ariaLabel,
  cellClassName,
  previewHasLabel = false,
}: {
  value: T | null
  options: GridOption<T>[]
  onChange: (v: T) => void
  /**
   * **点选**（鼠标点击、Enter / Space）之后再调一次——与 `onChange` 的区别：方向键漫游
   * 也会 `onChange`（radiogroup 的「选中跟着焦点走」），但漫游不该把弹层关掉。
   * 弹层里的选择器用它来收起自己。
   */
  onPick?: (v: T) => void
  columns?: number
  ariaLabel: string
  cellClassName?: string
  /**
   * 预览里已经印了名字（单列的线型 / 端型 / 箭头样式那种「样张 + 文字」的行）。
   * 此时**不包 Tip**：气泡会盖在同一行的同一句话上，把「实线」念第二遍（打磨 E12）。
   * 可达名仍由每个格子的 `aria-label` 提供，读屏不受影响。
   */
  previewHasLabel?: boolean
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
      data-option-grid
      className="grid gap-0.5 rounded-lg bg-surface-hover p-0.5"
      style={{ gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` }}
      onKeyDown={onKeyDown}
    >
      {options.map((opt) => {
        const active = opt.value === value
        const cell = (
          <button
            type="button"
            role="radio"
            aria-checked={active}
            aria-label={opt.label}
            data-value={opt.value}
            data-code={opt.code}
            // radiogroup 的漫游焦点：选中项可 Tab 进入，其余用方向键到达
            tabIndex={active || (value == null && opt === options[0]) ? 0 : -1}
            onClick={() => {
              onChange(opt.value)
              onPick?.(opt.value)
            }}
            className={cn(
              // 外层 12 = 格 8 + 槽内边距 2 + 2（外层 = 内层 + 内边距）
              'relative flex h-8 items-center justify-center rounded-md outline-none transition-colors',
              'focus-visible:focus-ring',
              active
                ? 'bg-surface font-semibold text-ink shadow-thumb'
                : 'text-ink-2 hover:bg-surface-active hover:text-ink',
              cellClassName,
            )}
          >
            {opt.preview ?? <span className="truncate px-1 text-sm">{opt.label}</span>}
          </button>
        )
        return previewHasLabel ? (
          <Fragment key={opt.value}>{cell}</Fragment>
        ) : (
          <Tip key={opt.value} label={tipLabelOf(opt)}>
            {cell}
          </Tip>
        )
      })}
    </div>
  )
}
