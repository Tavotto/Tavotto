import { useRef, type KeyboardEvent, type ReactNode } from 'react'
import { cn } from '@/lib/utils'
import { useSlidingIndicator } from './slidingIndicator'
import { Tip } from './Tooltip'

export interface SegmentedItem<T extends string> {
  value: T
  icon?: ReactNode
  label?: ReactNode
  tip?: string
  /**
   * 只有图标的分段项的**可达名**。tooltip 不是可达名——屏幕阅读器读到的是
   * 一个没有名字的 radio。缺省时退回 `tip`，两者都没有才真的无名。
   */
  ariaLabel?: string
  /**
   * 这一档此刻选不了（「当前图」没有正在编辑的图时）。留在原位、灰掉、
   * 不响应点击；原因走 `title`——原生 title 在禁用按钮上也出得来，tooltip 不行。
   */
  disabled?: boolean
  title?: string
}

interface SegmentedProps<T extends string> {
  value: T | null
  onChange: (v: T) => void
  items: SegmentedItem<T>[]
  className?: string
  /** 整组的可达名（「方向」「作用范围」…）——无名的 radiogroup 说不清在选什么 */
  ariaLabel?: string
  /** 落在 radiogroup 根上的稳定锚点（onboarding coachmark / e2e） */
  'data-onboarding-anchor'?: string
  'data-testid'?: string
}

/**
 * radiogroup 的键盘契约（WAI-ARIA radio group 模式；2026-09-14 审计 S3）：
 * 整组只占**一个** Tab 停靠点（选中项，没有选中就是第一个可用项），
 * ← → 换到相邻可用项并**当场选中**，Home / End 到首尾。此前每一格都是一个
 * Tab 停靠点、方向键不动——一个四档分段把 Tab 顺序拉长四倍，读屏念出
 * 「N 之 K」却换不了值。与 `OptionGrid` 同一套约定（那边是二维漫游）。
 */
const KEY_DELTA: Record<string, number> = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }

/**
 * 分段选择器（segmented control）：一组**互斥的取值**排成 28px 的一行，hairline
 * 外框把它标成一个控件（与输入框、下拉同一条边），选中项 = `selected` 轻 tint +
 * 字重（与列表行、设置导航、`Button active` 同一套选中语言），未选中 ink-3、
 * hover 浮 surface-hover。整组默认撑满容器宽度，各档等分；`value` 为 null（多选
 * 取值不一）时没有一档被标成选中。
 *
 * 它与 `Tabs` 的分工（2026-09-13 审计 §6「控件语法」）：**页签负责切换视图，
 * 不负责属性取值**——对齐、刻度方向、纵横比、作用范围这些「值」用它；版本对比
 * 的「版本 / 当前」、问题面板的「当前图 / 整个文档」、刻度卡的「X / Y」那种
 * 「看哪一页」用 `Tabs`。此前两边都是下划线页签，值与视图长得一样，选边 /
 * 选方向像在切换页面（B45 / B52）。
 */
export function Segmented<T extends string>({
  value,
  onChange,
  items,
  className,
  ariaLabel,
  ...rest
}: SegmentedProps<T>) {
  const rootRef = useRef<HTMLDivElement>(null)
  // 选中底是整组唯一的一块，换值时滑到新的一格（2026-09-14 二审 E2）；此前每格自己的
  // bg-selected 一亮一灭，中间没有轨迹。多选取值不一（value 为 null）时没有选中项，也就没有它
  const thumb = useSlidingIndicator(rootRef, '[role="radio"][aria-checked="true"]')
  const enabled = items.filter((it) => !it.disabled)
  // Tab 落点：选中项；多选取值不一（value 为 null）或选中项不可用时退到第一个可用项
  const tabStop = enabled.find((it) => it.value === value) ?? enabled[0]

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    let next: SegmentedItem<T> | undefined
    if (e.key === 'Home') next = enabled[0]
    else if (e.key === 'End') next = enabled[enabled.length - 1]
    else {
      const delta = KEY_DELTA[e.key]
      if (delta == null || enabled.length === 0) return
      const cur = enabled.findIndex((it) => it.value === value)
      // 没有选中项时从「焦点所在」那一格起步，否则从选中项起步
      const from =
        cur >= 0
          ? cur
          : enabled.findIndex(
              (it) =>
                it.value ===
                (e.target as HTMLElement).closest('[role="radio"]')?.getAttribute('data-value'),
            )
      next = enabled[Math.max(0, Math.min(enabled.length - 1, (from < 0 ? 0 : from) + delta))]
    }
    if (!next) return
    e.preventDefault()
    if (next.value !== value) onChange(next.value)
    // 焦点跟着选中走：连续按方向键才能继续漫游；用 data-value 找，label 可能是图标
    rootRef.current
      ?.querySelector<HTMLButtonElement>(`[data-value="${CSS.escape(next.value)}"]`)
      ?.focus()
  }

  return (
    <div
      ref={rootRef}
      role="radiogroup"
      aria-label={ariaLabel}
      onKeyDown={onKeyDown}
      className={cn(
        'relative flex h-7 w-full items-stretch rounded-sm border border-border bg-surface',
        className,
      )}
      {...rest}
    >
      {thumb.style && (
        <span
          aria-hidden
          data-segmented-thumb
          className={cn(
            'pointer-events-none absolute inset-y-0 left-0 rounded-sm bg-selected',
            thumb.animate && 'transition-[transform,width] duration-base ease-pop',
          )}
          style={thumb.style}
        />
      )}
      {items.map((item) => {
        const active = item.value === value
        const btn = (
          <button
            key={item.value}
            type="button"
            onClick={() => {
              if (!item.disabled) onChange(item.value)
            }}
            role="radio"
            aria-checked={active}
            aria-disabled={item.disabled || undefined}
            disabled={item.disabled}
            data-value={item.value}
            tabIndex={item === tabStop ? 0 : -1}
            title={item.title}
            aria-label={item.label == null ? (item.ariaLabel ?? item.tip) : undefined}
            className={cn(
              // relative：压在滑动的选中底之上；每格不再自己画 bg-selected
              'relative flex min-w-7 flex-1 items-center justify-center gap-1 whitespace-nowrap px-2 text-sm outline-none',
              // 首尾两格跟着外框的圆角走，hover 的 tint 才不会在角上露出方角
              'first:rounded-l-sm last:rounded-r-sm',
              'transition-colors duration-fast focus-visible:z-10 focus-visible:focus-ring',
              active
                ? 'font-medium text-ink'
                : item.disabled
                  ? 'cursor-default text-ink-faint'
                  : // 未选中的标签是要读的字：ink-3（≥4.5:1），不用 opacity 淡化
                    'text-ink-3 hover:bg-surface-hover hover:text-ink',
            )}
          >
            {item.icon}
            {item.label}
          </button>
        )
        return item.tip ? (
          <Tip key={item.value} label={item.tip}>
            {btn}
          </Tip>
        ) : (
          btn
        )
      })}
    </div>
  )
}
