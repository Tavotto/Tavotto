import { useId } from 'react'
import { t as translate } from '@/i18n'
import {
  LEGEND_OUTSIDE_PRESETS,
  outsidePresetOf,
  type LegendAnchor,
  type LegendPlacement,
} from '@/lib/legendModel'
import { cn } from '@/lib/utils'
import { optionLabel } from '../roles/registry'
import { NumberField } from '../../ui/Input'
import { Tip } from '../../ui/Tooltip'

/**
 * 图例位置：**内 / 外两带**的一个控件（审计 T17 + 2026-09-07 的外侧锚点）。
 *
 *   图内  3×3 位置网格 + 「最佳位置」——写 matplotlib 的 `loc` 名，锚框清掉；
 *   图外  六个常用外侧位 + 自定义锚点 x / y——写 `loc` + `loc_anchor`
 *         （父容器分数坐标里的一个点，`1.02` = 子图右边缘往外 2%）。
 *
 * 两带是同一件事的两种说法，所以是同一个控件、一次写入（`setLegendPlacement`
 * 把 set 与 remove 落进同一次 commit）。"custom" 表示用户在画布上拖过图例，
 * 显示为说明而不是可点的档位——点任何一个档位即回到预设定位。
 *
 * 那个方框**就是参照的容器**：九个档位在它内侧、六个外侧位在它外侧。审计
 * T17 要的「看得懂所参照的范围」靠两样东西——框下写出容器叫什么（`containerLabel`，
 * 「相对子图 1」，一行、不是一段说明），以及 `<Diagram>` 按**当前值**重画的
 * 那张小示意（容器边界 + 图例此刻落在哪）。静态内联 SVG，没有动画。
 *
 * 容器认不出来（脚本自己造的图例、fig.legend）时不写名字——宁可不写，也不写
 * 一个猜的。引擎不发 `loc_anchor`（脚本用了 4 元组锚框 / 非父容器变换）时整个
 * 外侧带不出现，理由由 `UnsupportedProps` 那条说出口，不在这里编。
 */

const GRID: string[][] = [
  ['upper left', 'upper center', 'upper right'],
  ['center left', 'center', 'center right'],
  ['lower left', 'lower center', 'lower right'],
]

/** 示意图里容器（子图）在画布上的位置；四周留白是给外侧位的 */
const BOX = { x: 16, y: 12, w: 40, h: 30 }
const VIEW = { w: 72, h: 54 }
/** 图例小方块的尺寸（示意用，不按真实比例——真实比例这么小的图上看不出来） */
const CHIP = { w: 14, h: 8 }

const ins = (key: string, values?: Record<string, unknown>) =>
  translate(key, { ns: 'inspector', ...(values ?? {}) })

/** `'upper left'` → `['upper', 'left']`；`'best'` / 认不出的回 `[null, null]` */
function splitLoc(loc: string): [string | null, string | null] {
  if (loc === 'center') return ['center', 'center']
  if (loc === 'right') return ['center', 'right'] // matplotlib 的历史别名
  const parts = loc.split(' ')
  if (parts.length !== 2) return [null, null]
  const [vert, horiz] = parts
  if (!['upper', 'lower', 'center'].includes(vert)) return [null, null]
  if (!['left', 'right', 'center'].includes(horiz)) return [null, null]
  return [vert, horiz]
}

/**
 * 图例小方块在示意图里的落点。
 *
 * 与 matplotlib 同一条算法：锚框（没有锚框时就是容器本身）上取 `loc` 说的
 * 那个角，图例的**同名角**贴上去。所以 `loc='upper left'` +
 * `anchor=(1.02, 1)` 画出来就是「图例的左上角贴在容器右边缘外侧的顶端」，
 * 与真实渲染是同一个心智模型，而不是六个硬编码的坐标。
 */
function chipAt(placement: LegendPlacement): { x: number; y: number } | null {
  const loc = placement.loc
  if (!loc || loc === 'custom') return null
  const [vert, horiz] = splitLoc(loc)
  if (!vert || !horiz) return null
  const a = placement.anchor
  // 锚点是一个**点**（零尺寸的框）：三种对齐落在同一处
  const ax0 = a ? BOX.x + a[0] * BOX.w : BOX.x
  const ay0 = a ? BOX.y + (1 - a[1]) * BOX.h : BOX.y
  const aw = a ? 0 : BOX.w
  const ah = a ? 0 : BOX.h
  const x =
    horiz === 'left' ? ax0 : horiz === 'right' ? ax0 + aw - CHIP.w : ax0 + (aw - CHIP.w) / 2
  const y = vert === 'upper' ? ay0 : vert === 'lower' ? ay0 + ah - CHIP.h : ay0 + (ah - CHIP.h) / 2
  return { x, y }
}

