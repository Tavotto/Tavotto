import { t as translate } from '@/i18n'
import { cn } from '@/lib/utils'
import { optionLabel } from '../roles/registry'
import { Tip } from '../../ui/Tooltip'

/**
 * 图例位置：3×3 位置网格 + 「自动」档，替代文字下拉。
 *
 * 写入值仍是 Matplotlib 的 loc 名（"upper right" …）。"custom" 表示用户在
 * 画布上拖过图例（bbox_to_anchor），显示为说明而不是可点的档位——点一个
 * 网格位置即回到预设定位。manifest 没给的档位不渲染。
 */

const GRID: string[][] = [
  ['upper left', 'upper center', 'upper right'],
  ['center left', 'center', 'center right'],
  ['lower left', 'lower center', 'lower right'],
]

export function LegendPositionPicker({
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
  const has = (v: string) => options.includes(v)
  // "right" 是 matplotlib 的历史别名（≈ center right），只有当前值恰好是它时才显示
  const extraChips = ['best', ...(value === 'right' ? ['right'] : [])].filter(has)

  return (
    <div className="flex w-full min-w-0 items-start gap-2">
      <div
        role="radiogroup"
        aria-label={ariaLabel}
        className="grid shrink-0 grid-cols-3 gap-px rounded-sm border border-border bg-surface p-1"
      >
        {GRID.flat().map((loc) => {
          if (!has(loc)) return <span key={loc} className="h-5 w-6" aria-hidden />
          const active = value === loc
          return (
            <Tip key={loc} label={optionLabel('loc', loc)}>
              <button
                type="button"
                role="radio"
                aria-checked={active}
                aria-label={optionLabel('loc', loc)}
                onClick={() => onChange(loc)}
                className={cn(
                  'flex h-5 w-6 items-center justify-center rounded-[2px] outline-none transition-colors',
                  'focus-visible:focus-ring',
                  active ? 'bg-accent-subtle' : 'hover:bg-ink/[.05]',
                )}
              >
                {/* 选中不只靠颜色：选中格是实心方块，未选是空心圆点 */}
                {active ? (
                  <span aria-hidden className="h-2 w-2 rounded-[1px] bg-accent" />
                ) : (
                  <span aria-hidden className="h-1.5 w-1.5 rounded-full border border-ink-faint" />
                )}
              </button>
            </Tip>
          )
        })}
      </div>
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        {extraChips.map((v) => {
          const active = value === v
          return (
            <button
              key={v}
              type="button"
              aria-pressed={active}
              onClick={() => onChange(v)}
              className={cn(
                'flex h-6 items-center justify-center rounded-sm border px-1.5 text-xs outline-none transition-colors',
                'focus-visible:focus-ring',
                active
                  ? 'border-accent bg-accent-subtle font-medium text-accent'
                  : 'border-border text-ink-2 hover:border-border-strong hover:text-ink',
              )}
            >
              {optionLabel('loc', v)}
            </button>
          )
        })}
        {value === 'custom' && (
          <p className="text-xs leading-snug text-ink-3">
            {translate('control.legendCustomHint', { ns: 'inspector' })}
          </p>
        )}
      </div>
    </div>
  )
}
