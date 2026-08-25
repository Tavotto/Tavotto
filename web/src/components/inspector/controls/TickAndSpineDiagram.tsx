import { useState, type KeyboardEvent } from 'react'
import { t as translate } from '@/i18n'
import { cn } from '@/lib/utils'
import { Tip } from '../../ui/Tooltip'

/**
 * 刻度与边框状态图：一张可点击的小坐标轴。
 *
 * 四条边各有两个开关——边框（spine_<side>）画成边线本体，刻度
 * （ticks_<side>）画成边上的三根刻度短线；网格（grid_x / grid_y）是图下方的
 * 两个开关，状态同时预览在图内。点对应的边即切换；每条边可单独聚焦
 * （role="switch" + aria-checked），开关状态用「实线 vs 虚线 + 透明度」表达，
 * 不只靠颜色。
 *
 * 纯展示 + 回调组件：字段在不在、当前值、写入全部由调用方（manifest 与
 * ElementWriter）决定；manifest 没有的部分整块不画。
 */

export interface TickSpineAdapter {
  has: (prop: string) => boolean
  read: (prop: string) => unknown
  /** 一次点击 = 一条历史 + 一次渲染（writeOnce 语义） */
  toggle: (prop: string, next: boolean) => void
  labelOf: (prop: string) => string
  isOverridden: (prop: string) => boolean
  /** 单独恢复一条到脚本值（clearOverride） */
  reset: (prop: string) => void
}

export const TICK_SPINE_PROPS = [
  'ticks_bottom', 'ticks_top', 'ticks_left', 'ticks_right',
  'spine_bottom', 'spine_top', 'spine_left', 'spine_right',
  'grid_x', 'grid_y',
] as const

type Side = 'top' | 'bottom' | 'left' | 'right'
const SIDES: Side[] = ['top', 'right', 'bottom', 'left']

/** 中央坐标框（viewBox 148×104） */
const BOX = { x: 26, y: 14, w: 96, h: 68 }

const spinePath = (side: Side): string => {
  const { x, y, w, h } = BOX
  switch (side) {
    case 'top': return `M${x} ${y} H${x + w}`
    case 'bottom': return `M${x} ${y + h} H${x + w}`
    case 'left': return `M${x} ${y} V${y + h}`
    case 'right': return `M${x + w} ${y} V${y + h}`
  }
}

/** 三根刻度短线，画在边框外侧 */
const tickMarks = (side: Side): string => {
  const { x, y, w, h } = BOX
  const L = 6
  const xs = [x + w * 0.25, x + w * 0.5, x + w * 0.75]
  const ys = [y + h * 0.25, y + h * 0.5, y + h * 0.75]
  switch (side) {
    case 'top': return xs.map((v) => `M${v} ${y} v${-L}`).join(' ')
    case 'bottom': return xs.map((v) => `M${v} ${y + h} v${L}`).join(' ')
    case 'left': return ys.map((v) => `M${x} ${v} h${-L}`).join(' ')
    case 'right': return ys.map((v) => `M${x + w} ${v} h${L}`).join(' ')
  }
}

/** 命中区：比图形宽出一圈，好点 */
const hitRect = (side: Side, kind: 'spine' | 'ticks') => {
  const { x, y, w, h } = BOX
  const t = kind === 'spine' ? 7 : 11
  const off = kind === 'spine' ? -3.5 : 3
  switch (side) {
    case 'top': return { x, y: y + (kind === 'spine' ? off : -t - off), width: w, height: t }
    case 'bottom': return { x, y: y + h + (kind === 'spine' ? off : off), width: w, height: t }
    case 'left': return { x: x + (kind === 'spine' ? off : -t - off), y, width: t, height: h }
    case 'right': return { x: x + w + (kind === 'spine' ? off : off), y, width: t, height: h }
  }
}

const ctl = (key: string, values?: Record<string, unknown>) =>
  translate(`control.${key}`, { ns: 'inspector', ...(values ?? {}) })

function SvgSwitch({
  prop,
  on,
  adapter,
  children,
  hit,
}: {
  prop: string
  on: boolean
  adapter: TickSpineAdapter
  children: React.ReactNode
  hit: { x: number; y: number; width: number; height: number }
}) {
  const [focused, setFocused] = useState(false)
  const name = ctl(on ? 'switchOn' : 'switchOff', { label: adapter.labelOf(prop) })
  const onKey = (e: KeyboardEvent) => {
    if (e.key !== 'Enter' && e.key !== ' ') return
    e.preventDefault()
    adapter.toggle(prop, !on)
  }
  return (
    <Tip label={adapter.isOverridden(prop) ? `${name} · ${translate('element.modified', { ns: 'inspector' })}` : name}>
      <g
        role="switch"
        aria-checked={on}
        aria-label={adapter.labelOf(prop)}
        tabIndex={0}
        className="cursor-pointer outline-none"
        onClick={() => adapter.toggle(prop, !on)}
        onKeyDown={onKey}
        onFocus={() => setFocused(true)}
        onBlur={() => setFocused(false)}
      >
        <rect {...hit} fill="transparent" />
        {focused && (
          <rect
            {...hit}
            fill="none"
            stroke="var(--color-accent)"
            strokeWidth="1"
            rx="2"
          />
        )}
        {children}
      </g>
    </Tip>
  )
}