/**
 * 容器边界 + 图例此刻的落点。**按当前值重画**，没有动画。
 *
 * 落点算不出来（`best` 按数据避让、拖到过自定义位置、多选取值不一致）时
 * 只画容器，不画一个猜的方块——那会是张语义错的精确图。
 */
function Diagram({ placement, label }: { placement: LegendPlacement | null; label: string }) {
  const chip = placement ? chipAt(placement) : null
  return (
    <svg
      width={VIEW.w}
      height={VIEW.h}
      viewBox={`0 0 ${VIEW.w} ${VIEW.h}`}
      role="img"
      aria-label={label}
      className="shrink-0 rounded-sm border border-border bg-surface"
    >
      <rect
        x={BOX.x}
        y={BOX.y}
        width={BOX.w}
        height={BOX.h}
        fill="none"
        stroke="currentColor"
        strokeWidth={1}
        strokeDasharray="2 2"
        className="text-ink-faint"
      />
      {chip && (
        <rect
          x={chip.x}
          y={chip.y}
          width={CHIP.w}
          height={CHIP.h}
          rx={1.5}
          fill="currentColor"
          className="text-accent"
        />
      )}
    </svg>
  )
}

/** 预设按钮上的小图示：容器 + 一个落在对应外侧位的方块 */
function PresetGlyph({ loc, anchor }: { loc: string; anchor: LegendAnchor }) {
  const chip = chipAt({ loc, anchor })
  const s = 0.32 // 与 Diagram 同一套坐标，缩到按钮里
  return (
    <svg
      width={VIEW.w * s}
      height={VIEW.h * s}
      viewBox={`0 0 ${VIEW.w} ${VIEW.h}`}
      aria-hidden
      className="pointer-events-none"
    >
      <rect
        x={BOX.x}
        y={BOX.y}
        width={BOX.w}
        height={BOX.h}
        fill="none"
        stroke="currentColor"
        strokeWidth={2.5}
        className="text-ink-faint"
      />
      {chip && (
        <rect
          x={chip.x}
          y={chip.y}
          width={CHIP.w}
          height={CHIP.h}
          rx={1}
          fill="currentColor"
          className="text-ink-2"
        />
      )}
    </svg>
  )
}

