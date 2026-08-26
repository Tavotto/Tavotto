import { cn } from '@/lib/utils'

/**
 * 离散步进滑杆。**每一格对应调用方给的一个真实取值**，绝不产生中间态——
 * 值域就是 `options`，`value` 是它的下标。
 *
 * 为什么不是一排同宽按钮：六档等宽按钮在 232–320px 的弹层里必然拥挤或截断
 * （见 docs/ux/img/ux-consistency-pass/before/zh-1440-ai-popover.png：
 * minimal 与 xhigh 两头都被切掉）。滑杆的宽度与档位数无关，加到八档也不会溢出。
 *
 * 无障碍：原生 `<input type="range">`，方向键 / Home / End 免费拿到，
 * 触屏免费拿到；`aria-valuetext` 报的是**当前语言的档位名**而不是下标数字
 * （屏幕阅读器念「3」毫无意义）。轨道下的小点表明它是离散的。
 */
export function StepSlider({
  value,
  count,
  onChange,
  ariaLabel,
  valueText,
  disabled,
  className,
}: {
  /** 当前档位下标（0 基） */
  value: number
  /** 总档位数 */
  count: number
  onChange: (index: number) => void
  ariaLabel: string
  /** 当前档位的可读名，进 aria-valuetext */
  valueText: string
  disabled?: boolean
  className?: string
}) {
  const max = Math.max(0, count - 1)
  const pct = max === 0 ? 0 : (value / max) * 100
  return (
    <div className={cn('flex min-w-0 flex-col gap-1', className)}>
      <div className="relative flex h-4 items-center">
        {/* 轨道：已走过的一段用 accent，其余用边框色 */}
        <span
          aria-hidden
          className="absolute inset-x-0 h-[3px] rounded-full bg-border-strong"
        />
        <span
          aria-hidden
          className="absolute left-0 h-[3px] rounded-full bg-accent"
          style={{ width: `${pct}%` }}
        />
        <input
          type="range"
          min={0}
          max={max}
          step={1}
          value={value}
          disabled={disabled || max === 0}
          aria-label={ariaLabel}
          aria-valuetext={valueText}
          onChange={(e) => onChange(Number(e.target.value))}
          className={cn(
            'relative h-4 w-full cursor-pointer appearance-none bg-transparent outline-none',
            'disabled:cursor-default disabled:opacity-40',
            // 拇指：14px 圆点，焦点环画在拇指上（原生 focus ring 会套整条轨道）
            '[&::-webkit-slider-thumb]:h-[14px] [&::-webkit-slider-thumb]:w-[14px]',
            '[&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:rounded-full',
            '[&::-webkit-slider-thumb]:border [&::-webkit-slider-thumb]:border-accent',
            '[&::-webkit-slider-thumb]:bg-surface',
            'focus-visible:[&::-webkit-slider-thumb]:ring-2 focus-visible:[&::-webkit-slider-thumb]:ring-accent/40',
            '[&::-moz-range-thumb]:h-[14px] [&::-moz-range-thumb]:w-[14px]',
            '[&::-moz-range-thumb]:rounded-full [&::-moz-range-thumb]:border',
            '[&::-moz-range-thumb]:border-accent [&::-moz-range-thumb]:bg-surface',
            '[&::-moz-range-track]:bg-transparent',
          )}
        />
      </div>
      {/* 离散刻度点：一格一个，表明这不是连续滑杆 */}
      {count > 1 && (
        <div aria-hidden className="flex justify-between px-[6px]">
          {Array.from({ length: count }, (_, i) => (
            <span
              key={i}
              className={cn(
                'h-[3px] w-[3px] rounded-full',
                i <= value ? 'bg-accent/60' : 'bg-border-strong',
              )}
            />
          ))}
        </div>
      )}
    </div>
  )
}
