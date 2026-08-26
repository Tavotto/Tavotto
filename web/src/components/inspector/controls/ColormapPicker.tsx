import { Check, ChevronDown } from 'lucide-react'
import { useState } from 'react'
import { t as translate } from '@/i18n'
import { cn } from '@/lib/utils'
import { Popover } from '../../ui/Popover'
import { colormapGradient } from './colormapStops'

/** 多选取值不一致时触发按钮上的占位文案（函数：常量会把语言定死在模块求值那一刻） */
const MIXED_TEXT = () => translate('element.mixedValues', { ns: 'inspector' })


/**
 * Colormap 选择器：真实渐变条（stops 离线采样自 matplotlib），
 * 名称保留原文——viridis / RdBu_r 是 Matplotlib 标识符，翻译反而对不上文档。
 * 脚本自定义的 cmap 没有 stops，回落成名称显示，选它 = 保持原样。
 */

function GradientBar({ name, className }: { name: string; className?: string }) {
  const grad = colormapGradient(name)
  if (!grad) {
    return (
      <span
        aria-hidden
        className={cn(
          'flex h-3.5 items-center justify-center rounded-[2px] border border-dashed border-border font-mono text-[9px] text-ink-3',
          className,
        )}
      >
        ?
      </span>
    )
  }
  return (
    <span
      aria-hidden
      className={cn('block h-3.5 rounded-[2px] border border-border-strong/40', className)}
      style={{ background: grad }}
    />
  )
}

export function ColormapPicker({
  value,
  options,
  onChange,
  ariaLabel,
}: {
  /**
   * 当前值。**多选取值不一致时传 null**——那时一个格子都不该被标成选中，
   * 也不该把「空」当成一个自定义值塞进选项表。
   */
  value: string | null
  options: string[]
  onChange: (v: string) => void
  ariaLabel: string
}) {
  const [open, setOpen] = useState(false)
  const all = value && !options.includes(value) ? [value, ...options] : options

  return (
    <Popover
      width={228}
      align="start"
      open={open}
      onOpenChange={setOpen}
      trigger={
        <button
          type="button"
          aria-label={ariaLabel}
          className={cn(
            'flex h-7 w-full items-center gap-1.5 rounded-sm border border-transparent bg-surface-2 px-1.5',
            'text-xs text-ink outline-none transition-colors hover:border-border',
            'focus-visible:focus-ring',
            open && 'border-accent',
          )}
        >
          {value !== null && <GradientBar name={value} className="w-12 shrink-0" />}
          <span className="min-w-0 flex-1 truncate text-left font-mono">
            {value === null ? MIXED_TEXT() : value}
          </span>
          <ChevronDown size={12} className="shrink-0 text-ink-3" />
        </button>
      }
    >
      <div
        role="radiogroup"
        aria-label={ariaLabel}
        className="flex max-h-72 flex-col gap-0.5 overflow-y-auto"
      >
        {all.map((name) => {
          const active = name === value
          return (
            <button
              key={name}
              type="button"
              role="radio"
              aria-checked={active}
              aria-label={
                colormapGradient(name)
                  ? name
                  : translate('control.customColormap', { ns: 'inspector', value: name })
              }
              onClick={() => {
                onChange(name)
                setOpen(false)
              }}
              className={cn(
                'flex h-7 items-center gap-2 rounded-sm border px-1.5 outline-none transition-colors',
                'focus-visible:focus-ring',
                active
                  ? 'border-accent bg-accent-subtle'
                  : 'border-transparent hover:bg-ink/[.045]',
              )}
            >
              <span className="flex w-3.5 shrink-0 items-center">
                {active && <Check size={11} aria-hidden className="text-accent" />}
              </span>
              <GradientBar name={name} className="w-16 shrink-0" />
              <span className="min-w-0 flex-1 truncate text-left font-mono text-xs text-ink">
                {name}
              </span>
            </button>
          )
        })}
      </div>
    </Popover>
  )
}