export function LegendPositionPicker({
  value,
  options,
  onChange,
  ariaLabel,
  containerLabel,
  anchor = null,
  anchorSupported = false,
  onPlace,
}: {
  /**
   * 当前 `loc`。**多选取值不一致时传 null**——那时一个格子都不该被标成选中，
   * 也不该把「空」当成一个自定义值塞进选项表。
   */
  value: string | null
  options: string[]
  /** 只改 `loc` 的写入（锚点由 `onPlace` 管；没有外侧带时它就是全部） */
  onChange: (v: string) => void
  ariaLabel: string
  /** 九个档位参照的容器名（宿主子图）；认不出来时不显示 */
  containerLabel?: string
  /** 当前锚点；`null` = 没有锚框（图例在容器内侧）。多选不一致时也传 null */
  anchor?: LegendAnchor | null
  /** 引擎宣称了 `loc_anchor` 这条能力——没有它整个外侧带不出现 */
  anchorSupported?: boolean
  /** 一次写下整个摆法（内 / 外都走它）；没给就退回只写 `loc` */
  onPlace?: (next: LegendPlacement) => void
}) {
  const gridHintId = useId()
  const has = (v: string) => options.includes(v)
  // "right" 是 matplotlib 的历史别名（≈ center right），只有当前值恰好是它时才显示
  const extraChips = ['best', ...(value === 'right' ? ['right'] : [])].filter(has)

  const within = containerLabel
    ? translate('control.legendPositionWithin', { ns: 'inspector', label: containerLabel })
    : null

  const placement: LegendPlacement | null = value === null ? null : { loc: value, anchor }
  const activePreset = placement ? outsidePresetOf(placement) : null
  const outside = anchor !== null
  const place = (next: LegendPlacement) => {
    if (onPlace) onPlace(next)
    else onChange(next.loc)
  }
  /** 图内的一次点击：回到容器内侧（有锚点就一并清掉） */
  const pickInside = (loc: string) => place({ loc, anchor: null })

  return (
    <div className="flex w-full min-w-0 flex-col gap-1.5">
      <div className="flex w-full min-w-0 items-start gap-2">
        <div className="flex shrink-0 flex-col items-center gap-0.5">
          <div
            role="radiogroup"
            aria-label={ariaLabel}
            aria-describedby={within ? gridHintId : undefined}
            className="grid grid-cols-3 gap-px rounded-sm border border-border bg-surface p-1"
          >
            {GRID.flat().map((loc) => {
              if (!has(loc)) return <span key={loc} className="h-5 w-6" aria-hidden />
              // 外侧摆着的时候九宫格里一个都不标选中：那个 loc 此刻说的是
              // 「贴锚点的哪个角」，不是「在容器里的哪一格」
              const active = !outside && value === loc
              return (
                <Tip key={loc} label={optionLabel('loc', loc)}>
                  <button
                    type="button"
                    role="radio"
                    aria-checked={active}
                    aria-label={optionLabel('loc', loc)}
                    onClick={() => pickInside(loc)}
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
                      <span
                        aria-hidden
                        className="h-1.5 w-1.5 rounded-full border border-ink-faint"
                      />
                    )}
                  </button>
                </Tip>
              )
            })}
          </div>
          {within && (
            <span
              id={gridHintId}
              className="max-w-[84px] truncate text-[10px] leading-3 text-ink-3"
              title={within}
            >
              {within}
            </span>
          )}
        </div>
        <div className="flex min-w-0 flex-1 flex-col gap-1">
          {extraChips.map((v) => {
            const active = !outside && value === v
            return (
              <button
                key={v}
                type="button"
                aria-pressed={active}
                onClick={() => pickInside(v)}
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

      {anchorSupported && (
        <div className="flex flex-col gap-1 border-t border-border pt-1.5">
          {/* 一条看得见的小标题：光靠一条分割线，「下面这排是图外」只能靠悬停
              才知道——那不是「看得懂」 */}
          <p className="text-[10px] leading-3 text-ink-3">{ins('control.legendOutsideBand')}</p>
          <div className="flex items-start gap-2">
            <div
              role="radiogroup"
              aria-label={ins('control.legendOutsideAria')}
              className="grid grid-cols-3 gap-1"
            >
              {LEGEND_OUTSIDE_PRESETS.map((p) => {
                const active = activePreset === p.id
                const label = ins(`control.legendOutside.${p.id}`)
                return (
                  <Tip key={p.id} label={label}>
                    <button
                      type="button"
                      role="radio"
                      aria-checked={active}
                      aria-label={label}
                      onClick={() => place({ loc: p.loc, anchor: [...p.anchor] })}
                      className={cn(
                        'flex h-6 w-9 items-center justify-center rounded-sm border outline-none transition-colors',
                        'focus-visible:focus-ring',
                        active
                          ? 'border-accent bg-accent-subtle text-accent'
                          : 'border-border hover:border-border-strong',
                      )}
                    >
                      <PresetGlyph loc={p.loc} anchor={p.anchor} />
                    </button>
                  </Tip>
                )
              })}
            </div>
            <Diagram placement={placement} label={ins('control.legendPreviewAria')} />
          </div>
          {outside && anchor && (
            <>
              <div className="flex items-center gap-1">
                <span className="shrink-0 text-[10px] text-ink-3">
                  {ins('control.legendAnchorLabel')}
                </span>
                <NumberField
                  className="min-w-0 flex-1"
                  ariaLabel={ins('control.legendAnchorX')}
                  value={anchor[0]}
                  step={0.01}
                  precision={2}
                  onChange={(v) => place({ loc: value ?? 'upper left', anchor: [v, anchor[1]] })}
                />
                <NumberField
                  className="min-w-0 flex-1"
                  ariaLabel={ins('control.legendAnchorY')}
                  value={anchor[1]}
                  step={0.01}
                  precision={2}
                  onChange={(v) => place({ loc: value ?? 'upper left', anchor: [anchor[0], v] })}
                />
              </div>
              {/* 外侧图例很容易探出图幅，导出时那一块会被静默裁掉——预检
                  `element-outside-figure` 会把它报出来，这里先说一句 */}
              <p className="text-[11px] leading-snug text-ink-3">
                {ins('control.legendOutsideOverflowHint')}
              </p>
            </>
          )}
        </div>
      )}
    </div>
  )
}
