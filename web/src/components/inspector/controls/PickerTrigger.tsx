import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from 'react'
import { ChevronDown } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { FIELD_BOX, FIELD_OPEN } from '@/components/ui/fieldBox'
import { t as translate } from '@/i18n'
import { cn } from '@/lib/utils'

/**
 * 带样张的取值选择器（线型 / 标记 / 填充纹理 / 色图 / 箭头端型）的**触发器**，全站一份。
 *
 * 2026-09-14 审计 S5 之前有三种：MarkerPicker / HatchPicker / ColormapPicker 是 surface-2 灰底
 * 11px 的 Radix Popover 触发器；LineStylePicker 是白底 hairline 12px、弹层用 absolute 挂在面板里
 * （会被 overflow 裁掉、`aria-haspopup="listbox"` 却开出一个 radiogroup）；StrokeSection 的箭头
 * 端型又是一套手写 combobox + listbox。同一张曲线页上下相邻的「线型」与「标记」长成两种。
 *
 * 现在：框与 `Select` / 输入框同一副（`fieldBox`），弹层一律 `ui/Popover`（portal、Esc、
 * 点外面关闭、焦点还回来），弹层里是 `OptionGrid`（radiogroup + 方向键）。Radix 的
 * Popover.Trigger 通过 asChild 把 aria-haspopup / aria-expanded / data-state 落到这颗按钮上，
 * 页面里不再手写 `aria-haspopup`。
 *
 * `preview` 是当前值的样张；`children` 是当前值的名字；`value === null`（多选取值不一）时
 * 显示「多个值」、不画样张——不谎报其中某一个。
 */
export const PickerTrigger = forwardRef<
  HTMLButtonElement,
  Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'children'> & {
    ariaLabel: string
    mixed: boolean
    preview?: ReactNode
    /** 当前值的名字（`mixed` 时忽略） */
    children?: ReactNode
    /** 名字用等宽（色图的内置名） */
    mono?: boolean
    /** 名字那一截的悬停提示（色图的自定义名：原文留在这里，用户查脚本时认得出） */
    textTitle?: string
  }
>(function PickerTrigger(
  { ariaLabel, mixed, preview, children, mono, textTitle, className, ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      type="button"
      aria-label={ariaLabel}
      {...rest}
      className={cn(
        'group flex h-7 w-full items-center gap-1.5 px-1.5 text-left outline-none',
        FIELD_BOX,
        FIELD_OPEN,
        'focus-visible:focus-ring',
        'disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:border-border-input',
        className,
      )}
    >
      {!mixed && preview}
      <span
        title={mixed ? undefined : textTitle}
        className={cn('min-w-0 flex-1 truncate', mixed && 'text-ink-3', mono && 'font-mono')}
      >
        {mixed ? translate('mixed', { ns: 'common' }) : children}
      </span>
      {/* 下拉的记号只有 chevron-down（宪法第四节）：打开时同一枚转到朝上 */}
      <ChevronDown
        size={ICON_SIZE.xs}
        aria-hidden
        className="shrink-0 text-ink-3 transition-transform duration-fast group-data-[state=open]:rotate-180"
      />
    </button>
  )
})