export function TickAndSpineDiagram({ adapter }: { adapter: TickSpineAdapter }) {
  const sideProps = SIDES.flatMap((s) => [`spine_${s}`, `ticks_${s}`])
  if (!sideProps.some((p) => adapter.has(p))) return null

  const on = (p: string) => adapter.read(p) === true
  const gridX = adapter.has('grid_x') && on('grid_x')
  const gridY = adapter.has('grid_y') && on('grid_y')
  const modified = TICK_SPINE_PROPS.filter((p) => adapter.has(p) && adapter.isOverridden(p))

  return (
    <div className="flex flex-col gap-1">
      <svg
        viewBox="0 0 148 104"
        className="w-full max-w-[220px] self-center text-ink"
        aria-label={ctl('tickSpineDiagram')}
        role="group"
      >
        {/* 网格预览（非交互，开关在下方） */}
        {gridX && (
          <path
            d={`M${BOX.x + BOX.w * 0.25} ${BOX.y} V${BOX.y + BOX.h} M${BOX.x + BOX.w * 0.5} ${BOX.y} V${BOX.y + BOX.h} M${BOX.x + BOX.w * 0.75} ${BOX.y} V${BOX.y + BOX.h}`}
            stroke="currentColor" strokeOpacity="0.18" strokeWidth="0.8" aria-hidden
          />
        )}
        {gridY && (
          <path
            d={`M${BOX.x} ${BOX.y + BOX.h * 0.25} H${BOX.x + BOX.w} M${BOX.x} ${BOX.y + BOX.h * 0.5} H${BOX.x + BOX.w} M${BOX.x} ${BOX.y + BOX.h * 0.75} H${BOX.x + BOX.w}`}
            stroke="currentColor" strokeOpacity="0.18" strokeWidth="0.8" aria-hidden
          />
        )}
        {SIDES.map((side) => (
          <g key={side}>
            {adapter.has(`spine_${side}`) && (
              <SvgSwitch
                prop={`spine_${side}`}
                on={on(`spine_${side}`)}
                adapter={adapter}
                hit={hitRect(side, 'spine')}
              >
                <path
                  d={spinePath(side)}
                  fill="none"
                  stroke="currentColor"
                  strokeWidth={on(`spine_${side}`) ? 2 : 1}
                  strokeOpacity={on(`spine_${side}`) ? 1 : 0.3}
                  strokeDasharray={on(`spine_${side}`) ? undefined : '3 3'}
                />
              </SvgSwitch>
            )}
            {adapter.has(`ticks_${side}`) && (
              <SvgSwitch
                prop={`ticks_${side}`}
                on={on(`ticks_${side}`)}
                adapter={adapter}
                hit={hitRect(side, 'ticks')}
              >
                <path
                  d={tickMarks(side)}
                  fill="none"
                  stroke="currentColor"
                  strokeWidth={on(`ticks_${side}`) ? 1.6 : 1}
                  strokeOpacity={on(`ticks_${side}`) ? 1 : 0.3}
                  strokeDasharray={on(`ticks_${side}`) ? undefined : '1.5 1.5'}
                />
              </SvgSwitch>
            )}
          </g>
        ))}
      </svg>

      {(adapter.has('grid_x') || adapter.has('grid_y')) && (
        <div className="flex gap-1.5">
          {(['grid_x', 'grid_y'] as const).map((p) =>
            adapter.has(p) ? (
              <button
                key={p}
                type="button"
                role="switch"
                aria-checked={on(p)}
                onClick={() => adapter.toggle(p, !on(p))}
                className={cn(
                  'flex h-6 flex-1 items-center justify-center gap-1 rounded-sm border text-xs outline-none transition-colors',
                  'focus-visible:focus-ring',
                  on(p)
                    ? 'border-accent bg-accent-subtle font-medium text-accent'
                    : 'border-border text-ink-2 hover:border-border-strong hover:text-ink',
                )}
              >
                <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden>
                  {p === 'grid_x' ? (
                    <path d="M4 1 V11 M8 1 V11" stroke="currentColor" strokeWidth="1" fill="none" />
                  ) : (
                    <path d="M1 4 H11 M1 8 H11" stroke="currentColor" strokeWidth="1" fill="none" />
                  )}
                </svg>
                {adapter.labelOf(p)}
              </button>
            ) : null,
          )}
        </div>
      )}

      {/* 已修改的边逐条列出，点 × 单独恢复到脚本——折叠进图形的属性也保有
          「一眼可辨来源 + 单项恢复」这两条契约 */}
      {modified.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {modified.map((p) => (
            <button
              key={p}
              type="button"
              onClick={() => adapter.reset(p)}
              aria-label={ctl('resetSide', { label: adapter.labelOf(p) })}
              className={cn(
                'flex h-5 items-center gap-1 rounded-sm bg-accent-subtle px-1.5 text-xs text-accent',
                'outline-none transition-colors hover:bg-accent/15 focus-visible:focus-ring',
              )}
            >
              <span aria-hidden className="h-1 w-1 rounded-full bg-accent" />
              {adapter.labelOf(p)}
              <span aria-hidden>×</span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
